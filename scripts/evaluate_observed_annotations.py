#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.result_identity import identity_urls_equivalent


SCHEMA_VERSION = "1.0"


class ObservedEvaluationError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate one observed development cohort against frozen manual annotations."
    )
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate(args.annotations, args.results)
    except (ObservedEvaluationError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"observed annotation evaluation failed: {error}") from error
    _write_json_atomic(Path(args.output), report)
    print(json.dumps(report["metrics"], sort_keys=True), flush=True)


def evaluate(
    annotations_path: str | Path,
    results_path: str | Path,
) -> dict[str, Any]:
    annotations = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))
    if not isinstance(annotations, dict) or set(annotations) not in (
        {"manifest", "records"},
        {"manifest", "records", "schema_version"},
    ):
        raise ObservedEvaluationError("annotation payload fields do not match schema")
    if "schema_version" in annotations and annotations["schema_version"] != "1.0":
        raise ObservedEvaluationError("annotation schema version is unsupported")
    manifest = annotations["manifest"]
    records = annotations["records"]
    if not isinstance(manifest, dict) or not isinstance(records, list):
        raise ObservedEvaluationError("annotation manifest or records are invalid")
    if manifest.get("annotation_record_count") != len(records):
        raise ObservedEvaluationError("annotation record count does not match manifest")
    if not isinstance(results, list) or not results or not all(
        isinstance(record, dict) for record in results
    ):
        raise ObservedEvaluationError("results must be a non-empty JSON record array")
    if manifest.get("cohort_record_count") != len(results):
        raise ObservedEvaluationError("result cohort count does not match annotation manifest")

    result_by_key = _unique_by_key(results, "results")
    annotation_by_key = _unique_by_key(records, "annotations")
    missing = sorted(set(annotation_by_key) - set(result_by_key))
    if missing:
        raise ObservedEvaluationError(f"annotated records are missing from results: {missing!r}")

    evaluated = []
    eligible_count = 0
    recovered_count = 0
    system_gap_remaining = 0
    unsafe_exact_count = 0
    wrong_expected_url_count = 0
    negative_control_exact_count = 0
    independently_reviewable_exact_count = 0
    independently_correct_exact_count = 0
    unknown_exact_count = 0
    for annotation in records:
        result = result_by_key[_record_key(annotation)]
        opening = result.get("open_position_url")
        exact_output = isinstance(opening, str) and bool(opening.strip())
        identity_verified = _identity_verified(result)
        safe_exact = exact_output and identity_verified and _s7_success(result)
        expected_url = annotation.get("expected_opening_url")
        url_verdict = "not_applicable"
        if exact_output and isinstance(expected_url, str):
            independently_reviewable_exact_count += 1
            if identity_urls_equivalent(opening, expected_url):
                independently_correct_exact_count += 1
                url_verdict = "matches_expected"
            else:
                wrong_expected_url_count += 1
                url_verdict = "wrong_expected_url"
        elif exact_output:
            unknown_exact_count += 1
            url_verdict = "requires_independent_review"
        if exact_output and not safe_exact:
            unsafe_exact_count += 1

        eligible = annotation.get("eligible_exact_opening") is True
        if eligible:
            eligible_count += 1
            if safe_exact:
                recovered_count += 1
            else:
                system_gap_remaining += 1
        elif exact_output and annotation.get("expected_disposition") in {
            "verified_closed",
            "no_public_opening",
            "external_blocked",
            "identity_rejected",
        }:
            negative_control_exact_count += 1

        evaluated.append(
            {
                "company_name": annotation.get("company_name"),
                "linkedin_job_url": annotation.get("linkedin_job_url"),
                "expected_disposition": annotation.get("expected_disposition"),
                "eligible_exact_opening": annotation.get("eligible_exact_opening"),
                "open_position_url": opening if exact_output else None,
                "safe_exact": safe_exact,
                "url_verdict": url_verdict,
            }
        )

    raw_exact_count = sum(bool(record.get("open_position_url")) for record in results)
    unannotated_exact_count = sum(
        bool(record.get("open_position_url"))
        for key, record in result_by_key.items()
        if key not in annotation_by_key
    )
    precision_unknown_count = unknown_exact_count + unannotated_exact_count
    annotated_count = len(records)
    cohort_count = len(results)
    exact_precision_status = (
        "available"
        if precision_unknown_count == 0 and independently_reviewable_exact_count > 0
        else "not_reportable"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort_classification": "observed_development",
        "annotation_manifest": manifest,
        "metrics": {
            "annotation_coverage": _metric(annotated_count, cohort_count),
            "raw_exact_rate": _metric(raw_exact_count, cohort_count),
            "conditional_exact_recall": _metric(recovered_count, eligible_count),
            "system_defect_rate": _metric(system_gap_remaining, eligible_count),
            "exact_precision": _metric(
                independently_correct_exact_count,
                independently_reviewable_exact_count,
                status=exact_precision_status,
                unknown_count=precision_unknown_count,
            ),
            "unsafe_exact_count": unsafe_exact_count,
            "wrong_expected_url_count": wrong_expected_url_count,
            "negative_control_exact_requires_review": negative_control_exact_count,
            "unannotated_record_count": cohort_count - annotated_count,
            "unannotated_exact_count": unannotated_exact_count,
        },
        "records": evaluated,
    }


def _record_key(record: dict[str, Any]) -> tuple[str, str]:
    company = record.get("company_name")
    linkedin = record.get("linkedin_job_url")
    if not isinstance(company, str) or not company.strip():
        raise ObservedEvaluationError("record company_name is missing")
    if not isinstance(linkedin, str) or not linkedin.strip():
        raise ObservedEvaluationError("record linkedin_job_url is missing")
    return company.strip().casefold(), linkedin.strip()


def _unique_by_key(records: list[dict[str, Any]], label: str) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = _record_key(record)
        if key in indexed:
            raise ObservedEvaluationError(f"{label} contains duplicate record {key!r}")
        indexed[key] = record
    return indexed


def _identity_verified(result: dict[str, Any]) -> bool:
    assertion = result.get("identity_assertion")
    return isinstance(assertion, dict) and assertion.get("verdict") == "verified"


def _s7_success(result: dict[str, Any]) -> bool:
    stages = result.get("stages")
    if not isinstance(stages, list):
        return False
    return any(
        isinstance(stage, dict)
        and stage.get("stage") == "result_validation"
        and stage.get("status") == "success"
        for stage in stages
    )


def _metric(
    numerator: int,
    denominator: int,
    *,
    status: str | None = None,
    unknown_count: int = 0,
) -> dict[str, Any]:
    resolved_status = status or ("available" if denominator else "not_reportable")
    return {
        "value": (
            round(numerator / denominator, 3)
            if denominator and resolved_status == "available"
            else None
        ),
        "numerator": numerator,
        "denominator": denominator,
        "unknown_count": unknown_count,
        "status": resolved_status,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()
