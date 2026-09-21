from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Pause batch dispatch without cancelling rows or deleting results."

    def handle(self, *args, **options):
        path = Path(settings.BATCH_PAUSE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.stdout.write(f"Batch processing paused: {path}. In-flight work may finish.")
