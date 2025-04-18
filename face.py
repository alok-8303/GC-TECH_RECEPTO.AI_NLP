import time
import requests
import urllib.request
import ssl
import certifi
import os
import json
from urllib.parse import urlparse, parse_qs

# ----------------- CONFIG ------------------
TESTING_MODE = True
APITOKEN = "your api key"
PERSONAS_FILE = 'personas.json'
OUTPUT_JSON = 'enhanced_personas.json'
MIN_CONFIDENCE = 85
# -------------------------------------------

# ----------------- LINKEDIN VALIDATION ------------------
def is_linkedin_profile(url):
    try:
        parsed = urlparse(url)
        if 'linkedin.com' not in parsed.netloc.lower():
            return False

        path_parts = [p for p in parsed.path.split('/') if p]
        return (
            (len(path_parts) >= 2 and path_parts[0] in ['in', 'pub']) or
            (len(path_parts) == 1 and path_parts[0] == 'profile' and 'id' in parse_qs(parsed.query))
        )
    except:
        return False

def extract_linkedin_profile(url):
    try:
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]

        if is_linkedin_profile(url):
            if path_parts[0] in ['in', 'pub']:
                return f"https://www.linkedin.com/{path_parts[0]}/{path_parts[1]}"
            elif path_parts[0] == 'profile':
                id = parse_qs(parsed.query).get('id', [''])[0]
                return f"https://www.linkedin.com/profile/view?id={id}"

        if 'post' in path_parts or 'activity' in path_parts:
            query = parse_qs(parsed.query)
            if 'author' in query:
                author = query['author'][0]
                if author.startswith('ACo'):
                    return f"https://www.linkedin.com/profile/view?id={author}"
            for part in path_parts:
                if part.startswith('urn:li:activity:'):
                    activity_id = part.split(':')[-1]
                    return f"https://www.linkedin.com/profile/view?id={activity_id}"

        return None
    except:
        return None

# ----------------- SOCIAL MEDIA PRIORITY ------------------
SOCIAL_MEDIA_PRIORITY = [
    'linkedin.com',
    'twitter.com',
    'github.com',
    'facebook.com',
    'instagram.com'
]

def get_best_social_profile(urls):
    for domain in SOCIAL_MEDIA_PRIORITY:
        for url in urls:
            if domain in url.lower():
                return url
    return urls[0] if urls else None

# ----------------- CORE FUNCTIONALITY ------------------
def download_image(url, output_file):
    if 'drive.google.com' in url:
        file_id = url.split('/d/')[1].split('/')[0]
        url = f"https://drive.google.com/uc?export=download&id={file_id}"

    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(url, context=context) as response:
            with open(output_file, 'wb') as out_file:
                out_file.write(response.read())
        return True
    except Exception as e:
        print(f"❌ Image download failed: {e}")
        return False

def search_by_face(image_file):
    if TESTING_MODE:
        print('🔍 TESTING MODE: Results may be less accurate')

    headers = {'Authorization': APITOKEN}
    try:
        with open(image_file, 'rb') as f:
            response = requests.post(
                'https://facecheck.id/api/upload_pic',
                headers=headers,
                files={'images': f}
            ).json()
    except Exception as e:
        return f"Upload failed: {e}", None

    if response.get('error'):
        return response['error'], None

    search_id = response['id_search']
    print(f"⚡ Search ID: {search_id}")

    while True:
        result = requests.post(
            'https://facecheck.id/api/search',
            headers=headers,
            json={
                'id_search': search_id,
                'with_progress': True,
                'demo': TESTING_MODE
            }
        ).json()

        if result.get('error'):
            return result['error'], None

        if result.get('output'):
            valid_urls = []
            for item in result['output']['items']:
                if item['score'] >= MIN_CONFIDENCE:
                    url = item['url']

                    if 'linkedin.com' in url.lower():
                        linkedin_url = extract_linkedin_profile(url)
                        if linkedin_url:
                            return None, linkedin_url
                    
                    valid_urls.append(url)

            if valid_urls:
                best_profile = get_best_social_profile(valid_urls)
                return None, best_profile

            return None, None

        print(f"⏳ {result['progress']}% - {result['message']}")
        time.sleep(1)

def enhance_personas():
    try:
        with open(PERSONAS_FILE) as f:
            personas = json.load(f)
    except Exception as e:
        print(f"❌ Failed to load personas: {e}")
        return []

    enhanced_personas = []

    for persona in personas:
        print(f"\n🔎 Processing: {persona['name']}")
        enhanced_persona = persona.copy()

        if not persona.get('image'):
            print("⚠️ No image available - skipping")
            enhanced_personas.append(enhanced_persona)
            continue

        temp_file = f"temp_{persona['name'][:20]}.jpg"
        if not download_image(persona['image'], temp_file):
            enhanced_personas.append(enhanced_persona)
            continue

        error, profile_url = search_by_face(temp_file)

        if profile_url:
            if 'linkedin.com' in profile_url.lower():
                enhanced_persona['face'] = profile_url
            else:
                if not enhanced_persona.get('social_profile') or not enhanced_persona['social_profile']:
                    enhanced_persona['social_profile'] = [profile_url]
                elif profile_url not in enhanced_persona['social_profile']:
                    enhanced_persona['social_profile'].append(profile_url)

        enhanced_personas.append(enhanced_persona)

        if os.path.exists(temp_file):
            os.remove(temp_file)

    with open(OUTPUT_JSON, 'w') as f:
        json.dump(enhanced_personas, f, indent=2)

    print(f"\n✅ Enhanced personas saved to {OUTPUT_JSON}")
    return enhanced_personas

# ----------------- MAIN EXECUTION ------------------
if __name__ == "__main__":
    print("🚀 Starting persona enhancement...")
    results = enhance_personas()

    print("\n📊 Final Results:")
    for p in results:
        print(f"\n👤 {p['name']}")
        if p.get('face'):
            print(f"   🔍 Face Match: {p['face']}")
        if p.get('social_profile'):
            print(f"   🔗 Social Profiles: {', '.join(p['social_profile'])}")
        else:
            print("   ❌ No profiles found")