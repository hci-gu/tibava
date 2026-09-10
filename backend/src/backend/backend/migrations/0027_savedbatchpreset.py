import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0026_videobatch_custom_preset_definition"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedBatchPreset",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=256)),
                ("description", models.CharField(blank=True, max_length=1024)),
                ("definition", models.JSONField()),
                ("date", models.DateTimeField(auto_now_add=True)),
                ("update_date", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_batch_presets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name", "date"],
            },
        ),
        migrations.AddConstraint(
            model_name="savedbatchpreset",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_saved_batch_preset_name_per_owner",
            ),
        ),
    ]
