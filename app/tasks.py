from celery import shared_task
from sqlalchemy.orm import Session
from db import SessionLocal
from ingest import REGISTRY
from match.vectorstore import reindex

@shared_task(name="app.tasks.ingest_source")
def ingest_source(source: str) -> int:
    session: Session = SessionLocal()
    try:
        Ingestor = REGISTRY[source]
        cnt = Ingestor(session).run()
        # Reindex after ingest
        reindex(session)
        return cnt
    finally:
        session.close()

@shared_task(name="app.tasks.ingest_all")
def ingest_all() -> int:
    session: Session = SessionLocal()
    try:
        total = 0
        for s in ["grantsgov", "nih", "nsf"]:
            Ingestor = REGISTRY[s]
            total += Ingestor(session).run()
        reindex(session)
        return total
    finally:
        session.close()
