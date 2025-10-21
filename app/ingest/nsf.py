import os, feedparser
from typing import Iterable
from sqlalchemy.orm import Session
from bs4 import BeautifulSoup
from dateparser import parse as dateparse

from .base import BaseIngestor
from settings import settings

SAMPLE = os.path.join(os.path.dirname(__file__), "../sample_data/nsf_rss.xml")
NSF_RSS = "https://www.nsf.gov/rss/rss_www_funding.xml"  # generic NSF funding feed

class NsfIngestor(BaseIngestor):
    source = "nsf"

    def __init__(self, session: Session):
        super().__init__(session)

    def fetch(self) -> Iterable[dict]:
        if settings.offline_demo:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                data = f.read()
            feed = feedparser.parse(data)
        else:
            feed = feedparser.parse(NSF_RSS)
        for e in feed.entries:
            yield dict(e)

    def normalize(self, e: dict) -> dict:
        title = e.get("title","")
        link = e.get("link")
        summary = BeautifulSoup(e.get("summary",""), "html.parser").get_text(" ", strip=True)
        posted = e.get("published") or e.get("updated")

        def to_date(s):
            if not s: return None
            d = dateparse(s)
            return d.date() if d else None

        return {
            "source": self.source,
            "opportunity_id": None,
            "title": title,
            "agency": "NSF",
            "mechanism": "",
            "category": None,
            "summary": summary,
            "eligibility": "",
            "keywords": None,
            "posted_date": to_date(posted),
            "close_date": None,
            "urls": {"landing": link, "details": link, "pdf": None},
            "assistance_listing": None,
            "raw": e
        }
