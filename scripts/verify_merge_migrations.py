"""Verify the upstream merge on disposable SQLite databases only.

Run with --upgrade to start at the fork's last migration; omit for a fresh DB.
Requires the backend dependencies, but never connects to the application DB.
"""
import os
from pathlib import Path
import sys


root = Path(__file__).resolve().parents[1]
for source in (
    "backend/src/backend", "analyser/src", "inference_ray/src",
    "packages/tibava_data/src", "packages/tibava_interface/src", "packages/tibava_utils/src",
):
    sys.path.insert(0, str(root / source))
os.environ["DJANGO_SETTINGS_MODULE"] = "tibava.settings"
os.environ["DB_ENGINE"] = "django.db.backends.sqlite3"
os.environ["DB_NAME"] = ":memory:"

import django
django.setup()

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

executor = MigrationExecutor(connection)
if "--upgrade" in sys.argv:
    fork = [("backend", "0029_videobatchitem_display_path")]
    executor.migrate(fork)
    apps = executor.loader.project_state(fork).apps
    user = apps.get_model("backend", "TibavaUser").objects.create(
        username="migration-check", allowance=27, max_video_size=123456789,
    )
    batch = apps.get_model("backend", "VideoBatch").objects.create(owner_id=user.pk, name="Preserved batch")
    user_id, batch_id = user.pk, batch.pk

executor = MigrationExecutor(connection)
leaves = executor.loader.graph.leaf_nodes()
executor.migrate(leaves)
apps = executor.loader.project_state(leaves).apps
user_model = apps.get_model("backend", "TibavaUser")
if "--upgrade" in sys.argv:
    user = user_model.objects.get(pk=user_id)
    assert (user.allowance, user.max_video_size) == (27, 123456789)
    assert user.max_plugin_runs == -1
    assert apps.get_model("backend", "VideoBatch").objects.get(pk=batch_id).name == "Preserved batch"
else:
    user = user_model.objects.create(username="fresh-check")
    assert user.allowance == 10
    assert user.max_video_size == 10 * 1024**3
assert len(executor.loader.graph.leaf_nodes("backend")) == 1
print("Fork upgrade preserves records and quotas" if "--upgrade" in sys.argv else "Fresh migration passes")
