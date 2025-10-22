# app/ingest/dod_sbir.py
import os
import time
import hashlib
from datetime import datetime, timedelta
from typing import Iterable, Any
import requests

from .base import BaseIngestor

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: research@example.org)"}

# SAM.gov Opportunities API v2 endpoint
SAM_GOV_API_URL = "https://api.sam.gov/prod/opportunities/v2/search"

def _hash(title: str | None, opp_id: str | None) -> str:
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8", errors="ignore"))
    h.update((opp_id or "").encode("utf-8", errors="ignore"))
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
    # catch ISO-like "2025-09-01T00:00:00Z" or "20250901"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        pass
    # Try YYYYMMDD format
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").date()
    except Exception:
        pass
    return None

def _mechanism_from_text(*texts: str | None) -> str | None:
    """Detect SBIR/STTR from text fields."""
    blob = " ".join([t or "" for t in texts]).upper()
    if "STTR" in blob and "SBIR" in blob:
        return "SBIR/STTR"
    if "STTR" in blob:
        return "STTR"
    if "SBIR" in blob:
        return "SBIR"
    return None

class DodSbirIngestor(BaseIngestor):
    """
    Ingestor for DoD SBIR/STTR opportunities using the SAM.gov Opportunities API v2.
    This also captures SBIR/STTR from other federal agencies (NIH, NSF, DOE, etc.).
    """
    source = "dod_sbir"

    def _fetch_from_sam_gov(self) -> list[dict]:
        """
        Fetch SBIR/STTR opportunities from SAM.gov API.
        Searches for opportunities with 'SBIR' or 'STTR' in the title.
        """
        api_key = os.getenv("SAM_GOV_API_KEY")
        if not api_key:
            print("❌ SAM_GOV_API_KEY not found in environment variables")
            print("   Please add your SAM.gov API key to Replit Secrets")
            return []

        print("Fetching SBIR/STTR opportunities from SAM.gov API...")
        
        all_opportunities = []
        
        # SAM.gov requires date ranges
        # Query from 90 days ago to 60 days in the future to capture:
        # - Historical active opportunities
        # - Pre-release notices for upcoming opportunities (up to 2 months ahead)
        posted_from = (datetime.now() - timedelta(days=90)).strftime("%m/%d/%Y")
        posted_to = (datetime.now() + timedelta(days=60)).strftime("%m/%d/%Y")
        
        print(f"  Date range: {posted_from} to {posted_to} (includes pre-release notices)")
        
        # Fetch in batches with pagination
        limit = 100
        offset = 0
        max_pages = 20  # Fetch up to 2000 opportunities to find SBIR/STTR
        
        for page in range(max_pages):
            params = {
                "api_key": api_key,
                "limit": limit,
                "offset": offset,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "ptype": "o,s,k",  # Solicitation, Special Notice, Combined Synopsis
            }
            
            try:
                print(f"  Fetching page {page + 1} (offset {offset})...")
                r = requests.get(SAM_GOV_API_URL, params=params, headers=HEADERS, timeout=40)
                
                if r.status_code == 400:
                    print(f"❌ Bad Request (400). Error details:")
                    try:
                        error_data = r.json()
                        print(f"   {error_data}")
                    except:
                        print(f"   {r.text[:500]}")
                    return all_opportunities
                elif r.status_code == 403:
                    print(f"❌ API authentication failed (403). Check your SAM_GOV_API_KEY")
                    return all_opportunities
                elif r.status_code == 429:
                    print(f"⚠️  API rate limited (429). Waiting 10 seconds...")
                    time.sleep(10)
                    continue
                    
                r.raise_for_status()
                data = r.json()
                
                total_records = data.get("totalRecords", 0)
                opportunities = data.get("opportunitiesData", [])
                
                if page == 0:
                    print(f"  Total records available: {total_records}")
                
                if not opportunities:
                    print(f"  No more opportunities found")
                    break
                
                # Filter for SBIR/STTR opportunities
                sbir_sttr_opps = []
                for opp in opportunities:
                    title = opp.get("title", "")
                    description = opp.get("description", "")
                    
                    # Check if this is a SBIR/STTR opportunity
                    if any(keyword in title.upper() for keyword in ["SBIR", "STTR"]) or \
                       any(keyword in description.upper() for keyword in ["SBIR", "STTR"]):
                        sbir_sttr_opps.append(opp)
                
                print(f"  Found {len(sbir_sttr_opps)} SBIR/STTR opportunities in this batch")
                all_opportunities.extend(sbir_sttr_opps)
                
                # Check if we've fetched all available records
                if offset + limit >= total_records:
                    print(f"  Reached end of results")
                    break
                
                offset += limit
                time.sleep(1)  # Be nice to the API
                
            except requests.exceptions.RequestException as e:
                print(f"❌ Network error: {e}")
                break
            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                break
        
        print(f"✅ Total SBIR/STTR opportunities fetched: {len(all_opportunities)}")
        return all_opportunities

    def fetch(self) -> Iterable[dict]:
        """
        Fetch SBIR/STTR opportunities from SAM.gov.
        """
        opportunities = self._fetch_from_sam_gov()
        
        if not opportunities:
            print("⚠️  No SBIR/STTR opportunities found from SAM.gov")
            return

        # Process and yield each opportunity
        for opp in opportunities:
            notice_id = opp.get("noticeId")
            title = opp.get("title", "")
            solicitation_number = opp.get("solicitationNumber")
            department = opp.get("department", "")
            sub_tier = opp.get("subTier", "")  # Specific agency (e.g., "Air Force")
            office = opp.get("office", "")
            
            description = opp.get("description", "")
            
            posted_date = _to_date(opp.get("postedDate"))
            response_deadline = _to_date(opp.get("responseDeadLine"))
            
            opp_type = opp.get("type", "")
            active = opp.get("active", "")
            
            naics_code = opp.get("naicsCode", "")
            classification_code = opp.get("classificationCode", "")
            
            # Build URL to the opportunity on SAM.gov
            if notice_id:
                landing_url = f"https://sam.gov/opp/{notice_id}/view"
            else:
                landing_url = "https://sam.gov"
            
            # Determine agency name
            if sub_tier:
                agency = f"{department} - {sub_tier}" if department else sub_tier
            else:
                agency = department or "Federal Agency"
            
            # Detect mechanism (SBIR/STTR)
            mechanism = _mechanism_from_text(title, description, solicitation_number)
            
            yield {
                "title": title or "(Untitled)",
                "summary": description[:1000] if description else None,  # Truncate long descriptions
                "opportunity_id": solicitation_number or notice_id,
                "notice_id": notice_id,
                "agency": agency,
                "department": department,
                "sub_tier": sub_tier,
                "office": office,
                "mechanism": mechanism,
                "posted_date": posted_date,
                "close_date": response_deadline,
                "landing": landing_url,
                "status": "Active" if active == "Yes" else "Inactive",
                "type": opp_type,
                "naics_code": naics_code,
                "classification_code": classification_code,
            }

    def normalize(self, item: dict) -> dict:
        title = item.get("title") or "(Untitled)"
        opp_id = item.get("opportunity_id")
        notice_id = item.get("notice_id")
        agency = item.get("agency", "Federal Agency")
        mechanism = item.get("mechanism")
        url = item.get("landing")
        status = item.get("status")
        opp_type = item.get("type")

        return {
            "source": self.source,
            "opportunity_id": opp_id,
            "title": title,
            "agency": agency,
            "mechanism": mechanism,
            "category": f"{status} - {opp_type}" if status and opp_type else (status or opp_type),
            "summary": item.get("summary"),
            "eligibility": None,
            "keywords": None,
            "posted_date": item.get("posted_date"),
            "close_date": item.get("close_date"),
            "urls": {"landing": url, "details": url, "pdf": None},
            "assistance_listing": None,
            "raw": {
                "notice_id": notice_id,
                "department": item.get("department"),
                "sub_tier": item.get("sub_tier"),
                "office": item.get("office"),
                "naics_code": item.get("naics_code"),
                "classification_code": item.get("classification_code"),
            },
            "hash": _hash(title, opp_id or notice_id),
        }
