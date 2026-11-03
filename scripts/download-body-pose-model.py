"""Install the official torchvision COCO keypoint model, verifying its hash prefix."""

import argparse
import hashlib
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, default=Path("data/models"))
    args = parser.parse_args()
    target = args.model_root / "body_pose/keypointrcnn_resnet50_fpn_coco-fc266e95.pth"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest().startswith("fc266e95"):
        print(f"Verified existing model: {target}")
        return
    partial = target.with_suffix(".download")
    try:
        urllib.request.urlretrieve("https://download.pytorch.org/models/" + target.name, partial)
        if not hashlib.sha256(partial.read_bytes()).hexdigest().startswith("fc266e95"):
            raise ValueError("Downloaded model failed checksum verification")
        partial.replace(target)
        print(f"Installed model: {target}")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
