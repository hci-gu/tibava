#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from urllib.request import urlopen

import yaml


CONFIG_PATH = Path(__file__).with_name("deploy.cuda.yml")
STATUS_URL = "http://localhost:52365/api/serve/applications/"


def main() -> int:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    expected = {app["name"] for app in config["applications"]}
    with urlopen(STATUS_URL, timeout=5) as response:
        status = json.load(response)

    applications = status.get("applications", {})
    missing = expected - applications.keys()
    unhealthy = {
        name: app.get("status")
        for name, app in applications.items()
        if name in expected and app.get("status") != "RUNNING"
    }

    if missing or unhealthy:
        print(
            f"Ray Serve is not ready: missing={sorted(missing)} "
            f"unhealthy={unhealthy}",
            file=sys.stderr,
        )
        return 1

    print(f"Ray Serve ready: {len(expected)} applications running")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ray Serve readiness check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
