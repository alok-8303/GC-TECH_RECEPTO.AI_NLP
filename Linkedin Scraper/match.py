# !pip install -q facenet-pytorch torchvision pillow
# !pip install -q pillow --upgrade

import json, os
import google.generativeai as genai
import torch
from PIL import Image
import requests
from io import BytesIO
from facenet_pytorch import InceptionResnetV1, MTCNN
from sklearn.metrics.pairwise import cosine_similarity
import re

# Load data
with open("final.json", "r") as f:
    persona_list = json.load(f)

with open("final_cleaned_linkedin_profiles.json", "r") as f:
    linkedin_data = json.load(f)

# Index LinkedIn profiles by persona name
profiles_by_name = {
    entry["persona"]["name"]: entry["linkedin_profiles"]
    for entry in linkedin_data
    if "persona" in entry and "linkedin_profiles" in entry
}

# Setup for face matching
device = 'cuda' if torch.cuda.is_available() else 'cpu'
mtcnn = MTCNN(image_size=160, margin=20, device=device)
face_model = InceptionResnetV1(pretrained='vggface2').eval().to(device)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
}

# Convert Google Drive link to direct download
def convert_drive_link(url):
    match = re.search(r'd/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url

# Download image with proper headers and support for Google Drive
def download_image(url):
    url = convert_drive_link(url)
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        raise Exception(f"Failed to download image from {url}")
    return Image.open(BytesIO(response.content)).convert('RGB')

def extract_face_embedding(url):
    img = download_image(url)
    face = mtcnn(img)
    if face is None:
        raise Exception(f"No face detected in image: {url}")
    face = face.unsqueeze(0).to(device)
    with torch.no_grad():
        embedding = face_model(face).cpu().numpy()
    return embedding

def match_faces(url1, url2):
    try:
        emb1 = extract_face_embedding(url1)
        emb2 = extract_face_embedding(url2)
        score = cosine_similarity(emb1, emb2)[0][0]
        return score
    except Exception as e:
        print(f"⚠️ Error matching faces: {e}")
        return None

# Prompt builder for text matching
def build_prompt(persona, profile):
    prompt = f"You are a helpful assistant. Given the persona:\n\n{json.dumps(persona, indent=2)}\n\n"
    prompt += "And the following LinkedIn profile:\n\n"
    url = profile["url"]
    profile_data = profile["profile_data"]
    prompt += f"URL: {url}\nProfile Data:\n{json.dumps(profile_data, indent=2)}\n\n"
    prompt += (
        "Based on the information, rate how well this LinkedIn profile matches the persona "
        "on a scale from 0 to 1, where 1 is a perfect match and 0 is no match at all.\n\n"
        "Return ONLY a floating point number between 0 and 1, with no explanation or other text."
    )
    return prompt

# Initialize Gemini
api_key = "your-api-key-here"  # Replace with your actual API key
genai.configure(api_key=api_key)
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# Get text similarity score for a single profile
def get_text_similarity(persona, profile):
    prompt = build_prompt(persona, profile)
    response = gemini_model.generate_content(prompt)
    output_text = response.text.strip()
    
    try:
        text_score = float(output_text)
        if 0 <= text_score <= 1:
            return text_score
        else:
            print(f"⚠️ Score out of range (0-1): {text_score}, clamping to valid range")
            return max(0, min(text_score, 1))  # Clamp to valid range
    except ValueError:
        print(f"❌ Could not parse score from response: '{output_text}'")
        return 0.0

# Process all profiles for a persona
def process_persona(persona, profiles_subset):
    name = persona.get("name")
    persona_image = persona.get("image")
    
    results = []
    
    print(f"🔍 Processing {len(profiles_subset)} LinkedIn profiles for {name}...")
    
    # First pass: Calculate face similarity scores for all profiles if persona has an image
    if persona_image:
        print(f"👤 Calculating face similarity scores...")
        for profile in profiles_subset:
            profile_image = profile["profile_data"].get("avatar")
            face_score = None
            
            if profile_image:
                print(f"  - Comparing images for profile: {profile['url']}")
                face_score = match_faces(persona_image, profile_image)
                if face_score is not None:
                    print(f"    Face Similarity Score: {face_score:.4f}")
            
            # Store face score with profile for later
            profile["face_score"] = face_score
    
    # Second pass: Calculate text similarity scores for all profiles
    print(f"📝 Calculating text similarity scores...")
    for profile in profiles_subset:
        print(f"  - Analyzing text similarity for profile: {profile['url']}")
        text_score = get_text_similarity(persona, profile)
        print(f"    Text Similarity Score: {text_score:.4f}")
        
        # Store text score with profile
        profile["text_score"] = text_score
        
        # Calculate combined score
        face_score = profile.get("face_score")
        if face_score is not None:
            # Weight text score at 70% and face score at 30%
            combined_score = (0.7 * text_score) + (0.3 * face_score)
        else:
            # If no face score, use text score only
            combined_score = text_score
        
        profile["combined_score"] = combined_score
        print(f"    Combined Score: {combined_score:.4f}")
        
        results.append({
            "url": profile["url"],
            "text_score": text_score,
            "face_score": face_score,
            "combined_score": combined_score
        })
    
    # Find the best match
    if results:
        best_match = max(results, key=lambda x: x["combined_score"])
        return best_match
    
    return None

# Output folder
os.makedirs("matches", exist_ok=True)

# Create list to hold all matches for the output.json file
all_matches = []

print("Starting matching process...")

# Run matching
for idx, persona in enumerate(persona_list, 1):
    name = persona.get("name")
    profiles_subset = profiles_by_name.get(name, [])

    if not profiles_subset:
        print(f"⚠️ Persona {idx}: {name} - No profiles found")
        all_matches.append({
            "persona_name": name,
            "matched_url": None,
            "text_score": None,
            "face_score": None,
            "combined_score": None
        })
        continue

    print(f"\n===== Processing Persona {idx}: {name} with {len(profiles_subset)} profiles =====")

    best_match = process_persona(persona, profiles_subset)

    if best_match:
        # Save individual result
        with open(f"matches/persona_{idx}_matched_result.json", "w") as f:
            json.dump({
                "persona_name": name,
                "matched_url": best_match["url"],
                "text_score": best_match["text_score"],
                "face_score": float(best_match["face_score"]) if best_match["face_score"] is not None else None,
                "combined_score": float(best_match["combined_score"])
            }, f, indent=2)

        # Add to all_matches
        all_matches.append({
            "persona_name": name,
            "matched_url": best_match["url"],
            "text_score": best_match["text_score"],
            "face_score": float(best_match["face_score"]) if best_match["face_score"] is not None else None,
            "combined_score": float(best_match["combined_score"])
        })

        # Print summary
        face_score_str = f", Face Score: {best_match['face_score']:.4f}" if best_match["face_score"] is not None else ", No face match"
        print(f"\n✅ Best match for {name}:")
        print(f"   URL: {best_match['url']}")
        print(f"   Text Score: {best_match['text_score']:.4f}{face_score_str}")
        print(f"   Combined Score: {best_match['combined_score']:.4f}")
    else:
        print(f"❌ No match for Persona {idx}: {name}")
        all_matches.append({
            "persona_name": name,
            "matched_url": None,
            "text_score": None,
            "face_score": None,
            "combined_score": None
        })

# Save all matches to output.json (without profile data)
with open("output.json", "w") as f:
    json.dump(all_matches, f, indent=2)

print("\nMatching process completed! Results saved to output.json")