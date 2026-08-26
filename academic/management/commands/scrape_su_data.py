import os
from django.core.management.base import BaseCommand
from academic.scraper import run_scraper

class Command(BaseCommand):
    help = 'Scrape Salisbury University publications from Crossref'
    
    def add_arguments(self, parser):
        parser.add_argument('--from-year', type=int, default=2024)
        parser.add_argument('--to-year', type=int, default=2024)
        parser.add_argument('--from-month', type=int, default=1)
        parser.add_argument('--to-month', type=int, default=12)
    
    def handle(self, *args, **options):
        result = run_scraper(
            from_year=options['from_year'],
            to_year=options['to_year'],
            from_month=options['from_month'],
            to_month=options['to_month']
        )
        self.stdout.write(self.style.SUCCESS(f"Scrape complete: {result}"))
