

api = "your api key"

import json, os
import google.generativeai as genai

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
genai.configure(api_key=api)
model = genai.GenerativeModel("gemini-2.0-flash")

# Matching function
def get_best_match(persona, profiles_subset):
    prompt = build_prompt(persona, profiles_subset)
    response = model.generate_content(prompt)
    output_text = response.text.strip()

    # Cleanup markdown formatting if present
    if output_text.startswith("```json"):
        output_text = output_text[len("```json"):].strip()
    if output_text.endswith("```"):
        output_text = output_text[:-3].strip()

    try:
        result_json = json.loads(output_text)
        matched_url = result_json.get("matched_url")
        score = result_json.get("score", None)

        for profile in profiles_subset:
            if profile["url"] == matched_url:
                return {
                    "matched_url": matched_url,
                    "score": score,
                    "matched_profile": profile["profile_data"]
                }

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
        score = match["score"]

        # Save results
        with open(f"matches/persona_{idx}_matched_url.json", "w") as f:
            json.dump({
                "matched_url": matched_url,
                "score": score
            }, f, indent=2)

        with open(f"matches/persona_{idx}_matched_profile.json", "w") as f:
            json.dump(matched_profile, f, indent=2)

        print(f"✅ Matched with URL: {matched_url} (Score: {score})\n")
    else:
        print(f"❌ No match for Persona {idx}: {name}\n")

