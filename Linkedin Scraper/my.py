import json
import time
import re
import random
import logging
import os
import argparse
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from ratelimit import limits, sleep_and_retry
from backoff import on_exception, expo
from requests.exceptions import RequestException
from urllib3.exceptions import HTTPError
from googlesearch import search as gsearch

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("linkedin_finder.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
MAX_SEARCH_RESULTS = 15
MIN_SEARCH_DELAY = 20
MAX_SEARCH_DELAY = 30
MIN_PERSONA_DELAY = 5
MAX_PERSONA_DELAY = 10
DEFAULT_INPUT_FILE = "final.json"
DEFAULT_OUTPUT_FILE = "linkedin_results.json"
MAX_THREADS = 4  # Be conservative with threading to avoid rate limits

class LinkedInFinder:
    def __init__(self, input_file, output_file, max_results=10, use_threading=False):
        self.input_file = input_file
        self.output_file = output_file
        self.max_results = max_results
        self.use_threading = use_threading
        self.results = []
        self.total_success = 0
        self.total_processed = 0

    def clean_text(self, text):
        """Remove non-alphanumeric characters except spaces"""
        if not text:
            return ""
        # Remove URLs
        text = re.sub(r'http\S+|www\S+|https\S+', '', text)
        # Replace non-alphanumeric with space and normalize whitespace
        cleaned = ' '.join(re.sub(r'[^\w\s]', ' ', text).split())
        return cleaned

    def extract_company_from_email(self, text):
        """Extract company domain from email address"""
        if not text or '@' not in text:
            return ""
            
        try:
            email_pattern = r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b'
            matches = re.findall(email_pattern, text)
            if matches:
                # Take domain without TLD
                return matches[0].split('.')[0]
        except Exception:
            pass
        return ""

    def generate_search_query(self, persona):
        """Generate a good search query from persona data"""
        query_parts = []
        keywords = []

        # Name (most important)
        name = self.clean_text(persona.get("name", "").split("(")[0])
        if name:
            query_parts.append(name)

        # Extract company from intro if there's an email
        intro = persona.get("intro", "")
        company_from_email = self.extract_company_from_email(intro)
        if company_from_email:
            query_parts.append(company_from_email)

        # LinkedIn-optimized keywords from intro
        intro_clean = self.clean_text(intro)
        if intro_clean:
            # Prioritize valuable professional terms
            professional_terms = ['engineer', 'developer', 'manager', 'director', 
                                'architect', 'scientist', 'analyst', 'consultant',
                                'founder', 'ceo', 'cto', 'vp', 'head', 'lead']
            
            for term in professional_terms:
                if re.search(rf'\b{term}\b', intro_clean.lower()):
                    keywords.append(term)
            
            # Add remaining significant words
            keywords.extend([w for w in intro_clean.split() if len(w) > 3 and w.lower() not in keywords][:3])

        # Keywords from persona
        if "keywords" in persona and isinstance(persona["keywords"], list):
            for kw in persona["keywords"][:5]:  # Limit to first 5
                if isinstance(kw, str) and kw:
                    kw_clean = self.clean_text(kw)
                    if kw_clean and kw_clean not in keywords:
                        keywords.append(kw_clean)

        # Add keywords to query parts
        query_parts.extend(keywords)

        # Location from timezone
        timezone = persona.get("timezone", "")
        if timezone:
            try:
                city = timezone.split("/")[-1].replace("_", " ")
                query_parts.append(self.clean_text(city))
            except:
                pass

        # Company industry
        industry = persona.get("company_industry", "")
        if industry:
            query_parts.append(self.clean_text(industry))

        # Deduplicate and join
        seen = set()
        clean_parts = []
        for part in query_parts:
            if part and not (part.lower() in seen or seen.add(part.lower())):
                clean_parts.append(part)
                
        # Return a well-formed search query (limit length)
        return " ".join(clean_parts[:10])

    @sleep_and_retry
    @limits(calls=1, period=MIN_SEARCH_DELAY)
    @on_exception(expo, (RequestException, HTTPError), max_tries=3)
    def safe_google_search(self, query, num_results=10):
        """Perform Google search with rate limiting and error handling"""
        try:
            # Add randomized sleep for additional safety
            delay = random.uniform(MIN_SEARCH_DELAY, MAX_SEARCH_DELAY)
            time.sleep(delay)
            
            logger.info(f"Searching for: {query}")
            results = list(gsearch(query, num_results=num_results, sleep_interval=delay))
            logger.info(f"Found {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Google search failed for '{query}': {e}")
            return []

    def extract_linkedin_urls(self, urls):
        """Extract and validate LinkedIn profile URLs"""
        linkedin_urls = []
        for url in urls:
            # Only include actual LinkedIn profile URLs
            if "linkedin.com/in/" in url:
                # Basic URL cleanup
                clean_url = re.sub(r'\?.*$', '', url)  # Remove query parameters
                if clean_url not in linkedin_urls:
                    linkedin_urls.append(clean_url)
        return linkedin_urls

    def extract_face_linkedin_urls(self, persona):
        """Extract LinkedIn URLs from the face field"""
        linkedin_urls = []
        
        # Check if face is a string (direct URL)
        if isinstance(persona.get("face"), str):
            face_url = persona["face"]
            if "linkedin.com/in/" in face_url:
                linkedin_urls.append(face_url)
                logger.info(f"Found LinkedIn URL in face string: {face_url}")
        
        # Check if face is a dictionary with profiles
        elif isinstance(persona.get("face"), dict) and "profiles" in persona["face"]:
            profiles = persona["face"]["profiles"]
            if isinstance(profiles, list):
                for profile in profiles:
                    if isinstance(profile, dict) and "url" in profile:
                        url = profile["url"]
                        if "linkedin.com/in/" in url and url not in linkedin_urls:
                            linkedin_urls.append(url)
                            logger.info(f"Found LinkedIn URL in face.profiles: {url}")
        
        return linkedin_urls

    def process_persona(self, persona, index, total):
        """Process a single persona"""
        name = persona.get("name", f"Person {index}")
        logger.info(f"[{index}/{total}] Processing: {name}")
        
        # Generate search query
        query = self.generate_search_query(persona)
        google_query = f"{name} linkedin {query}"
        logger.info(f"Query: {google_query}")
        
        # Initialize results for this persona
        persona_result = {
            "name": name,
            "query": query,
            "search_query": google_query,
            "linkedin_urls": [],
            "manual_verify_url": f"https://www.google.com/search?q={quote_plus(google_query)}"
        }
        
        # Get LinkedIn URLs from face field if available
        face_linkedin_urls = self.extract_face_linkedin_urls(persona)
        if face_linkedin_urls:
            persona_result["linkedin_urls"].extend(face_linkedin_urls)
            logger.info(f"Found {len(face_linkedin_urls)} LinkedIn URLs in face field")
        
        # Perform Google search if needed
        if len(persona_result["linkedin_urls"]) < self.max_results:
            try:
                needed_results = self.max_results - len(persona_result["linkedin_urls"])
                search_results = self.safe_google_search(google_query, num_results=needed_results + 5)
                linkedin_urls = self.extract_linkedin_urls(search_results)
                
                # Add new LinkedIn URLs
                for url in linkedin_urls:
                    if url not in persona_result["linkedin_urls"]:
                        persona_result["linkedin_urls"].append(url)
                        
                logger.info(f"Found {len(linkedin_urls)} additional LinkedIn URLs via Google search")
            except Exception as e:
                logger.error(f"Error during search for {name}: {e}")
        
        # Limit URLs to the maximum number
        persona_result["linkedin_urls"] = persona_result["linkedin_urls"][:self.max_results]
        
        # Add summary stats
        persona_result["found_urls_count"] = len(persona_result["linkedin_urls"])
        
        # Return the result
        self.total_processed += 1
        if persona_result["found_urls_count"] > 0:
            self.total_success += 1
            
        return persona_result

    def process_all_personas(self):
        """Process all personas in the input file"""
        try:
            # Load personas from input file
            logger.info(f"Loading personas from {self.input_file}")
            with open(self.input_file, "r", encoding="utf-8") as f:
                personas = json.load(f)
            
            logger.info(f"Loaded {len(personas)} personas")
            
            # Process personas
            if self.use_threading and len(personas) > 1:
                logger.info(f"Processing personas with {min(MAX_THREADS, len(personas))} threads")
                self.process_with_threading(personas)
            else:
                logger.info("Processing personas sequentially")
                self.process_sequentially(personas)
            
            # Save results
            self.save_results()
            
            # Log summary
            logger.info(f"Processing complete. Found LinkedIn URLs for {self.total_success} out of {self.total_processed} personas ({(self.total_success/self.total_processed)*100:.1f}%)")
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing personas: {e}")
            if self.results:
                logger.info("Saving partial results")
                self.save_results()
            return False

    def process_sequentially(self, personas):
        """Process personas one by one"""
        for i, persona in enumerate(personas):
            try:
                result = self.process_persona(persona, i+1, len(personas))
                self.results.append(result)
                
                # Add a random delay between personas
                if i < len(personas) - 1:
                    delay = random.uniform(MIN_PERSONA_DELAY, MAX_PERSONA_DELAY)
                    time.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Error processing persona #{i+1}: {e}")

    def process_with_threading(self, personas):
        """Process personas using multiple threads"""
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            # Submit all persona processing tasks
            future_to_persona = {
                executor.submit(self.process_persona, persona, i+1, len(personas)): i 
                for i, persona in enumerate(personas)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_persona):
                persona_index = future_to_persona[future]
                try:
                    result = future.result()
                    self.results.append(result)
                    logger.info(f"Completed processing persona #{persona_index+1}")
                except Exception as e:
                    logger.error(f"Error processing persona #{persona_index+1}: {e}")

    def save_results(self):
        """Save results to the output file without reordering"""
        try:
            # Use results directly without sorting to maintain input order
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Results saved to {self.output_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return False

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Find LinkedIn profiles for personas")
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT_FILE, help=f"Input JSON file (default: {DEFAULT_INPUT_FILE})")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT_FILE, help=f"Output JSON file (default: {DEFAULT_OUTPUT_FILE})")
    parser.add_argument("-m", "--max-results", type=int, default=5, help="Maximum number of LinkedIn URLs per persona (default: 5)")
    parser.add_argument("-t", "--threading", action="store_true", help="Use threading to process personas in parallel")
    return parser.parse_args()

def main():
    """Main entry point"""
    args = parse_arguments()
    
    logger.info("Starting LinkedIn profile finder")
    logger.info(f"Input file: {args.input}")
    logger.info(f"Output file: {args.output}")
    logger.info(f"Max results per persona: {args.max_results}")
    logger.info(f"Threading enabled: {args.threading}")
    
    finder = LinkedInFinder(
        input_file=args.input,
        output_file=args.output,
        max_results=args.max_results,
        use_threading=args.threading
    )
    
    success = finder.process_all_personas()
    
    if success:
        logger.info("LinkedIn profile finder completed successfully")
        return 0
    else:
        logger.error("LinkedIn profile finder encountered errors")
        return 1

if __name__ == "__main__":
    exit(main())