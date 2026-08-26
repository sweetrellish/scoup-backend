from django.core.management.base import BaseCommand
from academic.scraper import run_scraper

class Command(BaseCommand):
    help = 'Scrapes SU faculty data and updates database'

    def handle(self, *args, **options):
        result = run_scraper()
        self.stdout.write(
            self.style.SUCCESS(f"✓ {result}")
        )
