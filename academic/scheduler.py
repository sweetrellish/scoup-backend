from apscheduler.schedulers.background import BackgroundScheduler
from django.core.management import call_command
import logging

logger = logging.getLogger(__name__)

def start_scheduler():
    scheduler = BackgroundScheduler()
    
    # Run scraper daily at 2 AM
    scheduler.add_job(
        func=lambda: call_command('scrape_su_data'),
        trigger="cron",
        hour=2,
        minute=0,
        id='su_scraper_job',
        name='Daily SU Faculty Scrape',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("Scheduler started. Daily scrape scheduled for 2 AM")

def stop_scheduler(scheduler):
    if scheduler.running:
        scheduler.shutdown()
