# app/ingest/gates.py
import time, re, hashlib
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (contact: you@example.org)"}
BASE = "https://usprogram.gatesfoundation.org"

def _hash(title, url):
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update((url or "").encode("utf-8"))
    return h.hexdigest()

class GatesIngestor(BaseIngestor):
    source = "gates"

    def fetch(self):
        url = f"{BASE}/what-we-do/funding-opportunities"
        print(f"Fetching Gates Foundation opportunities from {url}")
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            print(f"✅ Successfully loaded Gates Foundation page ({len(r.text)} chars)")
        except Exception as e:
            print(f"❌ Failed to load Gates Foundation page: {e}")
            return
        
        soup = BeautifulSoup(r.text, "html.parser")

        # Try multiple selectors to find opportunity links
        selectors = [
            "a[href*='/what-we-do/funding-opportunities/']",
            "a[href*='funding']",
            "div[class*='opportunity'] a",
            "div[class*='grant'] a",
            "article a",
        ]
        
        opportunities = []
        for selector in selectors:
            links = soup.select(selector)
            if links:
                print(f"Found {len(links)} potential opportunities using selector: {selector}")
                opportunities = links
                break
        
        if not opportunities:
            print("⚠️  No funding opportunity links found on Gates Foundation page")
            print("   This is normal - they may not have active RFPs at this time")
            return

        count = 0
        for a in opportunities:
            href = a.get("href")
            if not href or isinstance(href, list):
                continue
                
            # Skip navigation/header links
            link_text = a.get_text(strip=True).lower()
            if link_text in ["funding opportunities", "grants", "home", "back"]:
                continue
                
            detail = href if href.startswith("http") else BASE + href
            
            # Skip if it's just the main opportunities page
            if detail == url:
                continue

            print(f"  Fetching details from: {detail}")
            time.sleep(1.0)
            
            try:
                dr = requests.get(detail, headers=HEADERS, timeout=30)
                dr.raise_for_status()
            except Exception as e:
                print(f"    ❌ Failed to load details: {e}")
                continue

            ds = BeautifulSoup(dr.text, "html.parser")
            h1 = ds.find("h1")
            title = h1.get_text(strip=True) if h1 else a.get_text(strip=True)
            
            # Skip if title is too generic
            if title.lower() in ["funding opportunities", "grants"]:
                continue
                
            # summary: first paragraph
            main = ds.select_one("article, .content, main")
            p = main.find("p") if main else None
            summary = p.get_text(" ", strip=True) if p else None

            # Gates often has RFPs with short windows; dates may not always be on page
            close = None

            count += 1
            print(f"    ✅ Found: {title[:60]}")
            
            yield {
                "title": title or "(Untitled)",
                "summary": summary,
                "landing": detail,
                "posted_date": None,
                "close_date": close,
            }
        
        print(f"✅ Gates Foundation: Found {count} funding opportunities")

    def normalize(self, item: dict) -> dict:
        url = item.get("landing")
        title = item.get("title") or "(Untitled)"
        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "Bill & Melinda Gates Foundation",
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
