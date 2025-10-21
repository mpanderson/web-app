from db import SessionLocal
from match.vectorstore import reindex

def main():
    s = SessionLocal()
    try:
        n = reindex(s)
        print(f"Indexed {n} opportunities")
    finally:
        s.close()

if __name__ == "__main__":
    main()
