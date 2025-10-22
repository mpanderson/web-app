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
    import hashlib
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update((url or "").encode("utf-8"))
    return h.hexdigest()

class GatesIngestor(BaseIngestor):
    source = "gates"

    def fetch(self):
        url = f"{BASE}/what-we-do/funding-opportunities"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Each funding card links to a detail page
        for a in soup.select("a[href*='/what-we-do/funding-opportunities/']"):
            href = a.get("href")
            if not href: continue
            detail = href if href.startswith("http") else BASE + href

            time.sleep(1.0)
            try:
                dr = requests.get(detail, headers=HEADERS, timeout=30)
                dr.raise_for_status()
            except Exception:
                continue

            ds = BeautifulSoup(dr.text, "html.parser")
            title = ds.find("h1").get_text(strip=True) if ds.find("h1") else a.get_text(strip=True)
            # summary: first paragraph
            main = ds.select_one("article, .content, main")
            summary = (main.find("p").get_text(" ", strip=True) if main and main.find("p") else None)

            # Gates often has RFPs with short windows; dates may not always be on page
            close = None

            yield {
                "title": title or "(Untitled)",
                "summary": summary,
                "landing": detail,
                "posted_date": None,
                "close_date": close,
            }

    def normalize(self, raw: dict) -> dict:
        url = raw.get("landing")
        title = raw.get("title") or "(Untitled)"
        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "Bill & Melinda Gates Foundation",
            "mechanism": None,
            "category": None,
            "summary": raw.get("summary"),
            "eligibility": None,
            "keywords": None,
            "posted_date": raw.get("posted_date"),
            "close_date": raw.get("close_date"),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": None,
            "hash": _hash(title, url),
        }
