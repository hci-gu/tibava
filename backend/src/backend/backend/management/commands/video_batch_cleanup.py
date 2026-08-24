import shutil
import time
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand

from backend.models import VideoBatch
from backend.utils.batch_upload import get_batch_upload_root


class Command(BaseCommand):
    help = "Remove abandoned temporary video batch upload directories"

    def add_arguments(self, parser):
        parser.add_argument("--max_age_hours", type=float, default=24.0)
        parser.add_argument("--dry_run", action="store_true")

    def handle(self, *args, **options):
        root = get_batch_upload_root()
        if not root.exists():
            self.stdout.write(self.style.SUCCESS("No batch upload root exists"))
            return

        cutoff = time.time() - options["max_age_hours"] * 60 * 60
        removed = 0
        for path in root.iterdir():
            if not path.is_dir():
                continue

            try:
                batch_id = uuid.UUID(path.name)
            except ValueError:
                batch_id = None

            batch_exists = (
                VideoBatch.objects.filter(id=batch_id).exists()
                if batch_id is not None
                else False
            )
            if batch_exists or Path(path).stat().st_mtime >= cutoff:
                continue

            if options["dry_run"]:
                self.stdout.write(f"Would remove {path}")
            else:
                shutil.rmtree(path)
                self.stdout.write(f"Removed {path}")
            removed += 1

        self.stdout.write(self.style.SUCCESS(f"Cleaned {removed} batch directories"))
