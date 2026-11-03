"""Attach retained original geometry without changing any curated clusters."""

import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from data import DataManager
from backend.models import PluginRun, PluginRunResult
from backend.utils.analyser_client import TaskAnalyserClient


class Command(BaseCommand):
    help = "Recover boxes/landmarks from known original analyser output IDs. Refuses mismatched face IDs or existing geometry."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=uuid.UUID)
        parser.add_argument("--bbox-data-id", type=uuid.UUID, required=True)
        parser.add_argument("--keypoints-data-id", type=uuid.UUID)

    def handle(self, run_id, bbox_data_id, keypoints_data_id=None, **options):
        run = PluginRun.objects.filter(id=run_id, type="face_clustering", status=PluginRun.STATUS_DONE).first()
        if run is None:
            raise CommandError("Choose a completed face clustering run.")
        if run.results.filter(type__in=[PluginRunResult.TYPE_BBOXES, PluginRunResult.TYPE_KEYPOINTS]).exists():
            raise CommandError("This run already has geometry; it will not be overwritten.")
        embeddings = run.results.filter(type=PluginRunResult.TYPE_IMAGE_EMBEDDINGS).first()
        if embeddings is None:
            raise CommandError("This run has no stored face embeddings to verify against.")
        manager = DataManager(settings.DATA_OUTPUT_PATH)
        with manager.load(embeddings.data_id) as data:
            expected = {entry.ref_id for entry in data.embeddings}
        if not expected:
            raise CommandError("No original face IDs are available for verification.")
        client = TaskAnalyserClient(host=settings.GRPC_HOST, port=settings.GRPC_PORT, manager=manager)
        recovered = []
        for identifier, kind, attribute in [(bbox_data_id, PluginRunResult.TYPE_BBOXES, "bboxes"),
                                             (keypoints_data_id, PluginRunResult.TYPE_KEYPOINTS, "kpss")]:
            if identifier is None:
                continue
            data = client.download_data(identifier.hex)
            if data is None:
                raise CommandError("The requested analyser output is unavailable; rerun Face clustering to produce a new run.")
            with data:
                entries = getattr(data, attribute, [])
                if not expected.issubset({entry.ref_id for entry in entries}):
                    raise CommandError("Geometry does not contain the original face IDs. Nothing was attached.")
            recovered.append((identifier.hex, kind, attribute))
        with transaction.atomic():
            run = PluginRun.objects.select_for_update().get(id=run.id)
            if run.results.filter(type__in=[PluginRunResult.TYPE_BBOXES, PluginRunResult.TYPE_KEYPOINTS]).exists():
                raise CommandError("Geometry was already attached by another operation.")
            for identifier, kind, name in recovered:
                PluginRunResult.objects.create(plugin_run=run, data_id=identifier, type=kind, name=name)
        self.stdout.write(self.style.SUCCESS("Original geometry attached; cluster edits preserved."))
