"""
Scheduler for automated daily syncs.

Run this script to keep your Google Sheet updated automatically.
Can be run as a daemon, cron job, or cloud function.
"""
import os
import sys
import logging
from datetime import datetime, time
from pathlib import Path

import schedule
from dotenv import load_dotenv

from .appfolio_import import import_from_csv, import_from_appfolio_api, import_from_skywalk_api
from .sheets_sync import full_sync

load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / 'logs' / 'sync.log')
    ]
)
logger = logging.getLogger(__name__)


def ensure_log_directory():
    """Create logs directory if it doesn't exist."""
    log_dir = Path(__file__).parent.parent / 'logs'
    log_dir.mkdir(exist_ok=True)


def daily_sync_job():
    """
    Daily sync job that:
    1. Imports latest data from AppFolio (API if configured, otherwise skip)
    2. Syncs with Google Sheets (preserving notes)
    """
    logger.info("=" * 50)
    logger.info("Starting daily sync job")
    
    # Try AppFolio API import if credentials are available
    if os.getenv('APPFOLIO_API_URL') and os.getenv('APPFOLIO_CLIENT_ID'):
        logger.info("Importing from AppFolio API...")
        try:
            result = import_from_appfolio_api()
            logger.info(f"AppFolio API import: {result['added']} added, {result['updated']} updated")
        except Exception as e:
            logger.error(f"AppFolio API import failed: {e}")
    elif os.getenv('SKYWALK_API_KEY'):
        logger.info("Importing from Skywalk API...")
        try:
            result = import_from_skywalk_api()
            logger.info(f"Skywalk API import: {result['added']} added, {result['updated']} updated")
        except Exception as e:
            logger.error(f"Skywalk API import failed: {e}")
    else:
        logger.info("No API configured, skipping API import")
        logger.info("To enable: set APPFOLIO_API_URL + APPFOLIO_CLIENT_ID + APPFOLIO_CLIENT_SECRET")
    
    # Check for any CSV files in the import folder
    import_dir = Path(__file__).parent.parent / 'imports'
    if import_dir.exists():
        for csv_file in import_dir.glob('*.csv'):
            logger.info(f"Processing CSV: {csv_file}")
            try:
                result = import_from_csv(csv_file)
                logger.info(f"CSV import: {result['added']} added, {result['updated']} updated")
                
                # Move processed file to archive
                archive_dir = import_dir / 'processed'
                archive_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                csv_file.rename(archive_dir / f"{csv_file.stem}_{timestamp}{csv_file.suffix}")
                logger.info(f"Archived: {csv_file.name}")
            except Exception as e:
                logger.error(f"CSV import failed for {csv_file}: {e}")
    
    # Sync with Google Sheets
    sheet_id = os.getenv('GOOGLE_SHEET_ID')
    if sheet_id:
        logger.info("Syncing with Google Sheets...")
        try:
            result = full_sync(sheet_id)
            logger.info(f"Sheets sync complete: {result}")
        except Exception as e:
            logger.error(f"Sheets sync failed: {e}")
    else:
        logger.warning("GOOGLE_SHEET_ID not set, skipping sheets sync")
    
    logger.info("Daily sync job complete")
    logger.info("=" * 50)


def run_scheduler(sync_time: str = "06:00"):
    """
    Run the scheduler daemon.
    
    Args:
        sync_time: Time to run daily sync in HH:MM format (24-hour)
    """
    ensure_log_directory()
    
    logger.info(f"Starting scheduler - daily sync at {sync_time}")
    
    # Schedule daily job
    schedule.every().day.at(sync_time).do(daily_sync_job)
    
    # Also run immediately on startup
    logger.info("Running initial sync...")
    daily_sync_job()
    
    # Keep running
    while True:
        schedule.run_pending()
        import time as time_module
        time_module.sleep(60)  # Check every minute


def run_once():
    """Run sync once and exit (for cron jobs or cloud functions)."""
    ensure_log_directory()
    daily_sync_job()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        run_once()
    else:
        sync_time = os.getenv('SYNC_TIME', '06:00')
        run_scheduler(sync_time)

