import os, hashlib, datetime, requests, feedparser
from sqlalchemy.orm import Session
from typing import Iterable
from base import BaseIngestor
from models import Opportunity

def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p:
            h.update(p.encode("utf-8", errors="ignore"))
    return h.hexdigest()

class DarpaIngestor(BaseIngestor):
    source = "darpa"
    RSS_URL = os.getenv("DARPA_RSS_URL", "https://www.darpa.mil/rss/opportunities.xml")

    def __init__(self, session: Session):
        super().__init__(session)

    def fetch(self) -> Iterable[dict]:
        r = requests.get(self.RSS_URL, timeout=30)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            title = getattr(e, "title", "") or ""
            summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
            link = getattr(e, "link", None)
            posted_date = None
            for k in ("published_parsed", "updated_parsed"):
                t = getattr(e, k, None)
                if t:
                    posted_date = datetime.date(*t[:3])
                    break
            yield {
                "title": title.strip(),
                "summary": summary.strip(),
                "landing": link,
                "posted_date": posted_date,
            }

    def normalize(self, raw: dict) -> Opportunity:
        title = raw.get("title") or "(Untitled)"
        summary = raw.get("summary") or None
        landing = raw.get("landing")
        posted_date = raw.get("posted_date")  # datetime.date or None
        uid = _hash(self.source, title, summary or "", landing or "")

        return Opportunity(
            source=self.source,
            opportunity_id=None,
            title=title[:512],
            agency="DARPA",
            mechanism="",
            category=None,
            summary=summary[:4000] if summary else None,
            eligibility=None,
            keywords=None,
            posted_date=posted_date,
            close_date=None,
            urls={"landing": landing, "details": landing, "pdf": None},
            assistance_listing=None,
            raw=None,   # keep JSON-safe; avoid dates in raw
            hash=uid,
        )
