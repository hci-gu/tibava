from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0028_increase_max_video_size"),
    ]

    operations = [
        migrations.AddField(
            model_name="videobatchitem",
            name="display_path",
            field=models.CharField(blank=True, max_length=1024),
        ),
    ]
