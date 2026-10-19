from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("backend", "0030_merge_upstream")]

    # Altering defaults leaves existing users' quotas unchanged.
    operations = [
        migrations.AlterField(
            model_name="tibavauser",
            name="allowance",
            field=models.IntegerField(default=settings.USER_LIMITS_ALLOWANCE),
        ),
        migrations.AlterField(
            model_name="tibavauser",
            name="max_video_size",
            field=models.BigIntegerField(default=settings.USER_LIMITS_MAX_VIDEO_SIZE),
        ),
        migrations.AlterField(
            model_name="tibavauser",
            name="max_plugin_runs",
            field=models.IntegerField(default=settings.USER_LIMITS_MAX_PLUGIN_RUNS),
        ),
    ]
