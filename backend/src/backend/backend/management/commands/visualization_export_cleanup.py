from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from backend.models import VideoVisualizationExport


class Command(BaseCommand):
    help = "Delete completed, failed and cancelled visualization exports older than N days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        jobs = VideoVisualizationExport.objects.filter(
            status__in=["complete", "error", "cancelled"],
            updated_at__lt=timezone.now() - timedelta(days=options["days"]),
        )
        count = jobs.count()
        jobs.delete()
        self.stdout.write(f"Removed {count} visualization exports.")
