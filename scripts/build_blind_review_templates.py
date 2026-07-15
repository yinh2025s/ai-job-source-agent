#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.blind_review_contract import verify_execution_chain


def main() -> None:
    parser = argparse.ArgumentParser(description="Build separate Codex and human blind-review templates.")
    parser.add_argument("--results", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--execution-manifest", required=True)
    parser.add_argument("--output-codex", required=True)
    parser.add_argument("--output-human", required=True)
    args = parser.parse_args()
    results_path = Path(args.results)
    trace_path = Path(args.trace)
    summary_path = Path(args.summary)
    results, _traces, _summary, provenance = verify_execution_chain(
        results_path=results_path, trace_path=trace_path, summary_path=summary_path,
        cohort_path=Path(args.cohort), holdout_manifest_path=Path(args.holdout_manifest),
        execution_manifest_path=Path(args.execution_manifest),
    )
    common = {
        "schema_version": "2.0",
        **provenance,
    }
    codex = {
        **common,
        "review_type": "codex_artifact",
        "reviewer_id": "Codex artifact review",
        "reviewed_at": None,
        "records": [_codex_record(record) for record in results],
    }
    human = {
        **common,
        "review_type": "user_human",
        "reviewer_id": None,
        "reviewed_at": None,
        "records": [_human_record(record) for record in results],
    }
    _write_json_atomic(Path(args.output_codex), codex)
    _write_json_atomic(Path(args.output_human), human)


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_name": record.get("company_name"),
        "linkedin_job_url": record.get("linkedin_job_url"),
        "linkedin_job_title": record.get("linkedin_job_title"),
        "linkedin_job_location": record.get("linkedin_job_location"),
        "expected_open_position_url": record.get("open_position_url"),
        "expected_candidate_opening_url": record.get("candidate_open_position_url"),
    }


def _codex_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **_identity(record),
        "suggested_record_disposition": None,
        "suggested_eligible_exact_opening": None,
        "suggested_identity_verdict": None,
        "evidence": [],
        "review_notes": None,
    }


def _human_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **_identity(record),
        "hiring_entity_name": None,
        "hiring_relationship": None,
        "hiring_relationship_verdict": None,
        "provider": record.get("provider"),
        "provider_tenant": None,
        "canonical_board_url": record.get("job_list_page_url"),
        "provider_tenant_verdict": None,
        "observed_opening_title": None,
        "title_verdict": None,
        "observed_opening_location": None,
        "location_verdict": None,
        "accessibility_verdict": None,
        "accessibility_checked_at": None,
        "record_disposition": None,
        "eligible_exact_opening": None,
        "identity_verdict": None,
        "evidence": [],
        "review_notes": None,
    }


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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
