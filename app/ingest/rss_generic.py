import datetime
import hashlib
from typing import Iterable
import requests
import feedparser
from sqlalchemy.orm import Session

from .base import BaseIngestor
from models import Opportunity   # <-- add this

def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p:
            h.update(p.encode("utf-8", errors="ignore"))
    return h.hexdigest()

class GenericRSSIngestor(BaseIngestor):
    """
    Minimal RSS ingestor.
    Configure per-source with:
      - name (e.g., "darpa")
      - rss_url
      - default_agency (e.g., "DARPA")
      - source_tag (stored in Opportunity.source)
    """
    def __init__(self, session: Session, *, name: str, rss_url: str, default_agency: str, source_tag: str):
        super().__init__(session)
        self.name = name
        self.rss_url = rss_url
        self.default_agency = default_agency
        self.source_tag = source_tag

    def fetch(self) -> Iterable[dict]:
        # Pull RSS
        r = requests.get(self.rss_url, timeout=30)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            title = getattr(e, "title", "") or ""
            summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
            link = getattr(e, "link", None)
            published = None
            for k in ("published_parsed", "updated_parsed"):
                if getattr(e, k, None):
                    published = datetime.date(*getattr(e, k)[:3])
                    break
            yield {
                "title": title.strip(),
                "summary": summary.strip(),
                "landing": link,
                "posted_date": published,
            }

    def _json_safe(obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: _json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_json_safe(v) for v in obj]
        return obj

    def normalize(self, raw: dict) -> Opportunity:
        title = raw.get("title") or "(Untitled)"
        summary = raw.get("summary") or None
        landing = raw.get("landing")
        posted_date = raw.get("posted_date")          # this is a date (fine for Date column)
        uid = _hash(self.source_tag, title, summary or "", landing or "")

        return Opportunity(
            source=self.source_tag,
            opportunity_id=None,
            title=title[:512],
            agency=self.default_agency,
            mechanism="",
            category=None,
            summary=summary[:4000] if summary else None,
            eligibility=None,
            keywords=None,
            posted_date=posted_date,                   # OK: Date column
            close_date=None,
            urls={"landing": landing, "details": landing, "pdf": None},
            assistance_listing=None,
            raw=_json_safe(raw),                       # <- make it JSON serializable
            hash=uid,
        )

