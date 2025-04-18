import openai
import json
import time
import logging
import os
import re
from typing import Dict, List, Any, Optional
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from urllib.parse import urlparse
from webdriver_manager.chrome import ChromeDriverManager

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("persona_scraper.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your api key")
PERSONAS_FILE = "enhanced_personas.json"
OUTPUT_FILE = "enriched_personas.json"
FINAL_OUTPUT_FILE = "final.json"  # New final output file
MAX_CONTENT_LENGTH = 4000
SCRAPE_TIMEOUT = 10
MODEL = "gpt-4"

class PersonaScraper:
    def __init__(self, input_file: str, output_file: str, final_file: str):
        self.input_file = input_file
        self.output_file = output_file
        self.final_file = final_file
        self.driver = None
        openai.api_key = OPENAI_API_KEY
        
    def init_driver(self) -> None:
        """Initialize the Selenium WebDriver with optimized settings."""
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-infobars")
            options.add_argument("--mute-audio")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36")
            
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(SCRAPE_TIMEOUT)
            logger.info("WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {str(e)}")
            raise

    def is_valid_url(self, url: str) -> bool:
        """Validate URL format."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
        except:
            return False

    def scrape_page_content(self, url: str) -> str:
        """Scrape content from a given URL with improved error handling and content extraction."""
        if not self.is_valid_url(url):
            logger.warning(f"Invalid URL format: {url}")
            return ""
        
        try:
            logger.info(f"Scraping: {url}")
            self.driver.get(url)
            
            try:
                WebDriverWait(self.driver, SCRAPE_TIMEOUT).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                logger.warning(f"Timeout waiting for page to load: {url}")
            
            if any(err in self.driver.title.lower() for err in ["error", "not found", "forbidden", "403", "404", "500"]):
                logger.warning(f"Error page detected: {url} - Title: {self.driver.title}")
                return ""
            
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            
            for element in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript", "aside"]):
                element.decompose()
            
            main_content = soup.find("main") or soup.find(id=lambda x: x and any(term in str(x).lower() for term in ["content", "main", "article"])) or soup.find("article")
            
            if main_content:
                text = main_content.get_text(separator=' ', strip=True)
            else:
                text = soup.get_text(separator=' ', strip=True)
            
            text = ' '.join(text.split())
            
            if not text.strip():
                logger.warning(f"No text content extracted from {url}")
                return ""
                
            logger.info(f"Successfully scraped {url} - {len(text)} characters")
            return text[:MAX_CONTENT_LENGTH]
            
        except WebDriverException as e:
            logger.error(f"WebDriver error while scraping {url}: {str(e)}")
            return ""
        except Exception as e:
            logger.error(f"Failed to scrape {url}: {str(e)}")
            return ""

    def generate_profile_summary(self, scraped_text: str, source_url: str) -> Dict[str, Any]:
        """Generate a structured profile summary from scraped text using OpenAI."""
        if not scraped_text.strip():
            logger.warning(f"No content to process for {source_url}")
            return self._get_empty_profile(source_url)
        
        prompt = f"""
        You are a professional data analyst extracting structured information about a person from web content.
        
        Analyze the following content from {source_url} and extract key professional details about the individual.
        Return your analysis as valid JSON with no additional text.
        
        Content:
        \"\"\"
        {scraped_text}
        \"\"\"
        
        Extract the following information in this exact JSON format:
        {{
          "user_profile": {{
            "name": "Full name of the person (leave empty if not found)",
            "title": "Professional title or role (leave empty if not found)",
            "bio": "Short professional bio/description (leave empty if not found)",
            "location": "Geographic location (leave empty if not found)",
            "contact_info": "Any public contact information (leave empty if not found)",
            "found_profile": true if this appears to be a personal profile page, false otherwise
          }},
          "summary": "2-3 sentence professional summary of this person based on available information",
          "expertise": ["List", "of", "professional", "skills", "or", "areas", "of", "expertise"],
          "keywords": ["Relevant", "professional", "keywords", "max_8_words"],
          "source": "{source_url}"
        }}
        
        If you can't find personal profile information, set "found_profile" to false and provide minimal information.
        """
        
        try:
            response = openai.ChatCompletion.create(
                model=MODEL,
                messages=[{"role": "system", "content": "You extract structured profile data from text. Respond only with valid JSON."},
                          {"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800
            )
            
            content = response.choices[0].message.content
            
            try:
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                
                result = json.loads(content)
                result["source"] = source_url
                logger.info(f"Successfully extracted profile data from {source_url}")
                return result
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from OpenAI response: {e}")
                logger.debug(f"Problematic response: {content[:200]}...")
                return self._get_empty_profile(source_url)
                
        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            return self._get_empty_profile(source_url)

    def _get_empty_profile(self, source_url: str) -> Dict[str, Any]:
        """Return an empty profile structure."""
        return {
            "user_profile": {
                "name": "",
                "title": "",
                "bio": "",
                "location": "",
                "contact_info": "",
                "found_profile": False
            },
            "summary": "Insufficient information available.",
            "expertise": [],
            "keywords": [],
            "source": source_url
        }
    
    def _process_intro(self, intro: str) -> str:
        """Process intro to ensure it's not more than 6 words.
        If it is, convert it to a comma-separated list of keywords."""
        if not intro:
            return ""
            
        words = intro.split()
        if len(words) <= 6:
            return intro
            
        # If intro is too long, use OpenAI to extract keywords
        try:
            prompt = f"""
            Extract the key professional terms and technical skills from this bio as a comma-separated list.
            Focus on technical skills, tools, languages, and professional interests.
            Return ONLY the comma-separated list with no other text, explanations, or quotation marks.
            Bio: "{intro}"
            """
            
            response = openai.ChatCompletion.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=100
            )
            
            extracted_keywords = response.choices[0].message.content.strip()
            
            # Remove any quotes or extra formatting
            extracted_keywords = extracted_keywords.replace('"', '').replace("'", "")
            if extracted_keywords.startswith("- "):
                extracted_keywords = extracted_keywords[2:]
                
            # If OpenAI returned too many words, take just the most important ones
            keywords_list = [k.strip() for k in extracted_keywords.split(',')]
            if len(keywords_list) > 10:  # Limit to 10 most important keywords
                keywords_list = keywords_list[:10]
                
            return ", ".join(keywords_list)
            
        except Exception as e:
            logger.error(f"Error extracting keywords from intro: {e}")
            # Fallback: just take the first 6 words
            return " ".join(words[:6])
    
    def extract_unique_keywords(self, persona_data: Dict[str, Any], max_keywords: int = 8) -> List[str]:
        """Extract unique keywords not already present in the persona intro.
        Combines keywords from profile data and selects the most relevant ones."""
        if not persona_data:
            return []
            
        # Get existing intro words to avoid duplication
        intro = persona_data.get("intro", "").lower()
        intro_words = set(re.findall(r'\b\w+\b', intro))
        
        # Collect potential keywords from various sources
        all_keywords = []
        
        # From expertise list
        expertise = persona_data.get("expertise", [])
        if expertise and isinstance(expertise, list):
            all_keywords.extend(expertise)
            
        # From keywords list if present
        existing_keywords = persona_data.get("keywords", [])
        if existing_keywords and isinstance(existing_keywords, list):
            all_keywords.extend(existing_keywords)
            
        # From summary
        summary = persona_data.get("summary", "")
        if summary:
            # Use OpenAI to extract keywords from summary
            try:
                prompt = f"""
                Extract the key professional terms and technical skills from this text as a list of single words or short phrases.
                Focus on items NOT mentioned in this list: {', '.join(intro_words)}
                Text: "{summary}"
                Return ONLY a comma-separated list with no other text, explanations, or quotation marks.
                """
                
                response = openai.ChatCompletion.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=100
                )
                
                summary_keywords = response.choices[0].message.content.strip()
                summary_keywords = summary_keywords.replace('"', '').replace("'", "")
                all_keywords.extend([k.strip() for k in summary_keywords.split(',')])
                
            except Exception as e:
                logger.error(f"Error extracting keywords from summary: {e}")
        
        # Filter out keywords already in intro
        filtered_keywords = []
        for keyword in all_keywords:
            # Skip empty keywords
            if not keyword or not keyword.strip():
                continue
                
            # Check if this keyword is already in intro
            keyword_lower = keyword.lower()
            if not any(word in keyword_lower for word in intro_words):
                filtered_keywords.append(keyword.strip())
        
        # Remove duplicates while preserving order
        unique_keywords = []
        seen = set()
        for keyword in filtered_keywords:
            if keyword.lower() not in seen:
                unique_keywords.append(keyword)
                seen.add(keyword.lower())
        
        # Limit to specified max number of keywords
        return unique_keywords[:max_keywords]

    def merge_persona_results(self, persona: Dict[str, Any], results: List[Dict]) -> Dict[str, Any]:
        """Merge multiple results for a single persona and format according to requirements."""
        persona_name = persona.get("name", "Unknown")
        
        if not results:
            # Return the original persona data with default values
            return {
                "name": persona_name,
                "image": persona.get("image", ""),
                "intro": persona.get("intro", ""),
                "timezone": persona.get("timezone", ""),
                "company_industry": persona.get("company_industry"),
                "company_size": persona.get("company_size"),
                "social_profile": persona.get("social_profile", []),
                "keywords": [],
                # Preserve the face field if it exists
                "face": persona.get("face", None)
            }
        
        # Find the most complete profile
        valid_profiles = [r for r in results if r.get("user_profile", {}).get("found_profile", False)]
        
        if not valid_profiles:
            base_profile = results[0].copy() if results else {}
        else:
            # Use the profile with the most complete information
            base_profile = max(
                valid_profiles, 
                key=lambda x: sum(1 for v in x.get("user_profile", {}).values() if v and v is not False)
            ).copy()
        
        # Extract user profile info
        user_profile = base_profile.get("user_profile", {})
        
        # Compile social profile links
        sources = persona.get("social_profile", []).copy()  # Start with existing social profiles
        for result in results:
            source = result.get("source")
            if source and source not in sources:
                sources.append(source)
        
        # Create enriched persona with merged data
        merged_persona = {
            "name": user_profile.get("name", "") or persona_name,
            "image": persona.get("image", ""),
            "intro": user_profile.get("bio", "") or persona.get("intro", ""),
            "timezone": persona.get("timezone", ""),
            "company_industry": persona.get("company_industry"),
            "company_size": persona.get("company_size"),
            "social_profile": sources,
            "keywords": []
        }
        
        # Preserve the face field if it exists in the original persona
        if "face" in persona:
            merged_persona["face"] = persona["face"]
        
        # Process intro if it's too long (more than 6 words)
        merged_persona["intro"] = self._process_intro(merged_persona["intro"])
        
        # Extract unique keywords
        merged_persona["keywords"] = self.extract_unique_keywords(base_profile)
            
        return merged_persona

    def _clean_url(self, url: str) -> str:
        """Attempt to fix URLs that may be missing http/https prefix."""
        if url and isinstance(url, str):
            if not (url.startswith('http://') or url.startswith('https://')):
                return f'https://{url}'
        return url

    def process_personas(self) -> List[Dict[str, Any]]:
        """Process all personas from the input file."""
        try:
            with open(self.input_file, "r", encoding="utf-8") as f:
                personas = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load personas file: {str(e)}")
            return []
        
        try:
            self.init_driver()
        except Exception as e:
            logger.error(f"Driver initialization failed: {str(e)}")
            return []
        
        enriched_personas = []
        
        try:
            for persona in personas:
                persona_name = persona.get("name", "Unknown")
                logger.info(f"\n{'=' * 50}\nProcessing persona: {persona_name}")
                
                # Get social links, but DO NOT add the face URL to them
                social_links = persona.get("social_profile", [])
                
                # REMOVED: Adding face URL to social links
                # Instead, just preserve it in the output
                
                if not social_links:
                    logger.warning(f"No social profiles found for {persona_name}")
                    # Add the persona with available information even without social profiles
                    enriched_persona = persona.copy()
                    if "keywords" not in enriched_persona:
                        enriched_persona["keywords"] = []
                    enriched_personas.append(enriched_persona)
                    continue
                    
                persona_results = []
                
                for url in social_links:
                    cleaned_url = self._clean_url(url)
                    scraped_text = self.scrape_page_content(cleaned_url)
                    
                    if not scraped_text.strip():
                        continue
                        
                    try:
                        profile_data = self.generate_profile_summary(scraped_text, cleaned_url)
                        persona_results.append(profile_data)
                        logger.info(f"Successfully processed profile from {cleaned_url}")
                    except Exception as e:
                        logger.error(f"Profile generation failed for {cleaned_url}: {str(e)}")
                        continue
                
                # Merge and add the results for this persona
                merged_persona = self.merge_persona_results(persona, persona_results)
                enriched_personas.append(merged_persona)
                logger.info(f"Completed processing for {persona_name}")
            
            return enriched_personas
            
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("WebDriver closed")

    def save_results(self, results: List[Dict[str, Any]], file_path: str) -> bool:
        """Save results to the specified output file in the required format."""
        try:
            # Ensure the results match the expected schema while preserving the face field
            formatted_results = []
            for persona in results:
                formatted_persona = {
                    "name": persona.get("name", ""),
                    "image": persona.get("image", ""),
                    "intro": persona.get("intro", ""),
                    "timezone": persona.get("timezone", ""),
                    "company_industry": persona.get("company_industry"),
                    "company_size": persona.get("company_size"),
                    "social_profile": persona.get("social_profile", []),
                    "keywords": persona.get("keywords", [])
                }
                
                # Preserve the face field if it exists
                if "face" in persona:
                    formatted_persona["face"] = persona["face"]
                
                formatted_results.append(formatted_persona)
                
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(formatted_results, f, indent=2, ensure_ascii=False)
            logger.info(f"Results saved to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save results to {file_path}: {str(e)}")
            return False
    
    def check_existing_profiles(self, file_path: str) -> List[Dict[str, Any]]:
        """Check if file exists and return its contents as a list."""
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing and isinstance(existing, list):
                    logger.info(f"Found existing profiles in {file_path}: {len(existing)} entries")
                    return existing
            except Exception as e:
                logger.error(f"Failed to load existing profiles from {file_path}: {str(e)}")
        return []
    
    def merge_and_save_final_results(self) -> bool:
        """Merge enriched profiles with any existing data and save to final output file."""
        try:
            # Get enriched profiles from intermediate file
            enriched_profiles = self.check_existing_profiles(self.output_file)
            if not enriched_profiles:
                logger.error(f"No enriched profiles found in {self.output_file}")
                return False
                
            # Get any existing profiles from final file to append to
            existing_final_profiles = self.check_existing_profiles(self.final_file)
            
            # Create a map of existing profiles by name for easy lookup
            existing_names = {profile.get("name", "").lower(): profile for profile in existing_final_profiles}
            
            final_profiles = existing_final_profiles.copy()
            
            # Update or add new profiles
            new_count = 0
            updated_count = 0
            
            for profile in enriched_profiles:
                name = profile.get("name", "").lower()
                
                if name in existing_names:
                    # Update existing profile
                    existing_profile = existing_names[name]
                    
                    # Prefer new data where available, but keep old data if new is empty
                    if not profile.get("image") and existing_profile.get("image"):
                        profile["image"] = existing_profile["image"]
                        
                    if not profile.get("intro") and existing_profile.get("intro"):
                        profile["intro"] = existing_profile["intro"]
                        
                    if not profile.get("timezone") and existing_profile.get("timezone"):
                        profile["timezone"] = existing_profile["timezone"]
                        
                    if profile.get("company_industry") is None and existing_profile.get("company_industry") is not None:
                        profile["company_industry"] = existing_profile["company_industry"]
                        
                    if profile.get("company_size") is None and existing_profile.get("company_size") is not None:
                        profile["company_size"] = existing_profile["company_size"]
                    
                    # Preserve face field from existing profile if it exists
                    if "face" in existing_profile and "face" not in profile:
                        profile["face"] = existing_profile["face"]
                    
                    # Merge social profiles, avoiding duplicates
                    existing_social = set(existing_profile.get("social_profile", []))
                    new_social = set(profile.get("social_profile", []))
                    profile["social_profile"] = list(existing_social.union(new_social))
                    
                    # Merge keywords, avoiding duplicates
                    existing_keywords = existing_profile.get("keywords", [])
                    new_keywords = profile.get("keywords", [])
                    combined_keywords = []
                    seen_keywords = set()
                    
                    # Add all keywords, avoiding duplicates
                    for keyword in existing_keywords + new_keywords:
                        if keyword.lower() not in seen_keywords:
                            combined_keywords.append(keyword)
                            seen_keywords.add(keyword.lower())
                    
                    profile["keywords"] = combined_keywords[:8]  # Limit to 8 keywords
                    
                    # Update the profile in the final list
                    for i, p in enumerate(final_profiles):
                        if p.get("name", "").lower() == name:
                            final_profiles[i] = profile
                            break
                            
                    updated_count += 1
                else:
                    # Add new profile
                    final_profiles.append(profile)
                    new_count += 1
            
            # Save to final output file
            result = self.save_results(final_profiles, self.final_file)
            
            if result:
                logger.info(f"Successfully saved final results with {new_count} new and {updated_count} updated profiles")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to merge and save final results: {str(e)}")
            return False
            
    def run(self) -> None:
        """Run the complete persona scraping and enrichment process."""
        logger.info(f"Starting persona enrichment process")
        logger.info(f"Input file: {self.input_file}")
        logger.info(f"Intermediate output file: {self.output_file}")
        logger.info(f"Final output file: {self.final_file}")
        
        results = self.process_personas()
        
        if results:
            # Save to intermediate file
            intermediate_save_success = self.save_results(results, self.output_file)
            
            if intermediate_save_success:
                # Merge and save to final file
                final_save_success = self.merge_and_save_final_results()
                
                if final_save_success:
                    logger.info(f"Enrichment completed successfully with {len(results)} personas processed")
                else:
                    logger.error("Failed to save final results to final.json")
            else:
                logger.error("Failed to save intermediate results")
        else:
            logger.error("No results generated")

def main():
    """Main entry point."""
    try:
        scraper = PersonaScraper(PERSONAS_FILE, OUTPUT_FILE, FINAL_OUTPUT_FILE)
        scraper.run()
    except Exception as e:
        logger.critical(f"Critical error in main process: {str(e)}")
        return 1
    return 0

if __name__ == "__main__":
    exit(main())