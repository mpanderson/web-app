from abc import ABC, abstractmethod
from typing import Iterable, Union, Optional
from datetime import date, datetime

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from models import Opportunity
from utils.text import content_hash  # if you have this helper; else you can use hashlib

def _to_date(maybe) -> Optional[date]:
    if maybe is None:
        return None
    if isinstance(maybe, date):
        return maybe
    if isinstance(maybe, datetime):
        return maybe.date()
    # try ISO string
    try:
        return datetime.fromisoformat(maybe).date()
    except Exception:
        return None

def _coerce_to_opportunity(rec: Union[Opportunity, dict]) -> Opportunity:
    if isinstance(rec, Opportunity):
        # ensure dates are date objects
        rec.posted_date = _to_date(rec.posted_date)
        rec.close_date = _to_date(rec.close_date)
        # ensure hash exists
        if not getattr(rec, "hash", None):
            rec.hash = content_hash(rec.title or "", rec.summary or "", rec.eligibility or "")
        return rec

    # dict path: normalize keys and build Opportunity
    data = dict(rec)
    data["posted_date"] = _to_date(data.get("posted_date"))
    data["close_date"]  = _to_date(data.get("close_date"))

    # compute hash if missing
    if not data.get("hash"):
        data["hash"] = content_hash(
            data.get("title", "") or "",
            data.get("summary", "") or "",
            data.get("eligibility", "") or ""
        )

    return Opportunity(**data)

class BaseIngestor(ABC):
    source = "base"

    def __init__(self, session: Session):
        self.session = session

    @abstractmethod
    def fetch(self) -> Iterable[dict]:
        """
        Return raw items (dicts) from the source.
        """
        ...

    def normalize(self, item: dict) -> Union[Opportunity, dict]:
        """
        Default passthrough: subclasses may return dicts OR Opportunity.
        """
        return item

    def upsert(self, record: Union[Opportunity, dict]) -> Opportunity:
        opp = _coerce_to_opportunity(record)
        if not opp.hash:
            raise ValueError("hash required for deduplication")

        existing = self.session.query(Opportunity).filter_by(hash=opp.hash).one_or_none()
        if existing:
            # update selected fields (keep id/hash)
            for attr in [
                "source","opportunity_id","title","agency","mechanism","category",
                "summary","eligibility","keywords","posted_date","close_date",
                "urls","assistance_listing","raw"
            ]:
                setattr(existing, attr, getattr(opp, attr))
            self.session.add(existing)
            return existing
        else:
            self.session.add(opp)
            return opp

    def run(self) -> int:
        count = 0
        for raw in self.fetch():
            rec = self.normalize(raw)   # dict or Opportunity
            self.upsert(rec)
            count += 1
        self.session.commit()
        return count
