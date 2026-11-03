from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("backend", "0030_videovisualizationexport")]
    operations = [migrations.AlterField(
        model_name="pluginrunresult", name="type",
        field=models.CharField(max_length=2, default="S", choices=[
            ("V", "VIDEO"), ("I", "IMAGES"), ("S", "SCALAR"), ("H", "HIST"),
            ("SH", "SHOTS"), ("R", "RGB_HIST"), ("CL", "CLUSTER"), ("FA", "FACE"),
            ("E", "IMAGE_EMBEDDINGS"), ("BB", "BBOXES"), ("KP", "KEYPOINTS"), ("SP", "SPATIAL"),
        ]),
    )]
