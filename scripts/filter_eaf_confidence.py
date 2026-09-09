#!/usr/bin/env python3
"""Command-line wrapper for the shared EAF export filter."""

from __future__ import annotations

import sys
from pathlib import Path


_BACKEND_SOURCE = Path(__file__).resolve().parents[1] / "backend" / "src" / "backend"
sys.path.insert(0, str(_BACKEND_SOURCE))

from backend.eaf_filter import *  # noqa: F403,E402
from backend.eaf_filter import main as _main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_main())
