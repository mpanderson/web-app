from pydantic import BaseModel
from datetime import date

class OpportunityOut(BaseModel):
    id: int
    source: str
    opportunity_id: str | None
    title: str | None
    agency: str | None
    mechanism: str | None
    category: str | None
    summary: str | None
    eligibility: str | None
    keywords: str | None
    posted_date: date | None
    close_date: date | None
    urls: dict | None
    assistance_listing: str | None

    class Config:
        from_attributes = True

class MatchIn(BaseModel):
    profile_text: str | None = None
    top_k: int = 20
