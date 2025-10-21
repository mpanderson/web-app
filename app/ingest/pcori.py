# app/ingest/pcori.py
import time, re, hashlib
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (contact: you@example.org)"}
BASE = "https://www.pcori.org"

def _date_guess(s: str | None):
    if not s: return None
    s = s.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try: return datetime.strptime(s, fmt).date()
        except Exception: pass
    m = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", s)
    if m:
        try: return datetime.strptime(m.group(1), "%B %d, %Y").date()
        except Exception: pass
    return None

def _hash(title, url):
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update((url or "").encode("utf-8"))
    return h.hexdigest()

class PcoriIngestor(BaseIngestor):
    source = "pcori"

    def fetch(self):
        # PCORI funding list (open opportunities are typically listed)
        url = f"{BASE}/funding-opportunities"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # cards/teasers:
        for a in soup.select("a[href*='/funding-opportunities/announcement/']"):
            detail_url = BASE + a.get("href") if a.get("href","").startswith("/") else a.get("href")
            # Throttle politely
            time.sleep(1.0)
            try:
                dr = requests.get(detail_url, headers=HEADERS, timeout=30)
                dr.raise_for_status()
            except Exception:
                continue
            ds = BeautifulSoup(dr.text, "html.parser")

            title = (ds.find("h1").get_text(strip=True) if ds.find("h1") else a.get_text(strip=True)) or ""
            # summary/snippet: first paragraph in content region
            summary = ""
            main = ds.select_one("article, .region-content, .content, main")
            if main:
                p = main.find("p")
                if p:
                    summary = p.get_text(" ", strip=True)

            # dates (PCORI shows LOI, application, etc.; grab anything resembling a close/deadline)
            text = ds.get_text(" ", strip=True)
            close = None
            for key in ["LOI Due", "Application Due", "Closes", "Deadline"]:
                m = re.search(rf"{key}[:\s]+([A-Za-z]+\s+\d{{1,2}},\s+\d{{4}})", text)
                if m:
                    close = _date_guess(m.group(1))
                    if close: break

            # posted—best-effort (may be missing)
            posted = None
            m2 = re.search(r"Posted[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})", text)
            if m2:
                posted = _date_guess(m2.group(1))

            yield {
                "title": title,
                "summary": summary or None,
                "landing": detail_url,
                "posted_date": posted,
                "close_date": close,
            }

    def normalize(self, raw: dict) -> Opportunity:
        title = raw.get("title") or "(Untitled)"
        url = raw.get("landing")
        opp = {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "PCORI",
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
        return opp
