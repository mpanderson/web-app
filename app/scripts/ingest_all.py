from tasks import ingest_all

if __name__ == "__main__":
    res = ingest_all.delay()
    print("Triggered ingest_all task:", res.id)
