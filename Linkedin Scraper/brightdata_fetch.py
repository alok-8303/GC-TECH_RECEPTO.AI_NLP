

import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Any
import requests
import os


api_key = "your api key"

class LinkedInProfileInfo:
    def __init__(self, api_token: str, dataset_id: str = "gd_l1viktl72bvl7bjuj0"):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self.dataset_id = dataset_id

    def collect_profile_info(self, profile_urls: List[Dict[str, str]]) -> Optional[List[Dict[str, Any]]]:
        try:
            start_time = datetime.now()
            print(f"\nStarting collection for {len(profile_urls)} profiles at {start_time.strftime('%H:%M:%S')}")

            collection_response = self._trigger_collection(profile_urls)
            if not collection_response or "snapshot_id" not in collection_response:
                raise ValueError("Failed to initiate data collection")

            snapshot_id = collection_response["snapshot_id"]
            print("\nCollecting data:")

            while True:
                status = self._check_status(snapshot_id)
                elapsed = (datetime.now() - start_time).seconds

                print(f"\rStatus: {status} ({elapsed}s elapsed)", end="", flush=True)

                if status == "ready":
                    print(f"\nCollection completed after {elapsed} seconds")
                    profile_data = self._get_data(snapshot_id)
                    if profile_data:
                        print(f"✓ Collected {len(profile_data)} profiles")
                        return profile_data
                    break
                elif status in ["failed", "error"]:
                    print(f"\nCollection failed with status: {status}")
                    return None
                time.sleep(5)
        except Exception as e:
            print(f"\nERROR: {str(e)}")
            return None

    def _trigger_collection(self, profile_urls: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        try:
            print("Connecting to API...")
            response = requests.post(
                "https://api.brightdata.com/datasets/v3/trigger",
                headers=self.headers,
                params={"dataset_id": self.dataset_id},
                json=profile_urls,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Failed to trigger collection: {str(e)}")
            return None

    def _check_status(self, snapshot_id: str) -> str:
        try:
            response = requests.get(
                f"https://api.brightdata.com/datasets/v3/progress/{snapshot_id}",
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json().get("status", "error")
        except requests.exceptions.RequestException:
            return "error"

    def _get_data(self, snapshot_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            response = requests.get(
                f"https://api.brightdata.com/datasets/v3/snapshot/{snapshot_id}",
                headers=self.headers,
                params={"format": "json"},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException:
            return None

def clean_linkedin_profiles(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for person in data:
        for profile in person.get("linkedin_profiles", []):
            profile_data = profile.get("profile_data", {})
            for field in ["people_also_viewed", "similar_profiles", "certifications", "patents", "activity"]:
                profile_data.pop(field, None)
    return data

def main():
    with open("final.json", "r") as f:
        personas = json.load(f)

    with open("linkedin_results.json", "r") as f:
        linkedin_results = json.load(f)

    url_to_person = {}
    profile_url_list = []
    name_order = [entry["name"] for entry in linkedin_results]  # Maintain order

    for entry in linkedin_results:
        name = entry["name"]
        persona = next((p for p in personas if p["name"] == name), None)
        urls = entry.get("linkedin_urls", [])

        if persona:
            for url in urls:
                url_to_person[url] = {"name": name, "persona": persona}
                profile_url_list.append({"url": url})

    collector = LinkedInProfileInfo(api_key)
    scraped_profiles = collector.collect_profile_info(profile_url_list)

    grouped_results = {name: {
        "name": name,
        "persona": next((p for p in personas if p["name"] == name), {}),
        "linkedin_profiles": []
    } for name in name_order}

    for url_data in scraped_profiles or []:
        url = url_data.get("url")
        if not url or url not in url_to_person:
            continue

        person_info = url_to_person[url]
        name = person_info["name"]

        grouped_results[name]["linkedin_profiles"].append({
            "url": url,
            "profile_data": url_data
        })

    output_data = [grouped_results[name] for name in name_order if name in grouped_results]
    cleaned_data = clean_linkedin_profiles(output_data)

    with open("final_cleaned_linkedin_profiles.json", "w", encoding="utf-8") as f:
        json.dump(cleaned_data, f, indent=2, ensure_ascii=False)

    print("\n✅ Final cleaned results saved to final_cleaned_linkedin_profiles.json")

if __name__ == "__main__":
    main()

