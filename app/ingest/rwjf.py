# app/ingest/rwjf.py
import time, re, hashlib
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (contact: you@example.org)"}
BASE = "https://www.rwjf.org"

def _date_guess(s: str | None):
    if not s: return None
    s = s.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    return None

def _hash(title, url):
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update((url or "").encode("utf-8"))
    return h.hexdigest()

class RwjfIngestor(BaseIngestor):
    source = "rwjf"

    def fetch(self):
        # Active Funding Opportunities
        url = f"{BASE}/en/grants/active-funding-opportunities.html"
        print(f"Fetching RWJF opportunities from {url}")
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            print(f"✅ Successfully loaded RWJF page ({len(r.text)} chars)")
        except Exception as e:
            print(f"❌ Failed to load RWJF page: {e}")
            return
            
        soup = BeautifulSoup(r.text, "html.parser")

        # Try multiple selectors
        selectors = [
            "a[href*='/en/grants/active-funding-opportunities/']",
            "a[href*='/grants/']",
            "div[class*='opportunity'] a",
            "div[class*='grant'] a",
            "article a",
            "div[class*='card'] a",
        ]
        
        opportunities = []
        for selector in selectors:
            links = soup.select(selector)
            # Filter to only include links that go to specific grant pages (not just nav)
            filtered = [a for a in links if a.get("href") and "/active-funding-opportunities/" in a.get("href")]
            if filtered:
                print(f"Found {len(filtered)} potential opportunities using selector: {selector}")
                opportunities = filtered
                break
        
        if not opportunities:
            print("⚠️  No funding opportunity links found on RWJF page")
            print("   This is normal - they may not have active funding calls at this time")
            return

        count = 0
        for a in opportunities:
            detail = a.get("href")
            if not detail or isinstance(detail, list):
                continue
                
            # Skip navigation links
            link_text = a.get_text(strip=True).lower()
            if link_text in ["active funding opportunities", "grants", "find a grant", "back", "home"]:
                continue
                
            detail_url = BASE + detail if detail.startswith("/") else detail
            
            # Skip if it's just the main opportunities page
            if detail_url == url:
                continue

            print(f"  Fetching details from: {detail_url}")
            time.sleep(1.0)
            
            try:
                dr = requests.get(detail_url, headers=HEADERS, timeout=30)
                dr.raise_for_status()
            except Exception as e:
                print(f"    ❌ Failed to load details: {e}")
                continue
                
            ds = BeautifulSoup(dr.text, "html.parser")

            h1 = ds.find("h1")
            title = h1.get_text(strip=True) if h1 else a.get_text(strip=True)
            
            # Skip if title is too generic
            if title.lower() in ["active funding opportunities", "grants"]:
                continue
                
            # summary: first paragraph in main content
            summary = ""
            main = ds.select_one("article, .content, main")
            if main:
                p = main.find("p")
                if p:
                    summary = p.get_text(" ", strip=True)

            # try to capture close/deadline text
            text = ds.get_text(" ", strip=True)
            close = None
            for key in ["deadline", "closes", "due"]:
                m = re.search(rf"{key}[:\s]+([A-Za-z]+\s+\d{{1,2}},\s+\d{{4}})", text, flags=re.IGNORECASE)
                if m:
                    close = _date_guess(m.group(1))
                    break

            count += 1
            print(f"    ✅ Found: {title[:60]}")
            
            yield {
                "title": title or "(Untitled)",
                "summary": summary or None,
                "landing": detail_url,
                "posted_date": None,
                "close_date": close,
            }
        
        print(f"✅ RWJF: Found {count} funding opportunities")

    def normalize(self, item: dict) -> dict:
        title = item.get("title") or "(Untitled)"
        url = item.get("landing")
        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "RWJF",
            "mechanism": None,
            "category": None,
            "summary": item.get("summary"),
            "eligibility": None,
            "keywords": None,
            "posted_date": item.get("posted_date"),
            "close_date": item.get("close_date"),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": None,
            "hash": _hash(title, url),
        }
