#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StrictReplayAuditError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Require full replay integrity and reproduced outcomes only; budget "
            "recoveries and expected transitions are failures."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        report = audit_strict_replay(
            payload,
            expected_records=args.expected_records,
        )
    except (StrictReplayAuditError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"strict replay audit failed: {error}") from error
    _write_json_atomic(Path(args.output), report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


def audit_strict_replay(
    manifest: Any,
    *,
    expected_records: int,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise StrictReplayAuditError("replay manifest must be an object")
    if expected_records <= 0:
        raise StrictReplayAuditError("expected record count must be positive")
    issues: list[str] = []
    if manifest.get("status") != "success":
        issues.append("bundle_status_not_success")

    integrity = manifest.get("record_integrity")
    if not isinstance(integrity, dict):
        issues.append("record_integrity_not_passed")
        counts: dict[str, Any] = {}
    else:
        counts = (
            integrity.get("counts")
            if isinstance(integrity.get("counts"), dict)
            else {}
        )
        if integrity.get("status") != "passed":
            issues.append("record_integrity_not_passed")
    for key in (
        "comparison_count",
        "export_attempted_count",
        "exported_count",
        "filter_matched_count",
        "result_count",
        "selected_count",
        "source_result_count",
        "trace_count",
    ):
        if counts.get(key) != expected_records:
            issues.append(f"{key}_mismatch")
    for key in ("limit_omitted_count", "replayability_dropped_count"):
        if counts.get(key) != 0:
            issues.append(f"{key}_nonzero")

    outcome = manifest.get("outcome_gate")
    if not isinstance(outcome, dict):
        issues.append("outcome_gate_not_passed")
        classifications: dict[str, Any] = {}
    else:
        classifications = (
            outcome.get("classification_counts")
            if isinstance(outcome.get("classification_counts"), dict)
            else {}
        )
        if outcome.get("status") != "passed":
            issues.append("outcome_gate_not_passed")
    if classifications.get("reproduced") != expected_records:
        issues.append("reproduced_count_mismatch")
    for key in (
        "budget_recovery",
        "expected_transition",
        "fixture_gap",
        "mismatch",
    ):
        if classifications.get(key) != 0:
            issues.append(f"{key}_nonzero")

    issues = sorted(set(issues))
    return {
        "schema_version": "1.0",
        "status": "passed" if not issues else "failed",
        "expected_records": expected_records,
        "integrity_counts": counts,
        "classification_counts": classifications,
        "issues": issues,
    }


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()
