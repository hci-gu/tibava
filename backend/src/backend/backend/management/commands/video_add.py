import os
from django.core.management.base import BaseCommand, CommandError
import pathlib
from django.contrib import auth

from backend.utils.video_ingest import PathUploadFile, ingest_video_file


class Command(BaseCommand):
    help = "Closes the specified poll for voting"

    def add_arguments(self, parser):
        parser.add_argument("--user", type=str)
        parser.add_argument("--video", type=str)
        parser.add_argument("--name", default="dir", choices=["dir", "filename"], type=str)

    def handle(self, *args, **options):
        try:
            user = auth.get_user_model().objects.get(id=options["user"])
        except auth.get_user_model().DoesNotExist as e:
            self.stdout.write(self.style.ERROR(f"User does not exist"))
            return
        print(os.listdir(options["video"]))
        if os.path.isdir(options["video"]):
            for root, dirs, files in os.walk(options["video"]):
                for f in files:
                    file_path = os.path.join(root, f)

                    path = pathlib.Path(file_path)

                    if options["name"] == "dir":
                        video_name = path.parent.name
                    elif options["name"] == "filename":
                        video_name = path.stem

                    result = ingest_video_file(
                        PathUploadFile(file_path, name=path.name),
                        owner=user,
                        title=video_name,
                    )

                    if result["status"] == "ok":
                        print(result["video"].id.hex)
                    else:
                        print(f"{file_path}: {result.get('type', 'error')}")

        self.stdout.write(self.style.SUCCESS(f"Videos added"))
        # else:
        #     options["video"]
