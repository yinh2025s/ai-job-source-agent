#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.evaluation_history import EvaluationHistory, run_record
from job_source_agent.checkpoint import ADAPTER_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive an evaluator summary and compare it with the latest baseline.")
    parser.add_argument("--summary", required=True, help="Existing evaluator summary JSON; its schema is preserved.")
    parser.add_argument("--history-dir", required=True, help="Destination evaluation history directory.")
    parser.add_argument("--label", help="Optional human-readable run label.")
    parser.add_argument("--no-baseline", action="store_true", help="Archive without comparing to the latest run.")
    parser.add_argument("--commit-sha", help="Commit SHA for the evaluated code; defaults to the current Git HEAD.")
    parser.add_argument("--benchmark-command", help="Original benchmark command that produced the summary.")
    args = parser.parse_args()

    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    metadata = {
        "adapter_version": ADAPTER_VERSION,
        "commit_sha": args.commit_sha or _current_commit(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }
    if args.benchmark_command:
        metadata["benchmark_command"] = args.benchmark_command
    run = EvaluationHistory(args.history_dir).archive(
        summary,
        label=args.label,
        metadata=metadata,
        compare_with_latest=not args.no_baseline,
    )
    print(json.dumps(run_record(run), ensure_ascii=True, sort_keys=True))


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


if __name__ == "__main__":
    main()
