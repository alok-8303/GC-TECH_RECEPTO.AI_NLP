# Persona Match: Enhanced LinkedIn Profile Matching via Image & Semantic Intelligence

## 🧠 Overview

This project is a comprehensive pipeline that automates the process of identifying and validating LinkedIn profiles for given personas using facial recognition, semantic scraping, web search, and AI-based validation. It's a submission to a problem statement focused on data enrichment and identity resolution.

## 📁 Repository Structure and Workflow

The pipeline flows through **five key stages**, represented by five Python scripts:
face.py → social.py → my.py → brightdata_fetch.py → final_output_plus_validation.py

Each script enhances the persona data and prepares it for the next stage.

---

## 1️⃣ `face.py` – Face-Based Social Profile Inference

**Purpose:**  
Takes input personas with images and performs reverse image searches to find the most probable social media profile.

**Key Technologies:**
- **API:** [FaceCheck ID API](https://facecheck.id/)
- **Libraries:** `requests`, `urllib`, `certifi`, `ssl`
- **Input:** `personas.json` (raw personas with names and images)
- **Output:** `enhanced_personas.json` (updated with `face` and `social_profile` fields)

**Logic:**
- Downloads persona images from Google Drive.
- Submits them to FaceCheck’s API for reverse search.
- Extracts the most confident social or LinkedIn match (minimum 85% score).
- Augments personas with `face` or `social_profile` fields depending on match type.

---

## 2️⃣ `social.py` – Web Scraping & Profile Enrichment

**Purpose:**  
Uses discovered social links to scrape real-world content and generate enriched professional summaries.

**Key Technologies:**
- **Tech Stack:** Selenium + BeautifulSoup for scraping, OpenAI GPT-4 API for summarization
- **Browser Driver:** `webdriver-manager` with ChromeDriver
- **Input:** `enhanced_personas.json`
- **Output:** `final.json` (consolidated persona data with fields like `intro`, `keywords`, etc.)

**Logic:**
- Loads each persona's social links (excluding FaceCheck results for integrity).
- Scrapes content, then uses GPT-4 to extract structured data: name, title, bio, expertise, keywords.
- Merges extracted data back into personas with deduplication and keyword cleanup.

---

## 3️⃣ `my.py` – LinkedIn Profile Search via Google

**Purpose:**  
Performs Google searches to find potential LinkedIn profiles based on enriched persona metadata.

**Key Technologies:**
- **Tool:** `googlesearch` Python package
- **Libraries:** `re`, `ratelimit`, `backoff`, `logging`
- **Input:** `final.json` (enriched persona data)
- **Output:** `linkedin_results.json` (names mapped to profile search results)

**Logic:**
- Constructs search queries using name, keywords, inferred company, and location.
- Executes rate-limited Google searches.
- Extracts LinkedIn profile URLs (`linkedin.com/in/`) from results.
- Consolidates found profiles for each persona.

---

## 4️⃣ `brightdata_fetch.py` – Profile Scraping via BrightData

**Purpose:**  
Triggers LinkedIn profile scraping using BrightData’s Dataset API and aggregates results.

**Key Technologies:**
- **API:** [BrightData Dataset Collection API](https://brightdata.com/)
- **Libraries:** `requests`, `datetime`, `json`
- **Input:** `linkedin_results.json` + `final.json`
- **Output:** `final_cleaned_linkedin_profiles.json` (scraped & cleaned data)

**Logic:**
- Maps LinkedIn URLs to their respective personas.
- Initiates profile scraping via BrightData’s snapshot API.
- Monitors collection status until completion.
- Cleans results by removing unneeded fields (`certifications`, `activity`, etc.)

---

## 5️⃣ `final_output_plus_validation.py` – Final Validation via Face and Gemini AI

**Purpose:**  
Performs final persona-to-profile matching using:
- Text similarity via Gemini AI
- Face similarity via InceptionResNet-V1 (FaceNet)

**Key Technologies:**
- **AI:** Google Gemini API (generative AI for prompt-based reasoning)
- **ML Models:** `facenet-pytorch`, `MTCNN` for face detection, cosine similarity for comparison
- **Input:** `final.json` + `final_cleaned_linkedin_profiles.json`
- **Output:** JSON files per match saved in `/matches/`

**Logic:**
- For each persona, gathers all candidate LinkedIn profiles.
- Builds a structured prompt for Gemini to score best text match.
- If image is available, calculates a cosine similarity between persona and profile photos.
- Final match score is a weighted average: **70% text + 30% face** (if available).
- Stores detailed match result including individual and combined scores.

---

## 🔧 API Keys Required

This project requires valid API keys for:
- FaceCheck
- OpenAI (GPT-4)
- Google Gemini
- BrightData Dataset API

---

## 📥 Input Files

| File Name                     | Description                                          |
|------------------------------|------------------------------------------------------|
| `personas.json`              | Raw data of user personas (name, image URL, etc.)    |
| `enhanced_personas.json`     | Output from `face.py` with added profile URLs        |
| `final.json`                 | Fully enriched personas (used by most scripts)       |
| `linkedin_results.json`      | Candidate LinkedIn profiles discovered via Google    |
| `final_cleaned_linkedin_profiles.json` | Cleaned LinkedIn profiles used for validation |

---

## 📤 Output Files

| File                                   | Description                                             |
|----------------------------------------|---------------------------------------------------------|
| `matches/persona_X_matched_result.json`| Final match for each persona with scores and profile data |
| `linkedin_results.json`                | Google search results with LinkedIn profile URLs        |
| `final.json`                           | Final enriched personas with all metadata               |
| `final_cleaned_linkedin_profiles.json` | Verified and cleaned LinkedIn data from BrightData      |

---

## ✅ Summary

This project demonstrates a full-fledged pipeline for enriching, validating, and matching professional identity data using:
- Multi-modal AI (image + text)
- Web scraping
- Semantic reasoning
- API orchestration

It bridges open-source search techniques with commercial APIs and models to create a reliable and scalable profile resolution system.
