from pdfminer.high_level import extract_text
import os

def extract_profile_text(profile_text: str | None, profile_file_path: str | None) -> str:
    text = profile_text or ""
    if profile_file_path and os.path.exists(profile_file_path):
        if profile_file_path.lower().endswith(".pdf"):
            try:
                text += "\n" + extract_text(profile_file_path)
            except Exception:
                pass
        else:
            try:
                with open(profile_file_path, "r", encoding="utf-8") as f:
                    text += "\n" + f.read()
            except Exception:
                pass
    return text.strip()
