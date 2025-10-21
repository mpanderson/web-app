import requests
import pandas as pd
from datetime import datetime

API_URL = "https://api.grants.gov/v1/api/search2"

# Define search parameters (filters)
payload = {
    "oppStatuses": "forecasted|posted",  # get open and forecasted
    "rows": 100,                         # how many per page (max ~500)
    "startRecordNum": 0,
    "resultType": "json"
}

def fetch_all():
    """Fetch all available opportunities with pagination."""
    all_results = []
    start = 0
    rows = 500  # request 500 at a time (API limit)
    while True:
        payload.update({"rows": rows, "startRecordNum": start})
        r = requests.post(API_URL, json=payload)
        r.raise_for_status()
        data = r.json()
        docs = data.get("oppHits", [])
        if not docs:
            break
        all_results.extend(docs)
        print(f"Fetched {len(docs)} records (total {len(all_results)})")
        if len(docs) < rows:
            break
        start += rows
    return all_results

def save_csv(results, filename=None):
    """Save results to CSV for ingestion into your matcher."""
    if not results:
        print("No results to save.")
        return
    df = pd.DataFrame(results)
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"grantsgov_{timestamp}.csv"
    df.to_csv(filename, index=False)
    print(f"Saved {len(df)} opportunities to {filename}")

if __name__ == "__main__":
    results = fetch_all()
    save_csv(results)
