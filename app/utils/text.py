import hashlib, re

def clean_text(t: str | None) -> str:
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p:
            h.update(p.encode("utf-8", "ignore"))
    return h.hexdigest()
