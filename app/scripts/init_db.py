from sqlalchemy import text
from db import engine
from models import Base

def main():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as con:
        try:
            con.execute(text("CREATE INDEX IF NOT EXISTS idx_close_date ON opportunities (close_date)"))
        except Exception:
            pass
    print("DB initialized.")

if __name__ == "__main__":
    main()
