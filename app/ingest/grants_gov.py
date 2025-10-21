import os, requests, datetime as dt, json
from typing import Iterable
from sqlalchemy.orm import Session
from dateparser import parse as dateparse

from base import BaseIngestor
from settings import settings

SAMPLE = os.path.join(os.path.dirname(__file__), "../sample_data/grantsgov_sample.json")

class GrantsGovIngestor(BaseIngestor):
    source = "grantsgov"

    def __init__(self, session: Session):
        super().__init__(session)

    def fetch(self) -> Iterable[dict]:
        if settings.offline_demo:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for row in data.get("opportunities", []):
                yield row
            return

        base = settings.grants_gov_base.rstrip("/")
        url = f"{base}/search"
        params = {
            "api_key": settings.grants_gov_api_key or "",
            "oppStatuses": "posted,forecasted",
            "startRecordNum": 0,
            "maxResults": 100,
            "sortBy": "modifiedDate|desc"
        }
        while True:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            j = r.json()
            items = j.get("oppHits", [])
            for it in items:
                yield it
            if len(items) < params["maxResults"]:
                break
            params["startRecordNum"] += params["maxResults"]

    def normalize(self, it: dict) -> dict:
        # Handle both sample and live shapes
        title = it.get("title") or it.get("OpportunityTitle")
        opp_id = it.get("opportunityNumber") or it.get("OpportunityNumber")
        agency = it.get("agency") or it.get("Agency")
        summary = it.get("summary") or it.get("Synopsis") or ""
        eligibility = it.get("eligibility") or it.get("EligibleApplicants") or ""
        mechanism = it.get("mechanism") or it.get("FundingInstrumentType") or ""
        posted = it.get("postedDate") or it.get("PostDate")
        close = it.get("closeDate") or it.get("CloseDate")
        def to_date(s):
            if not s: return None
            d = dateparse(s)
            return d.date() if d else None

        return {
            "source": self.source,
            "opportunity_id": opp_id,
            "title": title,
            "agency": agency,
            "mechanism": mechanism,
            "category": None,
            "summary": summary,
            "eligibility": eligibility,
            "keywords": None,
            "posted_date": to_date(posted),
            "close_date": to_date(close),
            "urls": {
                "landing": it.get("url") or it.get("OpportunityLink"),
                "details": it.get("url") or it.get("OpportunityLink"),
                "pdf": None
            },
            "assistance_listing": it.get("CFDANumber") or it.get("AssistanceListingNumber"),
            "raw": it
        }
