from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = 'Creates a superuser if none exists'

    def handle(self, *args, **options):
        if not User.objects.filter(username='ryan').exists():
            User.objects.create_superuser('ryan', 'ryan@scoup.local', 'ScoupAdmin123!')
            self.stdout.write(self.style.SUCCESS('✓ Created superuser: ryan'))
        else:
            self.stdout.write(self.style.SUCCESS('✓ Superuser already exists'))
