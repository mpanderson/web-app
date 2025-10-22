# app/main.py
from __future__ import annotations

import io
import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from db import SessionLocal
from models import Opportunity
from schemas import OpportunityOut
from scripts.init_db import main as init_db_main
from ingest import REGISTRY
from match.profile import extract_profile_text
from match.matcher import match_opportunities
from match.vectorstore import reindex
from scheduler import start_scheduler, stop_scheduler, get_scheduler_status

# (LLM re-rank – safely falls back if quota/key is missing)
from rerank.explainer import llm_rerank, RerankItem


app = FastAPI(title="RFA Matcher MVP")


# ---------- Startup / Shutdown ----------

@app.on_event("startup")
def startup():
    init_db_main()
    # Start the background scheduler for twice-daily ingestion
    start_scheduler()

@app.on_event("shutdown")
def shutdown():
    # Clean up scheduler on app shutdown
    stop_scheduler()


# ---------- Utilities ----------

def _to_date(s):
    """Flexible date parser -> datetime.date | None."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    # Pandas fallback
    try:
        x = pd.to_datetime(s, errors="coerce")
        return x.date() if pd.notna(x) else None
    except Exception:
        return None


def _hash3(a, b, c):
    """Stable hash from 3 fields."""
    import hashlib
    h = hashlib.sha256()
    for x in (a or "", b or "", c or ""):
        h.update(str(x).encode("utf-8", errors="ignore"))
    return h.hexdigest()


# ---------- Root / Health ----------

@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "name": "RFA Matcher MVP",
        "version": "1.0",
        "endpoints": {
            "health": "/health",
            "browse_opportunities": "/opportunities",
            "ingest_source": "/ingest/run?source={pcori|gates|rwjf|dod_sbir}",
            "match_profile": "/match (POST)",
            "reindex": "/match/reindex (POST)",
            "api_docs": "/docs"
        }
    }

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Scheduler Status ----------

@app.get("/scheduler/status")
def scheduler_status():
    """Get the current scheduler status and upcoming jobs."""
    return get_scheduler_status()


# ---------- Browse ----------

@app.get("/opportunities")
def list_opportunities(limit: int = 20, offset: int = 0):
    s: Session = SessionLocal()
    try:
        q = s.query(Opportunity).order_by(Opportunity.id.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        out = [OpportunityOut.model_validate(r).model_dump() for r in rows]
        return {"total": total, "items": out}
    finally:
        s.close()


# ---------- Ingest (live ingestors) ----------

@app.post("/ingest/run")
def ingest_run(source: str):
    if source not in REGISTRY:
        raise HTTPException(400, f"Unknown source: {source}. Choose from: {list(REGISTRY.keys())}")
    s: Session = SessionLocal()
    try:
        cnt = REGISTRY[source](s).run()
        reindex(s)
        return {"ingested": cnt, "source": source}
    finally:
        s.close()


# ---------- Ingest (CSV uploads) ----------

# NIH / NSF exports
@app.post("/ingest/csv")
def ingest_csv(
    source_name: str = Form(...),   # "nih_export" or "nsf_export"
    file: UploadFile = File(...)
):
    from ingest.base import _coerce_to_opportunity  # reuse your helper

    s = SessionLocal()
    try:
        raw = file.file.read()
        try:
            df = pd.read_csv(io.BytesIO(raw), engine="python")
        except Exception:
            df = pd.read_excel(io.BytesIO(raw))

        ingested = 0

        if source_name == "nih_export":
            # Expected: Title, Release_Date, Expired_Date, Activity..., Document_Number, Document_Type, URL
            for _, r in df.iterrows():
                title = (r.get("Title") or "").strip()
                posted = _to_date(r.get("Release_Date"))
                close  = _to_date(r.get("Expired_Date"))
                mech   = (r.get("Activity...") or r.get("Activity") or "").strip()
                docno  = (r.get("Document_Number") or "").strip()
                dtype  = (r.get("Document_Type") or "").strip()
                url    = (r.get("URL") or "").strip()

                rec = {
                    "source": "nih_export",
                    "opportunity_id": docno or None,
                    "title": title or "(Untitled)",
                    "agency": "NIH",
                    "mechanism": mech,
                    "category": dtype or None,
                    "summary": None,
                    "eligibility": None,
                    "keywords": None,
                    "posted_date": posted,
                    "close_date": close,
                    "urls": {"landing": url or None, "details": url or None, "pdf": None},
                    "assistance_listing": None,
                    "raw": None,
                    "hash": _hash3(title, docno, url),
                }

                opp = _coerce_to_opportunity(rec)
                existing = s.query(Opportunity).filter_by(hash=opp.hash).one_or_none()
                if existing:
                    for attr in ["source","opportunity_id","title","agency","mechanism","category","summary",
                                 "eligibility","keywords","posted_date","close_date","urls","assistance_listing","raw"]:
                        setattr(existing, attr, getattr(opp, attr))
                    s.add(existing)
                else:
                    s.add(opp)
                ingested += 1

        elif source_name == "nsf_export":
            # Expected: Title, Synopsis, (Next due date ...), Posted date (Y-m-d), URL, Type / Award Type, Solicitation URL
            next_due_col = next((c for c in df.columns if "Next due date" in c), None)
            import re
            for _, r in df.iterrows():
                title = (r.get("Title") or "").strip()
                synopsis = (r.get("Synopsis") or "").strip() or None
                posted = _to_date(r.get("Posted date (Y-m-d)"))
                next_due_text = r.get(next_due_col) if next_due_col else None
                close = None
                if isinstance(next_due_text, str):
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", next_due_text)
                    if m: close = _to_date(m.group(1))
                    if not close:
                        m2 = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", next_due_text)
                        if m2: close = _to_date(m2.group(1))
                url = (r.get("URL") or "").strip()
                sol_url = (r.get("Solicitation URL") or "").strip()
                mech = (r.get("Type") or r.get("Award Type") or "").strip()

                rec = {
                    "source": "nsf_export",
                    "opportunity_id": None,
                    "title": title or "(Untitled)",
                    "agency": "NSF",
                    "mechanism": mech,
                    "category": None,
                    "summary": synopsis,
                    "eligibility": None,
                    "keywords": None,
                    "posted_date": posted,
                    "close_date": close,
                    "urls": {"landing": url or None, "details": (sol_url or url or None), "pdf": None},
                    "assistance_listing": None,
                    "raw": None,
                    "hash": _hash3(title, mech, sol_url or url),
                }

                opp = _coerce_to_opportunity(rec)
                existing = s.query(Opportunity).filter_by(hash=opp.hash).one_or_none()
                if existing:
                    for attr in ["source","opportunity_id","title","agency","mechanism","category","summary",
                                 "eligibility","keywords","posted_date","close_date","urls","assistance_listing","raw"]:
                        setattr(existing, attr, getattr(opp, attr))
                    s.add(existing)
                else:
                    s.add(opp)
                ingested += 1

        else:
            raise HTTPException(status_code=400, detail="source_name must be one of: nih_export, nsf_export")

        s.commit()
        return {"ingested": ingested, "source": source_name, "columns_seen": list(df.columns)}
    finally:
        s.close()


# Grants.gov export (your new workflow)
@app.post("/ingest/grants_csv")
def ingest_grants_csv(
    file: UploadFile = File(...),
    source_name: str = Form("grants_export")
):
    """
    Ingest a Grants.gov CSV export (single file) and normalize rows into opportunities.
    We build a details URL as: https://www.grants.gov/search-results-detail/<opportunity_id>
    """
    from ingest.base import _coerce_to_opportunity

    s = SessionLocal()
    try:
        raw = file.file.read()
        try:
            df = pd.read_csv(io.BytesIO(raw), engine="python")
        except Exception:
            df = pd.read_excel(io.BytesIO(raw))

        # Be robust to header variants
        def col(*names):
            for n in names:
                if n in df.columns:
                    return n
            return None

        col_id     = col("opportunity_id","Opportunity ID","OpportunityID","opportunityId")
        col_num    = col("opportunity_number","Opportunity Number","OpportunityNumber","opportunityNumber")
        col_title  = col("opportunity_title","Opportunity Title","Title","opportunityTitle")
        col_post   = col("post_date","Post Date","Posted Date","open_date","postedDate")
        col_close  = col("close_date","Close Date","close_date_description","Close Date Description","closeDate")
        col_agency = col("agency_name","top_level_agency_name","Agency Name","Top Level Agency Name","agencyName")
        col_cat    = col("category","Category","category_explanation","Category Explanation")
        col_sum    = col("summary_description","Synopsis","Summary","synopsis")
        col_addl   = col("additional_info_url","Additional Info URL","additionalInfoUrl")
        col_expected = col("expected_number_of_awards", "Expected Number of Awards")
        col_total    = col("estimated_total_program_funding", "Estimated Total Program Funding")
        col_floor    = col("award_floor", "Award Floor")
        col_ceiling  = col("award_ceiling", "Award Ceiling")

        ingested = 0
        for _, r in df.iterrows():
            oid   = (str(r.get(col_id))   if col_id   else "").strip()
            onum  = (str(r.get(col_num))  if col_num  else "").strip()
            title = (str(r.get(col_title)) if col_title else "").strip()
            agency= (str(r.get(col_agency)) if col_agency else "").strip()
            cat   = (str(r.get(col_cat))   if col_cat   else "").strip()
            summ  = (str(r.get(col_sum))   if col_sum   else "").strip() or None

            posted= _to_date(r.get(col_post)) if col_post else None
            close = _to_date(r.get(col_close)) if col_close else None

            addl  = (str(r.get(col_addl)) if col_addl else "").strip()
            details_url = addl or (f"https://www.grants.gov/search-results-detail/{oid}" if oid else None)
            expected_awards = (str(r.get(col_expected)) if col_expected else None)
            total_funding   = (str(r.get(col_total))   if col_total   else None)
            award_floor     = (str(r.get(col_floor))   if col_floor   else None)
            award_ceiling   = (str(r.get(col_ceiling)) if col_ceiling else None)

            rec = {
                "source": source_name,
                "opportunity_id": onum or oid or None,  # prefer number; fallback to id
                "title": title or "(Untitled)",
                "agency": agency or None,
                "mechanism": None,                      # can be inferred later from title/number
                "category": cat or None,
                "summary": summ,
                "eligibility": None,
                "keywords": None,
                "posted_date": posted,
                "close_date": close,
                "urls": {"landing": details_url, "details": details_url, "pdf": None},
                "assistance_listing": None,
                "raw": {
                    "expected_number_of_awards": expected_awards,
                    "estimated_total_program_funding": total_funding,
                    "award_floor": award_floor,
                    "award_ceiling": award_ceiling,
                },
                "hash": _hash3(title, (onum or oid), details_url),
            }

            opp = _coerce_to_opportunity(rec)
            existing = s.query(Opportunity).filter_by(hash=opp.hash).one_or_none()
            if existing:
                for attr in ["source","opportunity_id","title","agency","mechanism","category","summary",
                             "eligibility","keywords","posted_date","close_date","urls","assistance_listing","raw"]:
                    setattr(existing, attr, getattr(opp, attr))
                s.add(existing)
            else:
                s.add(opp)
            ingested += 1

        s.commit()
        return {"ingested": ingested, "source": source_name, "columns_seen": list(df.columns)}
    finally:
        s.close()


# ---------- Match (vector) ----------

@app.post("/match")
async def match(
    profile_text: str = Form(None),
    profile_file: UploadFile | None = File(None),
    top_k: int = Form(20)
):
    # Save uploaded file to temp if provided
    tmp_path = None
    if profile_file:
        suffix = os.path.splitext(profile_file.filename or "")[-1] or ".bin"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(profile_file.file, f)

    try:
        text = extract_profile_text(profile_text, tmp_path)
        s: Session = SessionLocal()
        try:
            results = match_opportunities(s, text, top_k=top_k)
            items = []
            for r in results:
                opp = r["opportunity"]
                items.append({
                    "score": r["score"],
                    "opportunity": OpportunityOut.model_validate(opp).model_dump()
                })
            return {"query_len": len(text), "results": items}
        finally:
            s.close()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/match/reindex")
def rebuild_index():
    s: Session = SessionLocal()
    try:
        n = reindex(s)
        return {"indexed": n}
    finally:
        s.close()


# ---------- Match (rerank with LLM, safe fallback) ----------

@app.post("/match/rerank")
async def match_rerank(
    profile_text: str = Form(...),
    top_k: int = Form(20),
    agency: Optional[str] = Form(None),
    mechanism: Optional[str] = Form(None),
    close_after: Optional[str] = Form(None)  # "YYYY-MM-DD"
):
    s: Session = SessionLocal()
    try:
        base_results = match_opportunities(s, profile_text, top_k=top_k)

        # Hard filters
        from datetime import datetime as _dt
        filt = []
        for r in base_results:
            o = r["opportunity"]
            if agency and (o.agency or "").lower().find(agency.lower()) < 0:
                continue
            if mechanism and (o.mechanism or "").lower().find(mechanism.lower()) < 0:
                continue
            if close_after:
                try:
                    cutoff = _dt.fromisoformat(close_after).date()
                    if o.close_date and o.close_date < cutoff:
                        continue
                except Exception:
                    pass
            filt.append(r)

        max_items = int(os.getenv("RERANK_MAX_ITEMS", "20"))
        filt = filt[:max_items]

        payload = []
        for r in filt:
            o = r["opportunity"]
            payload.append(RerankItem(
                id=o.id,
                title=o.title, summary=o.summary, eligibility=o.eligibility,
                mechanism=o.mechanism, agency=o.agency, source=o.source,
                close_date=o.close_date
            ))

        try:
            ranked = llm_rerank(profile_text, payload)
        except Exception as e:
            print("llm_rerank crashed (falling back):", repr(e))
            ranked = []

        ranked = ranked or []
        by_id = {x["id"]: x for x in ranked if isinstance(x, dict) and "id" in x}

        enriched = []
        for r in filt:
            o = r["opportunity"]
            extra = by_id.get(o.id)
            enriched.append({
                "vector_score": r["score"],
                "rerank_fit": (extra.get("fit") if extra else None),
                "why": (extra.get("why") if extra else None),
                "opportunity": OpportunityOut.model_validate(o).model_dump()
            })

        enriched.sort(key=lambda x: (x["rerank_fit"] is not None, x["rerank_fit"] or -1, x["vector_score"]), reverse=True)
        return {"count": len(enriched), "results": enriched}
    finally:
        s.close()


# ---------- Minimal HTML UI ----------

def _html_escape(s: str | None) -> str:
    if not s:
        return ""
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

@app.get("/match/form", response_class=HTMLResponse)
def match_form():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>RFA Matcher</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    textarea { width: 100%; height: 140px; }
    input[type=number] { width: 80px; }
    .card { max-width: 1100px; }
    table { border-collapse: collapse; width: 100%; margin-top: 16px; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
    th { background: #f6f6f6; text-align: left; }
    .muted { color: #666; font-size: 12px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>RFA Matcher</h1>
    <form method="get" action="/match/table">
      <label><strong>Profile text</strong></label><br/>
      <textarea name="profile_text" placeholder="Paste your research interests, abstracts, methods, populations…"></textarea>
      <div style="margin-top:8px">
        <label>Top K:</label>
        <input type="number" name="top_k" value="20" min="1" max="100"/>
        <button type="submit" style="margin-left:8px">Search</button>
      </div>
      <p class="muted">Tip: include constraints like “prefer R01/R21”, “exclude supplements”, “deadline ≥ 60 days”, etc.</p>
    </form>
  </div>
</body>
</html>
"""

def _fmt_money(x: str | None) -> str:
    if not x or x.strip().lower() in {"none", "nan", "n/a", "na", ""}:
        return ""
    # strip $ and commas, then try int/float; fall back to original string
    s = x.strip().replace("$", "").replace(",", "")
    try:
        n = float(s)
        # show as dollars with commas; drop decimals if .00
        return "${:,.0f}".format(n) if n.is_integer() else "${:,.2f}".format(n)
    except Exception:
        return x  # already human-readable


@app.get("/match/table", response_class=HTMLResponse)
def match_table(profile_text: str = "", top_k: int = 20):
    if not profile_text.strip():
        return HTMLResponse(
            '<p style="font-family:system-ui">No <code>profile_text</code> provided. '
            'Go to <a href="/match/form">/match/form</a> to submit a query.</p>', status_code=400
        )

    s: Session = SessionLocal()
    try:
        results = match_opportunities(s, profile_text, top_k=top_k)
        rows_html = []
        for r in results:
            # FIRST: get the opportunity
            opp = r["opportunity"]

            # Scoring & basic text
            score = f"{r['score']:.3f}"
            title = _html_escape(opp.title) if opp.title else "(Untitled)"
            agency = _html_escape(opp.agency)
            mechanism = _html_escape(opp.mechanism)
            source = _html_escape(opp.source)
            posted = opp.posted_date.isoformat() if opp.posted_date else ""
            close  = opp.close_date.isoformat() if opp.close_date else ""
            assist = _html_escape(opp.assistance_listing)
            summary = _html_escape((opp.summary or "")[:500])

            # Links: prefer deep details link; fall back to landing
            details = (opp.urls or {}).get("details") if opp.urls else None
            landing = details or ((opp.urls or {}).get("landing") if opp.urls else None)

            # Opp # column as deep link (when both number and link exist)
            opp_num = _html_escape(opp.opportunity_id) if opp.opportunity_id else ""
            opp_num_html = (f'<a href="{details}" target="_blank" rel="noopener">{opp_num}</a>'
                            if opp_num and details else opp_num)

            # Title link uses the same preferred URL
            title_html = f'<a href="{landing}" target="_blank" rel="noopener">{title}</a>' if landing else title

            # Funding fields (pulled from raw JSON we stored during CSV ingest)
            raw = opp.raw or {}
            if isinstance(raw, dict):
                exp_awards = (raw.get("expected_number_of_awards") or "").strip()
                total_fund = _fmt_money(raw.get("estimated_total_program_funding"))
                floor_amt  = _fmt_money(raw.get("award_floor"))
                ceil_amt   = _fmt_money(raw.get("award_ceiling"))
            else:
                exp_awards = ""
                total_fund = ""
                floor_amt  = ""
                ceil_amt   = ""

            rows_html.append(f"""
              <tr>
                <td>{score}</td>
                <td>{title_html}<div class="muted">{summary}</div></td>
                <td>{opp_num_html}</td>
                <td>{agency}</td>
                <td>{mechanism}</td>
                <td>{source}</td>
                <td>{posted}</td>
                <td>{close}</td>
                <td>{_html_escape(exp_awards)}</td>
                <td>{_html_escape(total_fund)}</td>
                <td>{_html_escape(floor_amt)}</td>
                <td>{_html_escape(ceil_amt)}</td>
                <td>{assist or ""}</td>
              </tr>
            """)

        html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Matches</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f6f6f6; text-align: left; }}
    .muted {{ color: #666; font-size: 12px; }}
    .topbar {{ margin-bottom: 16px; }}
  </style>
</head>
<body>
  <div class="topbar">
    <a href="/match/form">◀ Back to form</a>
  </div>
  <h1>Matches</h1>
  <p class="muted">Query length: {len(profile_text)} • Top K: {top_k}</p>
  <table>
    <thead>
      <tr>
        <th>Score</th>
        <th>Title & Summary</th>
        <th>Opp #</th>
        <th>Agency</th>
        <th>Mechanism</th>
        <th>Source</th>
        <th>Posted</th>
        <th>Close</th>
        <th>Exp. Awards</th>
        <th>Total Funding</th>
        <th>Floor</th>
        <th>Ceiling</th>
        <th>Assistance</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html) if rows_html else '<tr><td colspan="13">No results.</td></tr>'}
    </tbody>
  </table>
</body>
</html>
"""
        return HTMLResponse(html)
    finally:
        s.close()


@app.post("/admin/reset")
def reset_database():
    """Delete all opportunities and embedding index files."""
    # SessionLocal and Opportunity are already imported at the top of the file

    # Paths must match vectorstore.py
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
    VECS_FILE = os.path.join(DATA_DIR, "opps_vecs.npy")
    IDS_FILE  = os.path.join(DATA_DIR, "opps_ids.json")

    # 1️⃣ Clear database
    s = SessionLocal()
    try:
        s.query(Opportunity).delete()
        s.commit()
    finally:
        s.close()


from fastapi.responses import HTMLResponse

from fastapi.responses import HTMLResponse

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>RFA Admin</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; max-width: 900px; }
    h1 { margin-bottom: 8px; }
    section { margin: 24px 0; padding: 16px; border: 1px solid #ddd; border-radius: 8px; }
    button { padding: 8px 12px; cursor: pointer; }
    .muted { color:#666; font-size: 12px; }
    pre { background:#f7f7f7; padding:8px; overflow:auto; }
  </style>
</head>
<body>
  <h1>RFA Matcher — Admin</h1>

  <section>
    <h3>1) Upload Grants.gov CSV</h3>
    <form id="uploadForm" method="post" action="/ingest/grants_csv" enctype="multipart/form-data">
      <input type="file" name="file" required />
      <input type="hidden" name="source_name" value="grants_export"/>
      <button type="submit">Upload</button>
    </form>
    <div id="uploadResult" class="muted"></div>
  </section>

  <section>
    <h3>2) Rebuild Embeddings</h3>
    <button onclick="post('/match/reindex')">Reindex</button>
    <pre id="reindexOut" class="muted"></pre>
  </section>

  <section>
    <h3>3) Run Web Scrapers (optional)</h3>
    <button onclick="post('/ingest/run?source=pcori')">PCORI</button>
    <button onclick="post('/ingest/run?source=rwjf')">RWJF</button>
    <button onclick="post('/ingest/run?source=gates')">Gates</button>
    <button onclick="post('/ingest/run?source=dod_sbir')">DoD SBIR/STTR</button>
    <pre id="scrapeOut" class="muted"></pre>
  </section>

  <section>
    <h3>4) Utilities</h3>
    <button onclick="fetch('/opportunities?limit=1').then(r=>r.json()).then(j=>out('utilsOut', j))">Count Opportunities</button>
    <a href="/match/form" style="margin-left:8px">Open Matcher Form →</a>
    <div style="margin-top:10px"></div>
    <button style="background:#ffe8e8;border:1px solid #f5bdbd" onclick="confirmReset()">⚠ Reset DB (delete all)</button>
    <pre id="resetOut" class="muted"></pre>
    <pre id="utilsOut" class="muted"></pre>
  </section>

<script>
async function post(url) {
  const r = await fetch(url, {method:'POST'});
  const j = await r.json().catch(()=>({status:r.status}));
  if (url.includes('reindex')) out('reindexOut', j);
  else if (url.includes('ingest/run')) out('scrapeOut', j);
  else if (url.includes('/admin/reset')) out('resetOut', j);
}

function out(id, obj){ document.getElementById(id).textContent = JSON.stringify(obj,null,2); }

const up = document.getElementById('uploadForm');
up && up.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const fd = new FormData(up);
  const r = await fetch('/ingest/grants_csv', {method:'POST', body:fd});
  const j = await r.json().catch(()=>({status:r.status}));
  document.getElementById('uploadResult').textContent = JSON.stringify(j,null,2);
});

async function confirmReset(){
  if (!confirm('This will delete ALL ingested opportunities and clear the vector index. Continue?')) return;
  await post('/admin/reset');
}
</script>
</body>
</html>
"""
