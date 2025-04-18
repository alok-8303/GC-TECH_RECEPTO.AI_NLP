# 🔍 LinkedIn Persona Mapping for B2B Lead Generation

## 📌 Problem Statement

 A scalable solution to uniquely identify and map user personas to accurate **LinkedIn profiles** , even when the available data is partial or inconsistent.

The system must:
1. ✅ Enrich sparse persona data using available inputs (e.g., name, image).
2. 🎯 Accurately identify matching LinkedIn profiles.
3. 📊 Provide confidence scores for matches using robust verification logic.

---

## 💡 Our Solution

We built a **four-stage automated pipeline** that combines:

1. 🧠 **Facial Recognition**  
   Uses image-based reverse search via FaceCheck API to find potential social profiles.

2. 🤖 **AI-Driven Data Enrichment**  
   Enhances persona data using external knowledge bases and contextual inference (e.g., job title, location, domain).

3. 🔎 **Search Automation**  
   Performs targeted LinkedIn search using enriched details and intelligent search operators followed by fetching profiles using **Bright Data**.

4. 🧮 **Weighted Verification Logic**  
   Scores matches based on multiple signals: facial match confidence, name similarity, company alignment, and keyword context.

---


## 🧠 Step 1: Facial Recognition - Social Profile Discovery

This module performs **image-based recognition** to discover social profiles that match a given persona using facial recognition technology.

---

### 📥 Input

A JSON file with basic persona data:
```json
{
  "name": "John Doe",
  "image_url": "https://example.com/image.jpg"
  " ...if other data"
}
