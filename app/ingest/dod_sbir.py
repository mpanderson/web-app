# app/ingest/dod_sbir.py
import time, hashlib
from datetime import datetime
from typing import Iterable, Any
import requests

from .base import BaseIngestor

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: research@example.org)"}

# Official SBIR.gov API endpoint for DoD topics
SBIR_API_URL = "https://api.www.sbir.gov/public/api/solicitations"

# DoD Topics App URL for fallback links
TOPICS_APP_URL = "https://www.dodsbirsttr.mil/topics-app/"

# Simple in-memory cache to avoid hitting API repeatedly
_cache = {"data": None, "timestamp": None, "ttl_seconds": 300}  # 5 min cache

def _hash(title: str | None, url: str | None) -> str:
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8", errors="ignore"))
    h.update((url or "").encode("utf-8", errors="ignore"))
    return h.hexdigest()

def _to_date(s: Any):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    # catch ISO-like "2025-09-01T00:00:00Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

def _mechanism_from_text(*texts: str | None) -> str | None:
    blob = " ".join([t or "" for t in texts]).upper()
    if "STTR" in blob and "SBIR" in blob:
        return "SBIR/STTR"
    if "STTR" in blob:
        return "STTR"
    if "SBIR" in blob:
        return "SBIR"
    return None

def _component_to_agency(component: str | None) -> str:
    if not component:
        return "DoD SBIR/STTR"
    return f"DoD SBIR/STTR - {component}"

class DodSbirIngestor(BaseIngestor):
    """
    Ingestor for DoD SBIR/STTR topics using the SBIR.gov API.
    Includes retry logic with exponential backoff for rate limiting.
    """
    source = "dod_sbir"

    def _check_cache(self) -> list[dict] | None:
        """Check if we have cached data that's still fresh."""
        if _cache["data"] and _cache["timestamp"]:
            age = (datetime.now() - _cache["timestamp"]).total_seconds()
            if age < _cache["ttl_seconds"]:
                print(f"Using cached DoD SBIR data ({int(age)}s old)")
                return _cache["data"]
        return None

    def _save_cache(self, data: list[dict]):
        """Save data to cache."""
        _cache["data"] = data
        _cache["timestamp"] = datetime.now()

    def _fetch_from_sbir_api(self) -> list[dict]:
        """
        Fetch from SBIR.gov API with retry/backoff for rate limiting.
        If rate limited, waits progressively longer between retries.
        """
        topics = []
        max_retries = 3
        base_delay = 10  # Start with 10 seconds
        
        params = {
            "agency": "DOD",
            "rows": 100,  # Fetch more rows to get pre-release + open topics
            "start": 0
        }
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    # Exponential backoff: 10s, 20s, 40s
                    delay = base_delay * (2 ** (attempt - 1))
                    print(f"⏳ Waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(delay)
                
                print(f"Fetching DoD SBIR topics from SBIR.gov API (attempt {attempt + 1}/{max_retries})...")
                r = requests.get(SBIR_API_URL, params=params, headers=HEADERS, timeout=40)
                
                if r.status_code == 429:
                    print(f"⚠️  API rate limited (429). Will retry after backoff.")
                    if attempt == max_retries - 1:
                        print(f"❌ Rate limit persists after {max_retries} attempts. Try again later.")
                    continue
                    
                r.raise_for_status()
                data = r.json()
                
                # Handle different response formats
                solicitations = data.get("solicitations", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                
                print(f"✅ Received {len(solicitations)} solicitations from API")
                
                for solicitation in solicitations:
                    sol_title = solicitation.get("solicitation_title") or solicitation.get("title")
                    branch = solicitation.get("branch") or solicitation.get("component")
                    program = solicitation.get("program")
                    release_date = solicitation.get("release_date") or solicitation.get("open_date")
                    close_date = solicitation.get("close_date") or solicitation.get("application_due_date")
                    status = solicitation.get("current_status") or solicitation.get("status")
                    
                    # Extract topics from each solicitation
                    sol_topics = solicitation.get("solicitation_topics") or solicitation.get("topics") or []
                    
                    if not sol_topics:
                        # If no topics array, treat the solicitation itself as a topic
                        sol_topics = [solicitation]
                    
                    for topic in sol_topics:
                        topic_dict = {
                            "topic_number": topic.get("topic_number") or topic.get("topicNumber"),
                            "topic_title": topic.get("topic_title") or topic.get("title"),
                            "topic_description": topic.get("topic_description") or topic.get("description"),
                            "branch": topic.get("branch") or branch,
                            "program": program,
                            "solicitation_title": sol_title,
                            "release_date": release_date,
                            "close_date": close_date,
                            "status": status,
                            "topic_link": topic.get("sbir_topic_link") or topic.get("link"),
                        }
                        topics.append(topic_dict)
                
                if topics:
                    self._save_cache(topics)
                    print(f"✅ Successfully fetched {len(topics)} DoD SBIR/STTR topics")
                else:
                    print("⚠️  API returned no topics")
                    
                return topics
                    
            except requests.exceptions.RequestException as e:
                print(f"❌ Network error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    print(f"Failed to fetch DoD SBIR topics after {max_retries} attempts")
                    break
            except Exception as e:
                print(f"❌ Unexpected error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    break
                    
        return []

    def fetch(self) -> Iterable[dict]:
        """
        Fetch DoD SBIR/STTR topics.
        First checks cache, then tries SBIR.gov API with retry logic.
        """
        # 1. Check cache first to avoid unnecessary API calls
        cached = self._check_cache()
        if cached:
            topics = cached
        else:
            # 2. Fetch from SBIR.gov API
            topics = self._fetch_from_sbir_api()
        
        if not topics:
            print("⚠️  WARNING: No DoD SBIR topics found. Check API status or try again later.")
            return

        # Process and yield each topic
        for t in topics:
            number = t.get("topic_number") or t.get("topicNumber") or t.get("number")
            title = t.get("topic_title") or t.get("topicTitle") or t.get("title")
            desc = t.get("topic_description") or t.get("description") or t.get("synopsis")
            comp = t.get("branch") or t.get("component") or t.get("service")
            prog = t.get("program")
            
            open_d = _to_date(t.get("release_date") or t.get("openDate") or t.get("open_date"))
            close_d = _to_date(t.get("close_date") or t.get("closeDate") or t.get("dueDate"))
            status = t.get("status") or t.get("current_status")
            
            topic_link = t.get("topic_link") or t.get("sbir_topic_link")
            if topic_link:
                details_url = topic_link
            elif number:
                details_url = f"{TOPICS_APP_URL}#/?search={number}"
            else:
                details_url = TOPICS_APP_URL

            yield {
                "title": title or (number or "(Untitled)"),
                "summary": desc,
                "opportunity_number": number,
                "component": comp,
                "mechanism": prog or _mechanism_from_text(prog, title, desc),
                "posted_date": open_d,
                "close_date": close_d,
                "landing": details_url,
                "status": status,
                "solicitation": t.get("solicitation_title"),
            }

    def normalize(self, item: dict) -> dict:
        title = item.get("title") or "(Untitled)"
        number = item.get("opportunity_number")
        comp = item.get("component")
        mech = item.get("mechanism")
        url = item.get("landing")
        status = item.get("status")

        return {
            "source": self.source,
            "opportunity_id": number,
            "title": title,
            "agency": _component_to_agency(comp),
            "mechanism": mech,
            "category": status,  # pre-release, open, etc.
            "summary": item.get("summary"),
            "eligibility": None,
            "keywords": None,
            "posted_date": item.get("posted_date"),
            "close_date": item.get("close_date"),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": {
                "component": comp,
                "program": mech,
                "status": status,
                "solicitation": item.get("solicitation"),
            },
            "hash": _hash(title, url),
        }
