# app/ingest/dod_sbir.py
import os, time, re, json, hashlib
from datetime import datetime, timedelta
from typing import Iterable, Any
import requests
from bs4 import BeautifulSoup
from requests_html import HTMLSession

from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: you@example.org)"}

# Official SBIR.gov API endpoint for DoD topics
SBIR_API_URL = "https://api.www.sbir.gov/public/api/solicitations"

# DoD Topics App
TOPICS_APP_URL = "https://www.dodsbirsttr.mil/topics-app/"

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
    Ingestor for DoD SBIR/STTR topics using browser rendering.
    This captures both pre-release and open announcements from the SPA.
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

    def _fetch_with_browser_rendering(self) -> list[dict]:
        """
        Use requests-html to render the JavaScript SPA and scrape the table.
        """
        topics = []
        
        try:
            print("Rendering DoD topics app with JavaScript...")
            session = HTMLSession()
            r = session.get(TOPICS_APP_URL, timeout=60)
            
            # Render JavaScript - this will execute the JS and wait for page to load
            # Note: This might take 10-20 seconds
            r.html.render(timeout=30, sleep=3)  # Wait 3 seconds after JS execution
            
            soup = BeautifulSoup(r.html.html, 'html.parser')
            
            # Parse the topics table
            # Looking for table rows with topic data
            rows = soup.select('tr') or soup.select('[class*="topic"]') or soup.select('[class*="row"]')
            
            print(f"Found {len(rows)} potential topic rows")
            
            for row in rows:
                # Extract topic data from table cells
                cells = row.find_all(['td', 'div'])
                
                # Try to find topic number pattern (e.g., A254-049, CBD254-005)
                text = row.get_text(' ', strip=True)
                topic_match = re.search(r'([A-Z]+\d+-[A-Z]?\d+)', text)
                
                if not topic_match:
                    continue
                    
                number = topic_match.group(1)
                
                # Extract other fields
                # Typical structure: Topic #, Title, Open, Close, Release #, etc.
                title_match = re.search(r'([A-Z][^0-9\n]{20,}?)(?:\d{2}/\d{2}/\d{4}|Pre-Release|Open)', text)
                title = title_match.group(1).strip() if title_match else text[:100]
                
                # Extract dates (MM/DD/YYYY format)
                dates = re.findall(r'(\d{2}/\d{2}/\d{4})', text)
                open_date = _to_date(dates[0]) if len(dates) > 0 else None
                close_date = _to_date(dates[1]) if len(dates) > 1 else None
                
                # Check if pre-release
                status = "Pre-Release" if "Pre-Release" in text else ("Open" if "Open" in text else None)
                
                # Extract component (ARMY, CBD, etc.)
                comp_match = re.search(r'\b(ARMY|NAVY|AIR FORCE|CBD|DARPA|MDA|SOCOM|OSD)\b', text, re.IGNORECASE)
                component = comp_match.group(1).upper() if comp_match else None
                
                topics.append({
                    "topic_number": number,
                    "topic_title": title,
                    "topic_description": None,  # Would need to click into detail page
                    "branch": component,
                    "program": None,  # Extract from detail if needed
                    "release_date": open_date,
                    "close_date": close_date,
                    "status": status,
                })
            
            session.close()
            
            if topics:
                print(f"Scraped {len(topics)} topics from rendered page")
                self._save_cache(topics)
            
            return topics
            
        except Exception as e:
            print(f"Browser rendering failed: {e}")
            return []

    def _fetch_from_sbir_api(self) -> list[dict]:
        """
        Try SBIR.gov API (with retry logic for rate limiting).
        """
        topics = []
        
        params = {
            "agency": "DOD",
            "rows": 50,
            "start": 0
        }
        
        try:
            r = requests.get(SBIR_API_URL, params=params, headers=HEADERS, timeout=40)
            
            if r.status_code == 429:
                print(f"SBIR.gov API rate limited (429) - skipping")
                return []
                    
            r.raise_for_status()
            data = r.json()
            
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
                
        except Exception as e:
            print(f"SBIR.gov API failed: {e}")
            return []

    def fetch(self) -> Iterable[dict]:
        # 1. Check cache first
        cached = self._check_cache()
        if cached:
            topics = cached
        else:
            # 2. Try browser rendering (most reliable for pre-release topics)
            topics = self._fetch_with_browser_rendering()
            
            # 3. Fall back to SBIR.gov API if browser fails
            if not topics:
                print("Browser rendering returned no results, trying SBIR.gov API...")
                topics = self._fetch_from_sbir_api()
        
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
