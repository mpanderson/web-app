from sqlalchemy.orm import Session
from .vectorstore import search
from utils.text import clean_text

def match_opportunities(session: Session, profile_text: str, top_k: int = 20):
    q = clean_text(profile_text)
    if not q:
        return []
    results = search(session, q, k=top_k)
    return results
