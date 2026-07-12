from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.checkpoint import ADAPTER_VERSION, CHECKPOINT_SCHEMA_VERSION, checkpoint_metadata
from job_source_agent.models import RESULT_SCHEMA_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate replay input checkpoint metadata against current code.")
    parser.add_argument("--input", required=True, help="Replay input JSON produced by export_replay_input.py.")
    parser.add_argument("--summary-output", help="Optional JSON validation summary path.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    records = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit("Input must be a JSON array of replay records.")

    summary = validate_replay_records(records)
    if args.summary_output:
        Path(args.summary_output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        "replay validation: {compatible}/{total} compatible, {incompatible} incompatible".format(**summary),
        flush=True,
    )
    if summary["incompatible"]:
        raise SystemExit(1)


def validate_replay_records(records: list[dict]) -> dict:
    checks = [_validate_record(index, record) for index, record in enumerate(records)]
    incompatible = [check for check in checks if not check["compatible"]]
    return {
        "total": len(records),
        "compatible": len(records) - len(incompatible),
        "incompatible": len(incompatible),
        "expected": {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
        },
        "checks": checks,
    }


def _validate_record(index: int, record: dict) -> dict:
    checkpoint = record.get("checkpoint") if isinstance(record.get("checkpoint"), dict) else {}
    expected_metadata = checkpoint_metadata(record)
    failures = []
    for key in ("checkpoint_schema_version", "result_schema_version", "adapter_version", "input_fingerprint"):
        if checkpoint.get(key) != expected_metadata.get(key):
            failures.append(
                {
                    "field": key,
                    "expected": expected_metadata.get(key),
                    "actual": checkpoint.get(key),
                }
            )
    return {
        "index": index,
        "company_name": record.get("company_name"),
        "compatible": not failures,
        "failures": failures,
    }


if __name__ == "__main__":
    main()
