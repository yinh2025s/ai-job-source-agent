#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_source_agent.runtime import inspect_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the project Python runtime.")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Require the pinned CPython 3.12 release baseline.",
    )
    args = parser.parse_args()
    status = inspect_runtime()
    version = ".".join(str(part) for part in status.version)
    print(f"runtime: {status.implementation} {version}")
    print(status.detail)
    accepted = status.release_compatible if args.release else status.supported
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
