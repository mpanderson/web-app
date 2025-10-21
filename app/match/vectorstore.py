import os, json
from typing import List, Tuple
import numpy as np
from sqlalchemy.orm import Session

from models import Opportunity
from settings import settings
from utils.text import clean_text

DATA_DIR = "/app/data"
VECS_FILE = os.path.join(DATA_DIR, "opps_vecs.npy")
IDS_FILE  = os.path.join(DATA_DIR, "opps_ids.json")

# Optional: allow a custom model name if your settings already had one
DEFAULT_LOCAL_MODEL = getattr(settings, "embeddings_model", "sentence-transformers/all-MiniLM-L6-v2")

_model = None  # lazy-loaded for local backend

# -------- Embedding backends --------

def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def _load_local_model():
    """Lazy-load SentenceTransformer model when EMBEDDINGS_BACKEND=local."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(DEFAULT_LOCAL_MODEL)
    return _model

def _embed_local(texts: List[str]) -> np.ndarray:
    model = _load_local_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    # sentence-transformers returns float32 or float64 depending on env; ensure float32
    return np.asarray(vecs, dtype="float32")

def _embed_openai(texts: List[str]) -> np.ndarray:
    """OpenAI embeddings backend (text-embedding-3-small). Requires OPENAI_API_KEY."""
    from openai import OpenAI
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set but EMBEDDINGS_BACKEND=openai.")
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    # Batch once; for very large corpora you may want to chunk
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    arr = np.array([d.embedding for d in resp.data], dtype="float32")
    # L2-normalize for cosine via dot product
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    return arr / norms

def embed_texts(texts: List[str]) -> np.ndarray:
    backend = getattr(settings, "EMBEDDINGS_BACKEND", "local").lower()
    if backend == "openai":
        return _embed_openai(texts)
    # default to local
    return _embed_local(texts)

# -------- Corpus & Index --------

def build_corpus(session: Session) -> Tuple[List[Opportunity], List[str], List[int]]:
    rows = session.query(Opportunity).all()
    texts: List[str] = []
    ids: List[int] = []
    for r in rows:
        t = " ".join(filter(None, [
            r.title,
            r.summary,
            r.eligibility,
            r.mechanism,
            r.agency,
            r.category
        ]))
        texts.append(clean_text(t))
        ids.append(r.id)
    return rows, texts, ids

def reindex(session: Session) -> int:
    """Rebuild embeddings and persist to disk. Returns # of items indexed."""
    _ensure_dir()
    rows, texts, ids = build_corpus(session)

    if not texts:
        # write empty artifacts
        np.save(VECS_FILE, np.empty((0, 384), dtype="float32"))
        with open(IDS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return 0

    vecs = embed_texts(texts)  # already L2-normalized
    # Persist
    np.save(VECS_FILE, vecs.astype("float32"))
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f)
    return len(ids)

def _load_index() -> Tuple[np.ndarray, List[int]]:
    if not (os.path.exists(VECS_FILE) and os.path.exists(IDS_FILE)):
        return np.empty((0, 384), dtype="float32"), []
    vecs = np.load(VECS_FILE).astype("float32")
    with open(IDS_FILE, "r", encoding="utf-8") as f:
        ids = json.load(f)
    # Safety: ensure vecs are normalized
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms
    return vecs, ids

def _cosine_topk(mat: np.ndarray, q: np.ndarray, ids: List[int], k: int) -> List[Tuple[int,float]]:
    """
    mat: (N, D) normalized
    q:   (1, D) normalized
    returns list of (id, score) sorted desc by score
    """
    if mat.size == 0 or not ids:
        return []
    sims = (mat @ q.T).ravel()  # cosine since both normalized
    k = max(1, min(k, len(ids)))
    # argpartition then sort those top-k for speed
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(int(ids[i]), float(sims[i])) for i in idx]

def search(session: Session, query: str, k: int = 20):
    """Return [{'opportunity': Opportunity, 'score': float}, ...]"""
    # Ensure index exists
    if not (os.path.exists(VECS_FILE) and os.path.exists(IDS_FILE)):
        reindex(session)

    mat, ids = _load_index()
    if mat.size == 0 or not ids:
        return []

    qv = embed_texts([clean_text(query)]).astype("float32")  # (1, D), normalized
    top = _cosine_topk(mat, qv, ids, k)

    # Map back to ORM
    wanted = [i for i, _ in top]
    if not wanted:
        return []
    op_by_id = {o.id: o for o in session.query(Opportunity).filter(Opportunity.id.in_(wanted)).all()}

    results = []
    for i, score in top:
        opp = op_by_id.get(i)
        if opp:
            results.append({"opportunity": opp, "score": score})
    return results
