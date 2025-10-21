import os
import time
import re
import hashlib
import datetime
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

from sqlalchemy.orm import Session
from base import BaseIngestor

UA = "rfa-matcher/1.0 (+https://example.org; polite crawler)"
BASE_URL = os.getenv("NIH_GUIDE_BASE_URL", "https://grants.nih.gov/funding/nih-guide-for-grants-and-contracts")
MAX_PAGES = int(os.getenv("NIH_GUIDE_MAX_PAGES", "40"))
DELAY = float(os.getenv("NIH_GUIDE_DELAY_SECS", "1.5"))
TIMEOUT = int(os.getenv("NIH_GUIDE_TIMEOUT_SECS", "20"))

def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p:
            h.update(p.encode("utf-8", errors="ignore"))
    return h.hexdigest()

def _allow_url(url: str) -> bool:
    # robots.txt polite check
    rp = robotparser.RobotFileParser()
    root = f"{urlparse(BASE_URL).scheme}://{urlparse(BASE_URL).netloc}"
    rp.set_url(urljoin(root, "/robots.txt"))
    try:
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        # If robots can’t be read, be conservative but proceed (site often allows Guide pages)
        return True

def _req(url: str, session: requests.Session, backoff_attempts: int = 4) -> requests.Response:
    delay = DELAY
    for i in range(backoff_attempts):
        resp = session.get(url, timeout=TIMEOUT)
        if resp.status_code in (429, 503):
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    # last try (will raise if still 429/503)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp

def _parse_date(text: str) -> Optional[datetime.date]:
    # NIH Guide usually shows dates like "Posted: July 10, 2025"
    # Try several patterns:
    for pat in [
        r"Posted:\s*([A-Za-z]+ \d{1,2}, \d{4})",
        r"(\w+\s+\d{1,2},\s+\d{4})",
        r"(\d{4}-\d{2}-\d{2})"
    ]:
        m = re.search(pat, text)
        if m:
            s = m.group(1)
            try:
                # Try Month DD, YYYY
                return datetime.datetime.strptime(s, "%B %d, %Y").date()
            except Exception:
                try:
                    return datetime.date.fromisoformat(s)
                except Exception:
                    continue
    return None

class NihGuideSearchIngestor(BaseIngestor):
    """
    Crawls the NIH Guide search listing, following pagination, and yields dicts.
    Later you can deepen: visit each detail page to extract mechanism/ICs.
    """
    source = "nih_full"

    def __init__(self, session: Session):
        super().__init__(session)
        self.session_http = requests.Session()
        self.session_http.headers.update({"User-Agent": UA})

    def fetch(self) -> Iterable[dict]:
        if not _allow_url(BASE_URL):
            raise RuntimeError("Blocked by robots.txt, aborting NIH Guide crawl.")
        url = BASE_URL
        pages = 0

        while url and pages < MAX_PAGES:
            resp = _req(url, self.session_http)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Heuristic: find result cards/rows with titles linking to Guide detail pages
            # Adjust selectors as needed if NIH tweaks markup.
            items = []
            for a in soup.select("a"):
                href = a.get("href") or ""
                if "/grants/guide/" in href.lower():
                    # Find the enclosing block for summary/date (siblings/parent text)
                    title = a.get_text(strip=True)
                    landing = urljoin(url, href)
                    block = a.find_parent(["article", "li", "div"]) or a
                    text = " ".join(block.get_text(" ", strip=True).split())
                    posted = _parse_date(text)

                    # Deduplicate by title+url on the page
                    items.append({
                        "source": self.source,
                        "title": title or "(Untitled)",
                        "summary": text[:4000] if text else None,
                        "agency": "NIH",
                        "mechanism": "",        # can be filled by detail-page parsing later
                        "urls": {"landing": landing, "details": landing, "pdf": None},
                        "posted_date": posted,
                        "close_date": None,
                        "assistance_listing": None,
                        "eligibility": None,
                        "keywords": None,
                        "raw": None,
                        # hash computed in base if not provided
                    })

            # Yield unique per page (same link may appear multiple times in bread crumbs, etc.)
            seen = set()
            for it in items:
                k = (it["title"], it["urls"]["landing"])
                if k in seen:
                    continue
                seen.add(k)
                yield it

            pages += 1
            time.sleep(DELAY)

            # Find the "next" pagination link heuristically
            next_link = None
            # Try rel or aria labels
            candidate = soup.select_one("a[rel='next']") or soup.select_one("a[aria-label*='Next'], a:contains('Next')")
            if candidate and candidate.get("href"):
                next_link = urljoin(url, candidate["href"])

            # Fallback: look for a link with text "Next" (case-insensitive)
            if not next_link:
                for a2 in soup.select("a"):
                    if a2.get_text(strip=True).lower() in ("next", "next ›", "›", "»"):
                        if a2.get("href"):
                            next_link = urljoin(url, a2["href"])
                            break

            url = next_link

    # We can let BaseIngestor coerce dict -> Opportunity (it computes hash, normalizes dates).
    # If you prefer, you could implement normalize() that returns Opportunity directly.
