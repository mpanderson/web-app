# app/ingest/dod_sbir.py
import os, time, re, json, hashlib
from datetime import datetime, timedelta
from typing import Iterable, Any
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup

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

def _run_playwright_in_thread() -> list[dict]:
    """
    Run Playwright in a separate thread to avoid event loop conflicts.
    This is necessary because FastAPI uses AnyIO/uvicorn async context.
    """
    from playwright.sync_api import sync_playwright
    
    topics = []
    
    try:
        print("Starting Playwright browser automation...")
        with sync_playwright() as p:
            # Launch browser in headless mode
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            
            # Navigate to the DoD topics app
            print(f"Navigating to {TOPICS_APP_URL}")
            page.goto(TOPICS_APP_URL, wait_until="networkidle", timeout=60000)
            
            # Wait for the table to load (adjust selector as needed)
            page.wait_for_selector('table, [class*="topic"], tr', timeout=30000)
            
            # Wait a bit more for JavaScript to finish rendering
            page.wait_for_timeout(3000)
            
            # Get the rendered HTML
            html = page.content()
            browser.close()
            
            print(f"Page rendered successfully, parsing HTML ({len(html)} chars)")
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find all table rows
            rows = soup.select('tr')
            print(f"Found {len(rows)} table rows")
            
            for row in rows:
                text = row.get_text(' ', strip=True)
                
                # Look for topic number pattern (e.g., A254-049, CBD254-005)
                topic_match = re.search(r'([A-Z]+\d+-[A-Z]?\d+)', text)
                
                if not topic_match:
                    continue
                    
                number = topic_match.group(1)
                
                # Extract title (text before dates or status)
                title_match = re.search(r'([A-Z][^0-9\n]{20,}?)(?:\d{2}/\d{2}/\d{4}|Pre-Release|Open)', text)
                title = title_match.group(1).strip() if title_match else text[:150]
                
                # Extract dates
                dates = re.findall(r'(\d{2}/\d{2}/\d{4})', text)
                open_date = _to_date(dates[0]) if len(dates) > 0 else None
                close_date = _to_date(dates[1]) if len(dates) > 1 else None
                
                # Check status
                status = "Pre-Release" if "Pre-Release" in text else ("Open" if "Open" in text else None)
                
                # Extract component
                comp_match = re.search(r'\b(ARMY|NAVY|AIR FORCE|CBD|DARPA|MDA|SOCOM|OSD|DTRA)\b', text, re.IGNORECASE)
                component = comp_match.group(1).upper() if comp_match else None
                
                topics.append({
                    "topic_number": number,
                    "topic_title": title,
                    "topic_description": None,
                    "branch": component,
                    "program": None,
                    "release_date": open_date,
                    "close_date": close_date,
                    "status": status,
                })
            
            print(f"Extracted {len(topics)} topics from rendered page")
            return topics
            
    except Exception as e:
        print(f"Playwright automation failed: {e}")
        import traceback
        traceback.print_exc()
        return []

class DodSbirIngestor(BaseIngestor):
    """
    Ingestor for DoD SBIR/STTR topics using Playwright browser automation.
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

    def _fetch_with_playwright(self) -> list[dict]:
        """
        Use Playwright in a separate thread to avoid event loop conflicts.
        """
        try:
            # Run Playwright in a thread executor to avoid async context issues
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_playwright_in_thread)
                topics = future.result(timeout=120)  # 2 minute timeout
                
            if topics:
                self._save_cache(topics)
                print(f"Successfully fetched {len(topics)} topics via Playwright")
            return topics
            
        except Exception as e:
            print(f"Playwright thread execution failed: {e}")
            return []

    def _fetch_from_sbir_api(self) -> list[dict]:
        """
        Try SBIR.gov API with retry/backoff for rate limiting.
        """
        topics = []
        max_retries = 2
        base_delay = 5
        
        params = {
            "agency": "DOD",
            "rows": 50,
            "start": 0
        }
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = base_delay * (2 ** attempt)
                    print(f"Retrying SBIR.gov API after {delay}s...")
                    time.sleep(delay)
                
                r = requests.get(SBIR_API_URL, params=params, headers=HEADERS, timeout=40)
                
                if r.status_code == 429:
                    print(f"SBIR.gov API rate limited (attempt {attempt + 1}/{max_retries})")
                    continue
                    
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
                print(f"SBIR.gov API attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    break
                    
        return []

    def fetch(self) -> Iterable[dict]:
        # 1. Check cache first
        cached = self._check_cache()
        if cached:
            topics = cached
        else:
            # 2. Try Playwright browser automation (most reliable for pre-release topics)
            topics = self._fetch_with_playwright()
            
            # 3. Fall back to SBIR.gov API if browser fails
            if not topics:
                print("Playwright returned no results, trying SBIR.gov API...")
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
