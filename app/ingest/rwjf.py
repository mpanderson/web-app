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
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a[href*='/en/grants/active-funding-opportunities/']"):
            detail = a.get("href")
            if not detail: continue
            detail_url = BASE + detail if detail.startswith("/") else detail

            time.sleep(1.0)
            try:
                dr = requests.get(detail_url, headers=HEADERS, timeout=30)
                dr.raise_for_status()
            except Exception:
                continue
            ds = BeautifulSoup(dr.text, "html.parser")

            title = ds.find("h1").get_text(strip=True) if ds.find("h1") else a.get_text(strip=True)
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

            yield {
                "title": title or "(Untitled)",
                "summary": summary or None,
                "landing": detail_url,
                "posted_date": None,
                "close_date": close,
            }

    def normalize(self, raw: dict) -> dict:
        title = raw.get("title") or "(Untitled)"
        url = raw.get("landing")
        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "RWJF",
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
