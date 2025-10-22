# app/ingest/grants_gov.py
import hashlib, time
from datetime import datetime
from typing import Iterable, Any
import requests

from .base import BaseIngestor

HEADERS = {"User-Agent": "RFA-Matcher/1.0 (+contact: research@example.org)"}

# Simpler.Grants.gov API endpoint
API_BASE_URL = "https://api.simpler.grants.gov"
SEARCH_ENDPOINT = f"{API_BASE_URL}/v1/opportunities/search"

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
    # ISO format
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

class GrantsGovIngestor(BaseIngestor):
    """
    Ingestor for Grants.gov using the Simpler.Grants.gov API.
    Fetches federal grant opportunities programmatically.
    No API key required for basic searches.
    """
    source = "grants_gov"

    def fetch(self) -> Iterable[dict]:
        """
        Fetch grant opportunities from Simpler.Grants.gov API.
        Uses pagination to fetch all available opportunities.
        """
        max_retries = 3
        page = 1
        page_size = 50  # Fetch 50 opportunities per request
        total_fetched = 0
        
        while True:
            for attempt in range(max_retries):
                try:
                    # Build search request
                    payload = {
                        "pagination": {
                            "page_offset": page,
                            "page_size": page_size,
                            "order_by": "opportunity_id",
                            "sort_direction": "descending"
                        },
                        "filters": {
                            # Fetch posted and forecasted opportunities
                            "opportunity_status": {
                                "one_of": ["forecasted", "posted"]
                            }
                        }
                    }
                    
                    print(f"Fetching Grants.gov opportunities (page {page}, size {page_size})...")
                    
                    response = requests.post(
                        SEARCH_ENDPOINT,
                        json=payload,
                        headers=HEADERS,
                        timeout=30
                    )
                    
                    if response.status_code == 429:
                        print(f"⚠️  Rate limited. Waiting before retry {attempt + 1}/{max_retries}...")
                        time.sleep(10 * (2 ** attempt))
                        continue
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    # Extract opportunities from response
                    opportunities = data.get("data", [])
                    pagination_info = data.get("pagination_info", {})
                    
                    if not opportunities:
                        print(f"✅ No more opportunities found. Total fetched: {total_fetched}")
                        return
                    
                    print(f"✅ Retrieved {len(opportunities)} opportunities from page {page}")
                    
                    for opp in opportunities:
                        yield self._parse_opportunity(opp)
                        total_fetched += 1
                    
                    # Check if there are more pages
                    total_pages = pagination_info.get("total_pages", 1)
                    if page >= total_pages:
                        print(f"✅ Reached last page. Total fetched: {total_fetched}")
                        return
                    
                    # Move to next page
                    page += 1
                    time.sleep(0.5)  # Rate limiting courtesy delay
                    break  # Success, exit retry loop
                    
                except requests.exceptions.RequestException as e:
                    print(f"❌ API request failed (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        print(f"Failed to fetch Grants.gov data after {max_retries} attempts")
                        return
                    time.sleep(5 * (2 ** attempt))
                except Exception as e:
                    print(f"❌ Unexpected error: {e}")
                    return

    def _parse_opportunity(self, opp: dict) -> dict:
        """
        Parse a single opportunity from the API response.
        Converts API format to our internal format.
        """
        # Extract key fields from the API response
        opportunity_id = opp.get("opportunity_id")
        opportunity_number = opp.get("opportunity_number")
        opportunity_title = opp.get("opportunity_title")
        agency = opp.get("agency")
        category = opp.get("opportunity_category")
        
        # Get summary/synopsis
        summary_obj = opp.get("summary", {})
        if isinstance(summary_obj, dict):
            summary_text = summary_obj.get("summary_description")
        else:
            summary_text = str(summary_obj) if summary_obj else None
        
        # Dates
        posted_date = _to_date(opp.get("post_date") or opp.get("posted_date"))
        close_date = _to_date(opp.get("close_date") or opp.get("application_deadline"))
        
        # Funding details
        award_floor = opp.get("award_floor")
        award_ceiling = opp.get("award_ceiling")
        estimated_funding = opp.get("estimated_total_program_funding")
        expected_awards = opp.get("expected_number_of_awards")
        
        # Assistance listings (CFDA numbers)
        assistance_listings = opp.get("assistance_listings", [])
        assistance_listing_numbers = [al.get("program_number") for al in assistance_listings if al.get("program_number")]
        
        # Applicant types (eligibility)
        applicant_types = opp.get("applicant_types", [])
        eligibility_text = ", ".join([at.get("applicant_type") for at in applicant_types if at.get("applicant_type")]) if applicant_types else None
        
        # URLs
        details_url = f"https://www.grants.gov/search-results-detail/{opportunity_id}" if opportunity_id else None
        
        return {
            "opportunity_id": opportunity_id,
            "opportunity_number": opportunity_number,
            "title": opportunity_title or "(Untitled)",
            "agency": agency,
            "category": category,
            "summary": summary_text,
            "eligibility": eligibility_text,
            "posted_date": posted_date,
            "close_date": close_date,
            "award_floor": award_floor,
            "award_ceiling": award_ceiling,
            "estimated_funding": estimated_funding,
            "expected_awards": expected_awards,
            "assistance_listings": assistance_listing_numbers,
            "details_url": details_url,
            "raw_data": opp,  # Store full API response for reference
        }

    def normalize(self, item: dict) -> dict:
        """Normalize the parsed opportunity to our database schema."""
        title = item.get("title") or "(Untitled)"
        opp_id = item.get("opportunity_id")
        opp_number = item.get("opportunity_number")
        
        return {
            "source": self.source,
            "opportunity_id": opp_number or opp_id,
            "title": title,
            "agency": item.get("agency"),
            "mechanism": None,  # Can be inferred from funding_instrument in raw_data
            "category": item.get("category"),
            "summary": item.get("summary"),
            "eligibility": item.get("eligibility"),
            "keywords": None,
            "posted_date": item.get("posted_date"),
            "close_date": item.get("close_date"),
            "urls": {
                "landing": item.get("details_url"),
                "details": item.get("details_url"),
                "pdf": None
            },
            "assistance_listing": ", ".join(item.get("assistance_listings", [])) if item.get("assistance_listings") else None,
            "raw": {
                "award_floor": item.get("award_floor"),
                "award_ceiling": item.get("award_ceiling"),
                "estimated_total_program_funding": item.get("estimated_funding"),
                "expected_number_of_awards": item.get("expected_awards"),
                "full_api_response": item.get("raw_data"),
            },
            "hash": _hash(title, opp_id or opp_number),
        }
