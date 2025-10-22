# app/ingest/dod_sbir.py
import os, time, re, json, hashlib
from datetime import datetime
from typing import Iterable, Any

import requests
from bs4 import BeautifulSoup

from .base import BaseIngestor
from models import Opportunity

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: you@example.org)"}

# You can set this in .env if you discover the definitive JSON endpoint:
# e.g., DOD_SBIR_TOPICS_API=https://www.dodsbirsttr.mil/api/topics
API_CANDIDATES = [
    lambda: os.getenv("DOD_SBR_TOPICS_API") or os.getenv("DOD_SBIR_TOPICS_API"),
    lambda: "https://www.dodsbirsttr.mil/topics-app/api/topics",
    lambda: "https://www.dodsbirsttr.mil/api/topics",
]

TOPICS_APP_URL = "https://www.dodsbirsttr.mil/topics-app/"

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
    # catch ISO-like "2025-09-01T00:00:00Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

def _mechanism_from_text(*texts: str | None) -> str | None:
    blob = " ".join([t or "" for t in texts]).upper()
    if "STTR" in blob and "SBIR" in blob:
        return "SBIR/STTR"
    if "STTR" in blob:
        return "STTR"
    if "SBIR" in blob:
        return "SBIR"
    return None

def _component_to_agency(component: str | None) -> str:
    # Present it as a sub-agency flavor for clarity in your table
    if not component:
        return "DoD SBIR/STTR"
    return f"DoD SBIR/STTR - {component}"

class DodSbirIngestor(BaseIngestor):
    """
    Ingestor for DoD SBIR/STTR topics. It will attempt:
     1) JSON API (env or defaults)
     2) Fallback: parse topics-app page for embedded JSON
    Normalize to your Opportunity schema for uniform matching.
    """
    source = "dod_sbir"

    def _fetch_from_api(self) -> list[dict]:
        for candidate_fn in API_CANDIDATES:
            url = candidate_fn()
            if not url:
                continue
            try:
                r = requests.get(url, headers=HEADERS, timeout=40)
                if r.status_code != 200:
                    continue
                data = r.json()
                # Shape can vary; try to find an array of topics
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    for key in ("topics", "data", "results", "items"):
                        if key in data and isinstance(data[key], list):
                            return data[key]
                # Sometimes paginated: { data: { topics: [] } }
                if "data" in data and isinstance(data["data"], dict):
                    inner = data["data"]
                    for key in ("topics", "items", "results"):
                        if key in inner and isinstance(inner[key], list):
                            return inner[key]
            except Exception:
                continue
        return []

    def _fetch_from_html(self) -> list[dict]:
        try:
            r = requests.get(TOPICS_APP_URL, headers=HEADERS, timeout=40)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            # Look for a <script> containing JSON – common in SPAs:
            # Try a few patterns
            scripts = soup.find_all("script")
            for sc in scripts:
                txt = sc.string or sc.get_text() or ""
                # Heuristics: look for "topics" array JSON
                if '"topics"' in txt or '"topicNumber"' in txt or '"topicTitle"' in txt:
                    # extract a JSON substring if possible
                    m = re.search(r"(\{.*\"topics\".*\})", txt, flags=re.DOTALL)
                    if m:
                        try:
                            obj = json.loads(m.group(1))
                            # Try to locate list within obj
                            for path in (
                                ["topics"],
                                ["data","topics"],
                                ["data","items"],
                                ["items"],
                                ["results"],
                            ):
                                node = obj
                                ok = True
                                for k in path:
                                    if isinstance(node, dict) and k in node:
                                        node = node[k]
                                    else:
                                        ok = False
                                        break
                                if ok and isinstance(node, list):
                                    return node
                        except Exception:
                            pass
            return []
        except Exception:
            return []

    def fetch(self) -> Iterable[dict]:
        topics = self._fetch_from_api()
        if not topics:
            topics = self._fetch_from_html()

        # Each topic should contain identifiers and some text
        for t in topics:
            # Common fields observed on DoD topic APIs (names vary by release):
            # topicNumber, topicTitle, component, description, solicitationTitle,
            # openDate, closeDate, url/detailLink/topicId etc.
            number = t.get("topicNumber") or t.get("number") or t.get("topic") or t.get("id")
            title  = t.get("topicTitle")  or t.get("title")
            comp   = t.get("component")   or t.get("service") or t.get("org") or None
            desc   = t.get("description") or t.get("synopsis") or t.get("summary") or None
            open_d = _to_date(t.get("openDate") or t.get("open_date") or t.get("postedDate"))
            close_d= _to_date(t.get("closeDate") or t.get("close_date") or t.get("dueDate"))
            soln   = t.get("solicitationTitle") or t.get("solicitation") or ""

            # Best-effort mechanism extraction (SBIR vs STTR)
            mech = _mechanism_from_text(soln, title, desc)

            # Build a details URL that reliably filters to this topic number in the SPA:
            # The site usually supports a query filter in the hash route; use a generic search link:
            details_url = f"{TOPICS_APP_URL}#/?search={number}" if number else TOPICS_APP_URL

            yield {
                "title": title or (number or "(Untitled)"),
                "summary": desc,
                "opportunity_number": number,
                "component": comp,
                "mechanism": mech,
                "posted_date": open_d,
                "close_date": close_d,
                "landing": details_url,
            }

    def normalize(self, raw: dict) -> dict:
        title = raw.get("title") or "(Untitled)"
        number = raw.get("opportunity_number")
        comp = raw.get("component")
        mech = raw.get("mechanism")
        url = raw.get("landing")

        return {
            "source": self.source,
            "opportunity_id": number,         # put topic number in opp_id for your table's "Opp #" column
            "title": title,
            "agency": _component_to_agency(comp),
            "mechanism": mech,
            "category": None,
            "summary": raw.get("summary"),
            "eligibility": None,
            "keywords": None,
            "posted_date": raw.get("posted_date"),
            "close_date": raw.get("close_date"),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": {
                "component": comp,
                "solicitation_or_program": raw.get("mechanism"),
            },
            "hash": _hash(title, url),
        }
