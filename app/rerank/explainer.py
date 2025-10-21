import os, time
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import date
from settings import settings

# OpenAI client (lazy import so app runs without key)
_client = None
def get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

class RerankItem(BaseModel):
    id: int
    title: str | None
    summary: str | None
    eligibility: str | None
    mechanism: str | None
    agency: str | None
    source: str
    close_date: date | None

PROMPT = """You are ranking funding opportunities for a researcher.

The researcher's profile:
---
{profile}
---

Each opportunity includes: title, summary, mechanism, agency, source, eligibility (if any), and close_date (if any).
Score each opportunity from 0 to 100 for FIT. Then explain in 1–2 sentences why it fits or doesn't.
Be strict about mismatches (e.g., agency/mechanism constraints, off-topic scope).
If deadlines are missing or passed, mention it.

Return ONLY valid JSON with a list of:
[{{"id": <int>, "fit": <0-100 int>, "why": "<short explanation>"}}]
"""

def llm_rerank(profile: str, items: List[RerankItem]) -> List[Dict[str, Any]]:
    import json, os, sys, traceback
    if not os.getenv("OPENAI_API_KEY"):
        return []
    try:
        client = get_client()
        model = os.getenv("RERANK_MODEL", "gpt-4o-mini")
        lines = []
        for it in items:
            lines.append(
                f"- id:{it.id}; title:{it.title or ''}; agency:{it.agency or ''}; mechanism:{it.mechanism or ''}; "
                f"source:{it.source}; close_date:{it.close_date or ''}; "
                f"summary:{(it.summary or '').strip()[:700]}; eligibility:{(it.eligibility or '').strip()[:400]}"
            )
        items_text = "\n".join(lines)
        prompt = PROMPT.format(profile=profile) + "\n\nOpportunities:\n" + items_text

        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a careful evaluator. Always return valid JSON."},
                {"role": "user", "content": prompt},
            ],
            timeout=int(os.getenv("RERANK_TIMEOUT", "30")),
        )
        content = (resp.choices[0].message.content or "{}").strip()

        try:
            data = json.loads(content)
        except Exception:
            print("LLM rerank: non-JSON response; falling back.")
            return []

        if isinstance(data, dict) and isinstance(data.get("list"), list):
            return data["list"]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print("LLM rerank error (falling back):", repr(e))
        traceback.print_exc(file=sys.stdout)
        return []
