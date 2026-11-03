import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0029_videobatchitem_display_path"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoVisualizationExport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(default="queued", max_length=16)),
                ("progress", models.FloatField(default=0)),
                ("cancel_requested", models.BooleanField(default=False)),
                ("preview", models.BooleanField(default=False)),
                ("configuration", models.JSONField(default=dict)),
                ("error", models.CharField(blank=True, max_length=512)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ("video", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="visualization_exports", to="backend.video")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
