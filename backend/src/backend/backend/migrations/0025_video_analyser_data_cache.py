from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0024_videobatch_cancelled_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="analyser_data_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="analyser_data_file",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="analyser_data_ext",
            field=models.CharField(blank=True, max_length=256, null=True),
        ),
    ]
