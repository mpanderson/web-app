import os, json, requests, feedparser
from typing import Iterable
from sqlalchemy.orm import Session
from bs4 import BeautifulSoup
from dateparser import parse as dateparse

from .base import BaseIngestor
from ..settings import settings

SAMPLE = os.path.join(os.path.dirname(__file__), "../sample_data/nih_rss.xml")
NIH_RSS = "https://grants.nih.gov/grants/guide/newsfeed/fundingopps.xml"

class NihGuideIngestor(BaseIngestor):
    source = "nih"

    def __init__(self, session: Session):
        super().__init__(session)

    def fetch(self) -> Iterable[dict]:
        if settings.offline_demo:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                data = f.read()
            feed = feedparser.parse(data)
        else:
            feed = feedparser.parse(NIH_RSS)

        for e in feed.entries:
            # Pull the article page for richer details (best-effort)
            html = ""
            if e.get("link"):
                try:
                    r = requests.get(e.link, timeout=30)
                    html = r.text
                except Exception:
                    pass
            yield {"entry": dict(e), "html": html}

    def normalize(self, it: dict) -> dict:
        e = it["entry"]
        title = e.get("title", "")
        link = e.get("link")
        summary = BeautifulSoup(e.get("summary",""), "html.parser").get_text(" ", strip=True)
        posted = e.get("published") or e.get("updated")

        # Best-effort parse from page
        mechanism = ""
        agency = "NIH"
        eligibility = ""
        close_date = None
        if it["html"]:
            soup = BeautifulSoup(it["html"], "html.parser")
            # NIH Guide pages have metadata blocks; grab first <p> text as synopsis fallback
            if not summary:
                p = soup.find("p")
                if p:
                    summary = p.get_text(" ", strip=True)

        def to_date(s):
            if not s: return None
            d = dateparse(s)
            return d.date() if d else None

        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": agency,
            "mechanism": mechanism,
            "category": None,
            "summary": summary,
            "eligibility": eligibility,
            "keywords": None,
            "posted_date": to_date(posted),
            "close_date": close_date,
            "urls": {"landing": link, "details": link, "pdf": None},
            "assistance_listing": None,
            "raw": it
        }
