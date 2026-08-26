from django.apps import AppConfig

class ScoupdDbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scoupdb'
    
    def ready(self):
        """Start scheduler when Django boots"""
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from django.core.management import call_command
            
            # Check if scheduler already exists
            from apscheduler.schedulers.background import BackgroundScheduler
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
            
            if not scheduler.running:
                scheduler.start()
                logger.info("✓ Scraper scheduler started (daily at 2 AM)")
        except Exception as e:
            logger.warning(f"Scheduler init warning (non-critical): {e}")
