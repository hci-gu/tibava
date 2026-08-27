from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0025_video_analyser_data_cache"),
    ]

    operations = [
        migrations.AddField(
            model_name="videobatch",
            name="custom_preset_definition",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="videobatch",
            name="custom_preset_item_ids",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
