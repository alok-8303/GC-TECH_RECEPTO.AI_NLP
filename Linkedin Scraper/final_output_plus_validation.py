
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

# Setup for face matching - rename the model to avoid conflict
device = 'cuda' if torch.cuda.is_available() else 'cpu'
mtcnn = MTCNN(image_size=160, margin=20, device=device)
face_model = InceptionResnetV1(pretrained='vggface2').eval().to(device)  # Renamed from 'model' to 'face_model'

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
        embedding = face_model(face).cpu().numpy()  # Use face_model instead of model
    return embedding

def match_faces(url1, url2):
    try:
        emb1 = extract_face_embedding(url1)
        emb2 = extract_face_embedding(url2)
        score = cosine_similarity(emb1, emb2)[0][0]
        print(f"Face Similarity Score: {score:.4f}")
        return score
    except Exception as e:
        print(f"⚠️ Error matching faces: {e}")
        return None

# Prompt builder with score request
def build_prompt(persona, profiles_subset):
    prompt = f"You are a helpful assistant. Given the persona:\n\n{json.dumps(persona, indent=2)}\n\n"
    prompt += "And the following LinkedIn profiles:\n\n"
    for i, profile in enumerate(profiles_subset, 1):
        url = profile["url"]
        profile_data = profile["profile_data"]
        prompt += f"{i}. URL: {url}\nProfile Data:\n{json.dumps(profile_data, indent=2)}\n\n"
    prompt += (
        "Based on the information, return the **most relevant profile's URL** that best matches the persona, "
        "along with a score from 0 to 1 indicating how confident you are in the match.\n\n"
        "Return ONLY in the following JSON format:\n"
        "{\n  \"matched_url\": \"<URL>\",\n  \"score\": <float between 0 and 1>\n}"
    )
    return prompt

# Initialize Gemini
api_key = 'your api key'
genai.configure(api_key=api_key)
gemini_model = genai.GenerativeModel("gemini-2.0-flash")  # Renamed from 'model' to 'gemini_model'

# Matching function 
def get_best_match(persona, profiles_subset):
    prompt = build_prompt(persona, profiles_subset)
    response = gemini_model.generate_content(prompt)  # Use gemini_model instead of model
    output_text = response.text.strip()

    # Cleanup markdown formatting if present
    if output_text.startswith("```json"):
        output_text = output_text[len("```json"):].strip()
    if output_text.endswith("```"):
        output_text = output_text[:-3].strip()

    try:
        result_json = json.loads(output_text)
        matched_url = result_json.get("matched_url")
        text_score = result_json.get("score", 0)

        for profile in profiles_subset:
            if profile["url"] == matched_url:
                # Initialize result dictionary
                result = {
                    "matched_url": matched_url,
                    "text_score": text_score,
                    "matched_profile": profile["profile_data"]
                }

                # Add image match score if images are available
                persona_image = persona.get("image")
                profile_image = profile["profile_data"].get("avatar")

                face_score = None
                if persona_image and profile_image:
                    print(f"Comparing images:\n- Persona: {persona_image}\n- Profile: {profile_image}")
                    face_score = match_faces(persona_image, profile_image)
                    result["face_score"] = face_score

                # Calculate combined score (weighted average if face score exists)
                if face_score is not None:
                    # Weight text score at 70% and face score at 30%
                    combined_score = (0.7 * text_score) + (0.3 * face_score)
                    result["combined_score"] = combined_score
                else:
                    # If no face score, use text score only
                    result["combined_score"] = text_score

                return result

        raise ValueError("URL not found in provided profiles.")

    except Exception as e:
        print("❌ Error parsing Gemini output:", e)
        print("Raw response:\n", output_text)
        return None

# Output folder
os.makedirs("matches", exist_ok=True)

# Run matching
for idx, persona in enumerate(persona_list, 1):
    name = persona.get("name")
    profiles_subset = profiles_by_name.get(name, [])

    if not profiles_subset:
        print(f"⚠️ Skipping Persona {idx} ({name}) - No profiles found.")
        continue

    print(f"🔍 Processing Persona {idx}: {name} with {len(profiles_subset)} profiles...")

    match = get_best_match(persona, profiles_subset)

    if match:
        matched_url = match["matched_url"]
        matched_profile = match["matched_profile"]
        text_score = match["text_score"]
        face_score = match.get("face_score", None)
        combined_score = match["combined_score"]

        # Save results
        with open(f"matches/persona_{idx}_matched_result.json", "w") as f:
            json.dump({
                "matched_url": matched_url,
                "text_score": text_score,
                "face_score": float(face_score) if face_score is not None else None, # Convert face_score to float
        "combined_score": float(combined_score), # Convert combined_score to float
                "matched_profile": matched_profile
            }, f, indent=2)

        # Print summary
        face_score_str = f", Face Score: {face_score:.4f}" if face_score is not None else ", No face match"
        print(f"✅ Matched with URL: {matched_url}")
        print(f"   Text Score: {text_score:.4f}{face_score_str}")
        print(f"   Combined Score: {combined_score:.4f}\n")
    else:
        print(f"❌ No match for Persona {idx}: {name}\n")

print("Matching process completed! ")

