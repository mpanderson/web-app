# app/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging

from db import SessionLocal
from ingest import REGISTRY
from match.vectorstore import reindex

# Set up logging
logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)

# Global scheduler instance
_scheduler = None

def run_all_ingestions():
    """
    Run all registered ingestion sources and reindex the vector store.
    This function is called by the scheduler at scheduled times.
    
    For each source, we:
    1. Delete all existing opportunities from that source
    2. Re-ingest fresh data
    3. This prevents stale/closed opportunities from lingering
    """
    from models import Opportunity
    
    logger.info("=" * 60)
    logger.info(f"🕐 Starting scheduled ingestion at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    session = SessionLocal()
    total_ingested = 0
    total_deleted = 0
    
    try:
        # Run all registered ingestors
        for source_name, ingestor_class in REGISTRY.items():
            try:
                # Clear existing opportunities from this source before re-ingesting
                logger.info(f"🗑️  Clearing old {source_name} opportunities...")
                deleted_count = session.query(Opportunity).filter_by(source=source_name).delete()
                session.commit()
                total_deleted += deleted_count
                logger.info(f"🗑️  Deleted {deleted_count} old {source_name} opportunities")
                
                # Ingest fresh data
                logger.info(f"📥 Running {source_name} ingestor...")
                ingestor = ingestor_class(session)
                count = ingestor.run()
                total_ingested += count
                logger.info(f"✅ {source_name}: ingested {count} new opportunities")
            except Exception as e:
                logger.error(f"❌ {source_name} failed: {e}")
                session.rollback()
                # Continue with other sources even if one fails
                continue
        
        # Reindex the vector store for semantic search
        logger.info("🔄 Reindexing vector store...")
        indexed_count = reindex(session)
        logger.info(f"✅ Reindexed {indexed_count} opportunities")
        
        logger.info("=" * 60)
        logger.info(f"✅ Scheduled ingestion complete!")
        logger.info(f"   Deleted: {total_deleted} old opportunities")
        logger.info(f"   Ingested: {total_ingested} new opportunities")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Scheduled ingestion failed: {e}")
    finally:
        session.close()

def start_scheduler():
    """
    Start the background scheduler with twice-daily ingestion tasks.
    Runs at 12:00 PM (noon) and 8:00 PM daily.
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("Scheduler already running, skipping initialization")
        return _scheduler
    
    logger.info("🚀 Initializing ingestion scheduler...")
    
    # Create background scheduler
    _scheduler = BackgroundScheduler(
        timezone="America/New_York",  # Set your timezone
        daemon=True
    )
    
    # Schedule for 12:00 PM (noon) daily
    _scheduler.add_job(
        run_all_ingestions,
        CronTrigger(hour=12, minute=0),
        id="ingestion_noon",
        name="Daily Ingestion at Noon",
        replace_existing=True
    )
    logger.info("📅 Scheduled: Daily ingestion at 12:00 PM (noon)")
    
    # Schedule for 8:00 PM daily
    _scheduler.add_job(
        run_all_ingestions,
        CronTrigger(hour=20, minute=0),
        id="ingestion_evening",
        name="Daily Ingestion at 8 PM",
        replace_existing=True
    )
    logger.info("📅 Scheduled: Daily ingestion at 8:00 PM")
    
    # Start the scheduler
    _scheduler.start()
    logger.info("✅ Scheduler started successfully!")
    
    # Log next run times
    jobs = _scheduler.get_jobs()
    for job in jobs:
        logger.info(f"⏰ Next run: {job.name} at {job.next_run_time}")
    
    return _scheduler

def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        logger.info("Stopping scheduler...")
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Scheduler stopped")

def get_scheduler_status():
    """Get current scheduler status and upcoming jobs."""
    global _scheduler
    
    if _scheduler is None:
        return {
            "running": False,
            "jobs": []
        }
    
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    
    return {
        "running": True,
        "jobs": jobs,
        "timezone": str(_scheduler.timezone)
    }
