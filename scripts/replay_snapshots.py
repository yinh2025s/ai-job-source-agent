from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate sanitized snapshots and create deterministic offline fixtures."
    )
    parser.add_argument("--snapshot-dir", required=True, help="Directory containing snapshots.jsonl.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Replay root; pass its sites/ child to Fetcher(fixtures_dir=...).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = replay_snapshots(args.snapshot_dir, args.output_dir)
    except SnapshotReplayError as exc:
        raise SystemExit(f"snapshot replay failed: {exc}") from exc
    print(json.dumps(result.summary, sort_keys=True), flush=True)
    print(f"fixtures: {Path(args.output_dir) / 'sites'}", flush=True)
    print(f"manifest: {result.manifest_path}", flush=True)
    print(f"summary: {result.summary_path}", flush=True)


if __name__ == "__main__":
    main()
