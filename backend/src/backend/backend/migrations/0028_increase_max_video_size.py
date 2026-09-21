from django.db import migrations, models


OLD_DEFAULT = 500 * 1024 * 1024
NEW_DEFAULT = 10 * 1024 * 1024 * 1024


def increase_existing_default_limits(apps, schema_editor):
    TibavaUser = apps.get_model("backend", "TibavaUser")
    TibavaUser.objects.filter(max_video_size=OLD_DEFAULT).update(
        max_video_size=NEW_DEFAULT
    )


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0027_savedbatchpreset"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tibavauser",
            name="max_video_size",
            field=models.BigIntegerField(default=NEW_DEFAULT),
        ),
        migrations.RunPython(
            increase_existing_default_limits,
            migrations.RunPython.noop,
        ),
    ]
