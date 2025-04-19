const fs = require('fs');
const axios = require('axios');
const { promisify } = require('util');
const path = require('path');

// Configuration with improved settings
const CONFIG = {
  API_KEY: 'your-api-key', // Replace with your key
  API_ENDPOINT: 'https://nubela.co/proxycurl/api/v2/linkedin',
  MAX_RETRIES: 5, // Increased from 3
  INITIAL_RETRY_DELAY: 2000,
  MAX_RETRY_DELAY: 15000, // Maximum backoff delay
  BATCH_SIZE: 2, // Reduced from 3 to decrease load
  BATCH_DELAY: 3000, // Increased from 1500
  INPUT_FILES: {
    PERSONAS: 'final.json',
    LINKEDIN_RESULTS: 'linkedin_results.json'
  },
  OUTPUT_FILES: {
    PROFILES: 'final_cleaned_linkedin_profiles.json',
    FAILED_URLS: 'failed_urls.json',
    DEBUG: 'debug_data.json',
    RETRY_QUEUE: 'retry_queue.json' // New file to store URLs for later retry
  },
  REQUEST_TIMEOUT: 60000, // Increased from 30000
  USER_AGENT: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
  CACHE_DIR: './cache'
};

// Promisify file system operations
const writeFileAsync = promisify(fs.writeFile);
const readFileAsync = promisify(fs.readFile);
const existsAsync = promisify(fs.exists);
const mkdirAsync = promisify(fs.mkdir);

/**
 * API Rate Limiter
 * Manages request rates to avoid hitting API limits
 */
class RateLimiter {
  constructor(options = {}) {
    this.queue = [];
    this.processing = false;
    this.lastRequestTime = 0;
    this.minInterval = options.minInterval || 1000; // Min time between requests
    this.currentDelay = this.minInterval;
    this.consecutiveErrors = 0;
  }

  /**
   * Add a task to the queue
   * @param {Function} task - Function that returns a Promise
   * @returns {Promise} Result of the task
   */
  async schedule(task) {
    return new Promise((resolve, reject) => {
      this.queue.push({ task, resolve, reject });
      this.processQueue();
    });
  }

  /**
   * Process the queue
   */
  async processQueue() {
    if (this.processing || this.queue.length === 0) return;
    
    this.processing = true;
    const now = Date.now();
    const timeToWait = Math.max(0, this.lastRequestTime + this.currentDelay - now);
    
    await this.delay(timeToWait);
    
    const { task, resolve, reject } = this.queue.shift();
    this.lastRequestTime = Date.now();
    
    try {
      const result = await task();
      // Success - reduce delay but not below minimum
      this.consecutiveErrors = 0;
      this.currentDelay = Math.max(this.minInterval, this.currentDelay * 0.8);
      resolve(result);
    } catch (error) {
      // Error - increase delay for backoff
      this.consecutiveErrors++;
      this.currentDelay = Math.min(
        this.currentDelay * 1.5, 
        CONFIG.MAX_RETRY_DELAY
      );
      reject(error);
    } finally {
      this.processing = false;
      this.processQueue();
    }
  }

  /**
   * Delay execution
   * @param {number} ms - Milliseconds to delay
   * @returns {Promise} Delay promise
   */
  delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

/**
 * Cache Manager
 * Implements a disk-based cache for API responses
 */
class CacheManager {
  constructor(cacheDir) {
    this.cacheDir = cacheDir;
    this.initCache();
  }

  async initCache() {
    if (!(await existsAsync(this.cacheDir))) {
      await mkdirAsync(this.cacheDir, { recursive: true });
    }
  }

  getCacheKey(url) {
    // Create a safe filename from the URL
    return Buffer.from(url).toString('base64').replace(/[\/+=]/g, '_');
  }

  async get(url) {
    const cacheKey = this.getCacheKey(url);
    const cachePath = path.join(this.cacheDir, `${cacheKey}.json`);
    
    try {
      if (await existsAsync(cachePath)) {
        const data = await readFileAsync(cachePath, 'utf8');
        const { timestamp, response } = JSON.parse(data);
        
        // Cache valid for 7 days
        if (Date.now() - timestamp < 7 * 24 * 60 * 60 * 1000) {
          return response;
        }
      }
    } catch (error) {
      console.warn(`Cache read error for ${url}: ${error.message}`);
    }
    
    return null;
  }

  async set(url, response) {
    const cacheKey = this.getCacheKey(url);
    const cachePath = path.join(this.cacheDir, `${cacheKey}.json`);
    
    try {
      const data = JSON.stringify({
        timestamp: Date.now(),
        response
      });
      await writeFileAsync(cachePath, data);
      return true;
    } catch (error) {
      console.warn(`Cache write error for ${url}: ${error.message}`);
      return false;
    }
  }
}

/**
 * LinkedIn Profile Collector class
 * Handles fetching and processing LinkedIn profiles
 */
class LinkedInProfileCollector {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.headers = {
      'Authorization': `Bearer ${apiKey}`,
      'Accept': 'application/json',
      'User-Agent': CONFIG.USER_AGENT
    };
    this.requestCount = 0;
    this.failedUrls = [];
    this.retryQueue = [];
    this.successCount = 0;
    this.debugData = [];
    this.rateLimiter = new RateLimiter();
    this.cache = new CacheManager(CONFIG.CACHE_DIR);
  }

  /**
   * Collect LinkedIn profiles for the provided URLs
   * @param {Array} profileUrls - Array of profile URL objects
   * @returns {Object} Collection results
   */
  async collectProfiles(profileUrls) {
    console.log(`\nStarting collection for ${profileUrls.length} profiles`);
    
    const profiles = [];
    const totalBatches = Math.ceil(profileUrls.length / CONFIG.BATCH_SIZE);
    
    // Process in batches
    for (let i = 0; i < profileUrls.length; i += CONFIG.BATCH_SIZE) {
      const batch = profileUrls.slice(i, i + CONFIG.BATCH_SIZE);
      const batchNumber = Math.floor(i / CONFIG.BATCH_SIZE) + 1;
      
      console.log(`\nProcessing batch ${batchNumber}/${totalBatches}`);
      console.log(`Batch URLs: ${batch.map(b => b.url).join(', ')}`);
      
      const batchResults = await this.processBatch(batch);
      profiles.push(...batchResults.filter(Boolean));
      
      // Add reasonable delay between batches
      if (i + CONFIG.BATCH_SIZE < profileUrls.length) {
        console.log(`Waiting ${CONFIG.BATCH_DELAY}ms before next batch...`);
        await this.delay(CONFIG.BATCH_DELAY);
      }
    }
    
    // Handle retry queue if needed
    if (this.retryQueue.length > 0) {
      console.log(`\nProcessing ${this.retryQueue.length} URLs from retry queue with extra delay...`);
      await this.saveRetryQueue();
      
      // Wait extra time before processing retry queue
      await this.delay(CONFIG.MAX_RETRY_DELAY);
      
      for (const urlObj of this.retryQueue) {
        try {
          console.log(`Retrying queued URL: ${urlObj.url}`);
          const profile = await this.fetchProfileWithRetry(urlObj.url, CONFIG.MAX_RETRIES);
          if (profile) {
            this.successCount++;
            console.log(`✓ Success on retry: ${urlObj.url}`);
            profiles.push(profile);
          }
        } catch (error) {
          console.error(`✗ Failed on retry: ${urlObj.url} - ${error.message}`);
          this.failedUrls.push(urlObj.url);
        }
        
        // Add delay between retry attempts
        await this.delay(CONFIG.INITIAL_RETRY_DELAY * 2);
      }
    }
    
    await this.saveFailedUrls();
    await this.saveDebugData();
    return this.formatResults(profiles);
  }

  /**
   * Process a batch of profile URLs
   * @param {Array} batch - Batch of profile URL objects
   * @returns {Array} Batch results
   */
  async processBatch(batch) {
    // Process URLs sequentially to reduce API pressure
    const results = [];
    
    for (const urlObj of batch) {
      try {
        // First check cache
        const cachedProfile = await this.cache.get(urlObj.url);
        if (cachedProfile) {
          console.log(`✓ Using cached data for: ${urlObj.url}`);
          this.successCount++;
          results.push(this.processProfile(cachedProfile, urlObj.url));
          continue;
        }
        
        const profile = await this.fetchProfileWithRetry(urlObj.url, CONFIG.MAX_RETRIES);
        if (profile) {
          this.successCount++;
          console.log(`✓ Success: ${urlObj.url}`);
          results.push(profile);
        }
      } catch (error) {
        console.error(`✗ Failed: ${urlObj.url} - ${error.message}`);
        
        // Add to retry queue instead of immediately failing
        if (error.message.includes('timeout') || 
            error.message.includes('429') || 
            error.message.includes('500')) {
          console.log(`Adding to retry queue: ${urlObj.url}`);
          this.retryQueue.push(urlObj);
        } else {
          this.failedUrls.push(urlObj.url);
        }
      }
    }
    
    return results;
  }

  /**
   * Fetch profile with retry logic and exponential backoff
   * @param {string} url - LinkedIn profile URL
   * @param {number} retriesLeft - Number of retries left
   * @returns {Object} Profile data
   */
  async fetchProfileWithRetry(url, retriesLeft) {
    try {
      const profile = await this.fetchProfile(url);
      this.requestCount++;
      return profile;
    } catch (error) {
      if (retriesLeft > 0) {
        // Calculate exponential backoff delay
        const retryAttempt = CONFIG.MAX_RETRIES - retriesLeft + 1;
        const delay = Math.min(
          CONFIG.INITIAL_RETRY_DELAY * Math.pow(1.5, retryAttempt),
          CONFIG.MAX_RETRY_DELAY
        );
        
        console.log(`Retrying ${url} (${retryAttempt}/${CONFIG.MAX_RETRIES}) after ${delay}ms delay`);
        await this.delay(delay);
        return this.fetchProfileWithRetry(url, retriesLeft - 1);
      }
      throw error;
    }
  }

  /**
   * Fetch LinkedIn profile data with rate limiting
   * @param {string} url - LinkedIn profile URL
   * @returns {Object} Profile data
   */
  async fetchProfile(url) {
    const params = {
      url: url,
      fallback_to_cache: 'on-error',
      use_cache: 'if-present',
      skills: 'include',
      inferred_salary: 'include',
      personal_email: 'include',
      personal_contact_number: 'include'
    };
    
    return this.rateLimiter.schedule(async () => {
      try {
        console.log(`Fetching profile: ${url}`);
        const response = await axios.get(CONFIG.API_ENDPOINT, {
          headers: this.headers,
          params: params,
          timeout: CONFIG.REQUEST_TIMEOUT
        });
        
        // Save raw response data for debugging
        this.debugData.push({
          url: url,
          timestamp: new Date().toISOString(),
          status: response.status,
          headers: response.headers,
          rawData: response.data
        });
        
        if (!response.data) {
          throw new Error('Empty response data received');
        }
        
        // Cache the successful response
        await this.cache.set(url, response.data);
        
        // Validate minimum required fields
        if (!response.data.full_name) {
          console.warn(`⚠️ Warning: Profile missing full_name: ${url}`);
        }
        
        return this.processProfile(response.data, url);
      } catch (error) {
        throw this.handleApiError(error);
      }
    });
  }

  /**
   * Handle API errors with detailed logging
   * @param {Error} error - Error object
   * @returns {Error} Formatted error
   */
  handleApiError(error) {
    if (error.response) {
      const status = error.response.status;
      const message = error.response.data ? JSON.stringify(error.response.data) : 'Unknown error';
      
      // Save error details for debugging
      this.debugData.push({
        timestamp: new Date().toISOString(),
        error: 'API Error',
        status,
        message,
        headers: error.response.headers
      });
      
      // Handle specific status codes
      if (status === 429) {
        // Extract retry-after header if available
        const retryAfter = error.response.headers['retry-after'];
        const retryMs = retryAfter ? parseInt(retryAfter) * 1000 : CONFIG.MAX_RETRY_DELAY;
        return new Error(`Rate limit exceeded (429): ${message}. Retry after ${retryMs}ms`);
      } else if (status >= 500) {
        return new Error(`Server error (${status}): ${message}`);
      } else if (status === 404) {
        return new Error(`Profile not found (404): ${message}`);
      }
      
      return new Error(`API Error ${status}: ${message}`);
    }
    
    if (error.request) {
      // Request was made but no response received
      const errorMsg = error.code === 'ECONNABORTED' 
        ? `Request timeout after ${CONFIG.REQUEST_TIMEOUT}ms` 
        : 'No response received (connection error)';
      
      this.debugData.push({
        timestamp: new Date().toISOString(),
        error: 'Request Error',
        code: error.code,
        message: errorMsg
      });
      
      return new Error(errorMsg);
    }
    
    // Something else caused the error
    this.debugData.push({
      timestamp: new Date().toISOString(),
      error: 'General Error',
      message: error.message
    });
    
    return new Error(`Request Error: ${error.message}`);
  }

  /**
   * Process raw profile data with improved error handling
   * @param {Object} rawData - Raw profile data
   * @param {string} url - LinkedIn profile URL
   * @returns {Object} Processed profile
   */
  processProfile(rawData, url) {
    const ensureValue = (value, defaultValue = null) => 
      value !== undefined ? value : defaultValue;
      
    const processArray = (arr, mapper) => {
      if (!Array.isArray(arr) || arr.length === 0) {
        return [];
      }
      return arr.map(item => {
        try {
          return mapper(item);
        } catch (error) {
          console.warn(`⚠️ Warning: Error processing array item: ${error.message}`);
          return null;
        }
      }).filter(Boolean);
    };
    
    try {
      // Extract key information with proper defaults
      return {
        url: url,
        profile_data: {
          name: ensureValue(rawData.full_name),
          headline: ensureValue(rawData.occupation),
          about: ensureValue(rawData.summary),
          avatar: ensureValue(rawData.profile_pic_url),
          location: ensureValue(rawData.location),
          experiences: processArray(rawData.experiences, exp => ({
            title: ensureValue(exp.title),
            company: ensureValue(exp.company),
            date_range: this.formatDateRange(exp.starts_at, exp.ends_at),
            location: ensureValue(exp.location),
            description: ensureValue(exp.description),
            employment_type: ensureValue(exp.employment_type),
            current: !exp.ends_at
          })),
          education: processArray(rawData.education, edu => ({
            school: ensureValue(edu.school),
            degree: ensureValue(edu.degree_name),
            field: ensureValue(edu.field_of_study),
            dates: this.formatDateRange(edu.starts_at, edu.ends_at) || ensureValue(edu.dates)
          })),
          skills: processArray(rawData.skills, skill => ensureValue(skill.name)),
          languages: processArray(rawData.languages, lang => ensureValue(lang.name)),
          projects: processArray(rawData.projects, proj => ({
            title: ensureValue(proj.title),
            description: ensureValue(proj.description),
            date_range: this.formatDateRange(proj.starts_at, proj.ends_at)
          })),
          certifications: processArray(rawData.certifications, cert => ({
            name: ensureValue(cert.name),
            authority: ensureValue(cert.authority),
            date: this.formatDate(cert.starts_at)
          })),
          volunteer_experience: processArray(rawData.volunteer_work, vol => ({
            role: ensureValue(vol.role),
            cause: ensureValue(vol.cause),
            company: ensureValue(vol.company),
            date_range: this.formatDateRange(vol.starts_at, vol.ends_at)
          })),
          current_company: rawData.experiences && rawData.experiences[0]?.company ? { 
            name: ensureValue(rawData.experiences[0].company) 
          } : null
        }
      };
    } catch (error) {
      console.error(`❌ Error processing profile data for ${url}: ${error.message}`);
      
      // Provide minimal profile data to ensure it gets included
      return {
        url: url,
        profile_data: {
          name: rawData.full_name || "Unknown",
          _error: `Processing error: ${error.message}`,
          _raw_data_available: true // Flag to indicate raw data is available for debugging
        }
      };
    }
  }

  /**
   * Format date range with better handling
   * @param {Object} start - Start date
   * @param {Object} end - End date
   * @returns {string} Formatted date range
   */
  formatDateRange(start, end) {
    try {
      if (!start && !end) return '';
      
      const formatDate = (date) => {
        if (!date) return null;
        
        const month = date.month ? `${date.month}/` : '';
        const year = date.year || '';
        return month + year;
      };
      
      const startStr = formatDate(start);
      const endStr = end ? formatDate(end) : 'Present';
      
      if (startStr) {
        return `${startStr} - ${endStr}`;
      } else if (endStr && endStr !== 'Present') {
        return `Until ${endStr}`;
      }
      
      return '';
    } catch (error) {
      return '';
    }
  }

  /**
   * Format single date
   * @param {Object} date - Date object
   * @returns {string} Formatted date
   */
  formatDate(date) {
    try {
      if (!date) return '';
      
      const month = date.month || '';
      const year = date.year || '';
      
      if (month && year) {
        return `${month}/${year}`;
      }
      return year || '';
    } catch (error) {
      return '';
    }
  }

  /**
   * Format collection results
   * @param {Array} profiles - Collected profiles
   * @returns {Object} Formatted results
   */
  formatResults(profiles) {
    return {
      successCount: this.successCount,
      failedCount: this.failedUrls.length,
      requestCount: this.requestCount,
      profiles: profiles,
      failedUrls: this.failedUrls
    };
  }

  /**
   * Save failed URLs to file
   */
  async saveFailedUrls() {
    if (this.failedUrls.length > 0) {
      try {
        await writeFileAsync(CONFIG.OUTPUT_FILES.FAILED_URLS, JSON.stringify(this.failedUrls, null, 2));
        console.log(`Failed URLs saved to ${CONFIG.OUTPUT_FILES.FAILED_URLS}`);
      } catch (error) {
        console.error(`Error saving failed URLs: ${error.message}`);
      }
    }
  }
  
  /**
   * Save retry queue to file
   */
  async saveRetryQueue() {
    if (this.retryQueue.length > 0) {
      try {
        await writeFileAsync(CONFIG.OUTPUT_FILES.RETRY_QUEUE, JSON.stringify(this.retryQueue, null, 2));
        console.log(`Retry queue saved to ${CONFIG.OUTPUT_FILES.RETRY_QUEUE}`);
      } catch (error) {
        console.error(`Error saving retry queue: ${error.message}`);
      }
    }
  }
  
  /**
   * Save debug data to file
   */
  async saveDebugData() {
    try {
      const debugDir = path.dirname(CONFIG.OUTPUT_FILES.DEBUG);
      if (!(await existsAsync(debugDir))) {
        await mkdirAsync(debugDir, { recursive: true });
      }
      await writeFileAsync(CONFIG.OUTPUT_FILES.DEBUG, JSON.stringify(this.debugData, null, 2));
      console.log(`Debug data saved to ${CONFIG.OUTPUT_FILES.DEBUG}`);
    } catch (error) {
      console.error(`Error saving debug data: ${error.message}`);
    }
  }

  /**
   * Delay execution
   * @param {number} ms - Milliseconds to delay
   * @returns {Promise} Delay promise
   */
  delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

/**
 * File operations helper
 */
class FileHelper {
  /**
   * Load input files with better error handling
   * @returns {Object} Loaded data
   */
  static async loadInputFiles() {
    try {
      // Load personas file
      let personas = [];
      try {
        personas = JSON.parse(await readFileAsync(CONFIG.INPUT_FILES.PERSONAS, 'utf8'));
      } catch (error) {
        console.error(`Error loading personas file: ${error.message}`);
        throw new Error(`Cannot load personas file (${CONFIG.INPUT_FILES.PERSONAS}). Check if the file exists and is valid JSON.`);
      }
      
      // Load LinkedIn results file
      let linkedinResults = [];
      try {
        linkedinResults = JSON.parse(await readFileAsync(CONFIG.INPUT_FILES.LINKEDIN_RESULTS, 'utf8'));
      } catch (error) {
        console.error(`Error loading LinkedIn results file: ${error.message}`);
        throw new Error(`Cannot load LinkedIn results file (${CONFIG.INPUT_FILES.LINKEDIN_RESULTS}). Check if the file exists and is valid JSON.`);
      }
      
      return { personas, linkedinResults };
    } catch (error) {
      throw new Error(`Error loading input files: ${error.message}`);
    }
  }

  /**
   * Save output file with enhanced error handling
   * @param {string} filename - Output filename
   * @param {Object} data - Data to save
   * @returns {Promise<boolean>} Success status
   */
  static async saveOutputFile(filename, data) {
    try {
      const outputPath = path.resolve(filename);
      const outputDir = path.dirname(outputPath);
      
      // Create directory if it doesn't exist
      if (!(await existsAsync(outputDir))) {
        await mkdirAsync(outputDir, { recursive: true });
      }
      
      // Create backup of existing file if exists
      if (await existsAsync(outputPath)) {
        const backupPath = `${outputPath}.backup.${Date.now()}.json`;
        try {
          await fs.promises.copyFile(outputPath, backupPath);
          console.log(`Created backup of existing file: ${backupPath}`);
        } catch (backupError) {
          console.warn(`Could not create backup: ${backupError.message}`);
        }
      }
      
      // Save file
      await writeFileAsync(outputPath, JSON.stringify(data, null, 2));
      console.log(`Output saved to: ${outputPath}`);
      return true;
    } catch (error) {
      console.error(`Error saving output file: ${error.message}`);
      return false;
    }
  }
}

/**
 * Data processor for LinkedIn profiles
 */
class DataProcessor {
  /**
   * Prepare URL mapping with improved validation
   * @param {Array} personas - Persona data
   * @param {Array} linkedinResults - LinkedIn results
   * @returns {Object} URL mapping
   */
  static prepareUrlMapping(personas, linkedinResults) {
    const urlToPerson = {};
    const profileUrlList = [];
    const nameOrder = linkedinResults.map(entry => entry.name);
    const skippedEntries = [];
  
    linkedinResults.forEach(entry => {
      // Validate entry has a name
      if (!entry.name) {
        console.warn('⚠️ Warning: Entry missing name, skipping');
        skippedEntries.push(entry);
        return;
      }
      
      const persona = personas.find(p => p.name === entry.name);
      if (persona) {
        // Clean and validate URLs
        const urls = (entry.linkedin_urls || [])
          .filter(url => !!url) // Filter out null/empty URLs
          .map(url => this.normalizeLinkedInUrl(url)); // Normalize URL format
        
        if (urls.length === 0) {
          console.warn(`⚠️ Warning: No valid LinkedIn URLs found for ${entry.name}`);
        }
        
        urls.forEach(url => {
          if (!url) {
            console.warn(`⚠️ Warning: Invalid URL found for ${entry.name}`);
            return;
          }
          
          urlToPerson[url] = { name: entry.name, persona };
          profileUrlList.push({ url });
        });
      } else {
        console.warn(`⚠️ Warning: No matching persona found for ${entry.name}`);
      }
    });
    
    if (skippedEntries.length > 0) {
      console.warn(`⚠️ Warning: Skipped ${skippedEntries.length} entries due to missing name`);
    }
    
    console.log(`Prepared ${profileUrlList.length} URLs for collection`);
    return { urlToPerson, profileUrlList, nameOrder };
  }
  
  /**
   * Normalize LinkedIn URL format
   * @param {string} url - LinkedIn profile URL
   * @returns {string} Normalized URL
   */
  static normalizeLinkedInUrl(url) {
    if (!url) return null;
    
    try {
      // Ensure URL has protocol
      if (!url.startsWith('http')) {
        url = 'https://' + url;
      }
      
      // Parse and normalize URL
      const urlObj = new URL(url);
      
      // Validate it's a LinkedIn URL
      if (!urlObj.hostname.includes('linkedin.com')) {
        console.warn(`⚠️ Warning: URL is not a LinkedIn URL: ${url}`);
        return url; // Return as-is since we can't normalize
      }
      
      // Remove tracking parameters
      urlObj.search = '';
      
      return urlObj.toString().replace(/\/$/, ''); // Remove trailing slash
    } catch (error) {
      console.warn(`⚠️ Warning: Invalid URL format: ${url}`);
      return url; // Return as-is if we can't parse it
    }
  }

  /**
   * Group profiles by persona with improved error handling
   * @param {Array} profiles - Collected profiles
   * @param {Object} urlToPerson - URL to person mapping
   * @param {Array} nameOrder - Name order array
   * @returns {Array} Grouped profiles
   */
  static groupResults(profiles, urlToPerson, nameOrder) {
    // Create a mapping for faster lookups
    const grouped = nameOrder.reduce((acc, name) => {
      acc[name] = {
        name,
        persona: {},
        linkedin_profiles: []
      };
      return acc;
    }, {});
  
    let unmappedProfiles = 0;
    let invalidProfiles = 0;
    
    // Populate the mapping
    profiles.forEach(profile => {
      if (!profile) {
        invalidProfiles++;
        return;
      }
      
      if (!profile.url) {
        console.warn('⚠️ Warning: Profile missing URL');
        invalidProfiles++;
        return;
      }
      
      const personInfo = urlToPerson[profile.url];
      if (personInfo) {
        grouped[personInfo.name].persona = personInfo.persona;
        grouped[personInfo.name].linkedin_profiles.push(profile);
      } else {
        unmappedProfiles++;
        console.warn(`⚠️ Warning: No person mapping found for URL: ${profile.url}`);
      }
    });
    
    if (invalidProfiles > 0) {
      console.warn(`⚠️ Warning: ${invalidProfiles} invalid profile objects found`);
    }
    
    if (unmappedProfiles > 0) {
      console.warn(`⚠️ Warning: ${unmappedProfiles} profiles could not be mapped to a persona`);
    }
    
    // Convert back to array in the original order
    const result = Object.values(grouped);
    return result;
  }
}

/**
 * Main execution function
 */
async function main() {
    try {
        // Load input files
        const { personas, linkedinResults } = await FileHelper.loadInputFiles();
        console.log(`Loaded ${personas.length} personas and ${linkedinResults.length} LinkedIn results`);

        // Prepare URL mapping
        const { urlToPerson, profileUrlList, nameOrder } = DataProcessor.prepareUrlMapping(personas, linkedinResults);

        // Initialize collector
        const collector = new LinkedInProfileCollector(CONFIG.API_KEY);

        // Collect profiles
        console.log('\nStarting LinkedIn profile collection...');
        const results = await collector.collectProfiles(profileUrlList);

        // Group and process results
        const processedResults = DataProcessor.groupResults(results.profiles, urlToPerson, nameOrder);

        // Save final results
        await FileHelper.saveOutputFile(CONFIG.OUTPUT_FILES.PROFILES, processedResults);

        console.log('\nCollection completed:');
        console.log(`✓ Successful requests: ${results.successCount}`);
        console.log(`✗ Failed requests: ${results.failedCount}`);
        console.log(`Total requests made: ${results.requestCount}`);
    } catch (error) {
        console.error(`Fatal error: ${error.message}`);
        process.exit(1);
    }
}

// Run the program
main();
// Run the program
main().catch(error => {
    console.error(`Unhandled error in main execution: ${error.message}`);
    console.error(error.stack);
    process.exit(1);
  });