from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0023_videobatch_videobatchitem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="videobatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("U", "UPLOADING"),
                    ("I", "INGESTING"),
                    ("R", "READY"),
                    ("P", "PARTIAL_ERROR"),
                    ("E", "ERROR"),
                    ("N", "RUNNING"),
                    ("C", "CANCELLED"),
                ],
                default="U",
                max_length=2,
            ),
        ),
    ]
