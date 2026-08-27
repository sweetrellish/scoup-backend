from django.core.management.base import BaseCommand
from django.core.management import call_command
import os

class Command(BaseCommand):
    help = 'Import faculty from export file'

    def handle(self, *args, **options):
        if os.path.exists('faculty_export.json'):
            call_command('loaddata', 'faculty_export.json')
            self.stdout.write(self.style.SUCCESS('✓ Faculty imported'))
