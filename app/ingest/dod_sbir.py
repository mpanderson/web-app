# app/ingest/dod_sbir.py
import os, time, re, json, hashlib
from datetime import datetime, timedelta
from typing import Iterable, Any
import requests
from bs4 import BeautifulSoup

from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: you@example.org)"}

# Official SBIR.gov API endpoint for DoD topics
SBIR_API_URL = "https://api.www.sbir.gov/public/api/solicitations"

# DoD Topics App - try multiple possible endpoints
TOPICS_APP_BASE = "https://www.dodsbirsttr.mil/topics-app"
TOPICS_APP_ENDPOINTS = [
    f"{TOPICS_APP_BASE}/topicSearch",
    f"{TOPICS_APP_BASE}/api/topics",
    f"{TOPICS_APP_BASE}/api/topicSearch",
    "https://www.dodsbirsttr.mil/api/topics",
]

# Simple in-memory cache to avoid rate limiting
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
    Ingestor for DoD SBIR/STTR topics.
    Priority: 
    1. Check cache (avoid rate limiting)
    2. Try SBIR.gov API with retry/backoff
    3. Fall back to DoD topics app endpoints
    4. Parse HTML as last resort
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
        Fetch from SBIR.gov API with exponential backoff retry logic.
        """
        topics = []
        max_retries = 3
        base_delay = 2  # seconds
        
        params = {
            "agency": "DOD",
            "rows": 50,
            "start": 0
        }
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + (time.time() % 1)
                    print(f"Retry {attempt}/{max_retries} after {delay:.1f}s delay...")
                    time.sleep(delay)
                
                r = requests.get(SBIR_API_URL, params=params, headers=HEADERS, timeout=40)
                
                if r.status_code == 429:
                    print(f"SBIR.gov API rate limited (429)")
                    continue  # Retry
                    
                r.raise_for_status()
                data = r.json()
                
                # Process solicitations and extract topics
                solicitations = data if isinstance(data, list) else []
                
                for solicitation in solicitations:
                    sol_title = solicitation.get("solicitation_title") or solicitation.get("title")
                    branch = solicitation.get("branch") or solicitation.get("component")
                    program = solicitation.get("program")
                    release_date = solicitation.get("release_date") or solicitation.get("open_date")
                    close_date = solicitation.get("close_date") or solicitation.get("application_due_date")
                    status = solicitation.get("current_status")
                    
                    sol_topics = solicitation.get("solicitation_topics") or solicitation.get("topics") or []
                    
                    for topic in sol_topics:
                        topic_dict = {
                            "topic_number": topic.get("topic_number"),
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
                    print(f"Fetched {len(topics)} topics from SBIR.gov API")
                return topics
                
            except requests.exceptions.RequestException as e:
                print(f"SBIR.gov API attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    break
                    
        return []

    def _fetch_from_topics_app(self) -> list[dict]:
        """
        Try various DoD topics app API endpoints with proper headers.
        """
        # Headers that mimic browser requests
        browser_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{TOPICS_APP_BASE}/",
            "Origin": "https://www.dodsbirsttr.mil",
        }
        
        # Try different query parameter combinations
        query_params_variants = [
            {"cycle": "All Active Solicitations", "status": "Pre-Release,Open", "size": 100},
            {"status": "Pre-Release,Open", "size": 100},
            {"size": 100},
            {},
        ]
        
        for endpoint in TOPICS_APP_ENDPOINTS:
            for params in query_params_variants:
                try:
                    r = requests.get(endpoint, params=params, headers=browser_headers, timeout=30)
                    if r.status_code == 200:
                        try:
                            data = r.json()
                            # Try to find topics in various structures
                            if isinstance(data, list) and data:
                                print(f"Found {len(data)} topics from {endpoint}")
                                return data
                            if isinstance(data, dict):
                                for key in ("topics", "data", "results", "content", "items"):
                                    if key in data and isinstance(data[key], list) and data[key]:
                                        print(f"Found {len(data[key])} topics from {endpoint} -> {key}")
                                        return data[key]
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    continue
        
        return []

    def fetch(self) -> Iterable[dict]:
        # 1. Check cache first
        cached = self._check_cache()
        if cached:
            topics = cached
        else:
            # 2. Try SBIR.gov API with retry
            topics = self._fetch_from_sbir_api()
            
            # 3. Fall back to topics app endpoints
            if not topics:
                print("Trying DoD topics app endpoints...")
                topics = self._fetch_from_topics_app()
        
        if not topics:
            print("WARNING: No DoD SBIR topics found from any source")
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
                details_url = f"{TOPICS_APP_BASE}/#/?search={number}"
            else:
                details_url = TOPICS_APP_BASE

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
