# app/ingest/dod_sbir_csv.py
import csv, hashlib
from datetime import datetime
from io import StringIO
from typing import Any

from .base import BaseIngestor

def _hash(title: str | None, url: str | None) -> str:
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8", errors="ignore"))
    h.update((url or "").encode("utf-8", errors="ignore"))
    return h.hexdigest()

def _to_date(s: Any):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

def _component_to_agency(component: str | None) -> str:
    if not component:
        return "DoD SBIR/STTR"
    return f"DoD SBIR/STTR - {component}"

class DodSbirCsvIngestor(BaseIngestor):
    """
    Ingestor for DoD SBIR/STTR topics from CSV upload.
    Expected CSV columns: Topic #, Title, Open, Close, Component, etc.
    """
    source = "dod_sbir"

    def __init__(self, session, csv_content: str):
        super().__init__(session)
        self.csv_content = csv_content

    def fetch(self):
        """Parse CSV content and yield topic dictionaries."""
        csv_file = StringIO(self.csv_content)
        reader = csv.DictReader(csv_file)
        
        for row in reader:
            # Handle various possible column names
            topic_number = (
                row.get("Topic #") or 
                row.get("Topic Number") or 
                row.get("topic_number") or
                row.get("topicNumber")
            )
            
            title = (
                row.get("Title") or 
                row.get("Topic Title") or 
                row.get("title") or
                row.get("topicTitle")
            )
            
            open_date = (
                row.get("Open") or 
                row.get("Open Date") or 
                row.get("open_date") or
                row.get("Release Date")
            )
            
            close_date = (
                row.get("Close") or 
                row.get("Close Date") or 
                row.get("close_date") or
                row.get("Due Date")
            )
            
            component = (
                row.get("Component") or 
                row.get("Branch") or 
                row.get("Service") or
                row.get("component")
            )
            
            status = (
                row.get("Status") or
                row.get("Topic Status") or
                row.get("status")
            )
            
            # Build landing URL
            landing_url = f"https://www.dodsbirsttr.mil/topics-app/#/?search={topic_number}" if topic_number else None
            
            yield {
                "topic_number": topic_number,
                "title": title,
                "open_date": open_date,
                "close_date": close_date,
                "component": component,
                "status": status,
                "landing": landing_url,
            }

    def normalize(self, item: dict) -> dict:
        title = item.get("title") or "(Untitled)"
        number = item.get("topic_number")
        comp = item.get("component")
        url = item.get("landing")
        status = item.get("status")

        return {
            "source": self.source,
            "opportunity_id": number,
            "title": title,
            "agency": _component_to_agency(comp),
            "mechanism": None,
            "category": status,
            "summary": None,
            "eligibility": None,
            "keywords": None,
            "posted_date": _to_date(item.get("open_date")),
            "close_date": _to_date(item.get("close_date")),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": {
                "component": comp,
                "status": status,
            },
            "hash": _hash(title, url),
        }
