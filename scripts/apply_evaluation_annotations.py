#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    summarize_results,
    validate_evaluation_record,
)


ANNOTATION_MANIFEST_SCHEMA_VERSION = "1.0"
RECORD_FIELDS = {
    "company_name",
    "linkedin_job_url",
    "linkedin_job_title",
    "expected_open_position_url",
    "expected_candidate_opening_url",
    "record_disposition",
    "eligible_exact_opening",
    "identity_verdict",
    "evidence",
    "review_notes",
}
EVIDENCE_FIELDS = {"kind", "url", "finding"}


class EvaluationAnnotationError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind independent evaluation annotations to one frozen result cohort."
    )
    parser.add_argument("--results", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--output-trace", required=True)
    parser.add_argument("--output-summary", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        annotated, summary = apply_annotations(
            args.results,
            args.trace,
            args.summary,
            args.annotations,
        )
    except (EvaluationAnnotationError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"evaluation annotation failed: {error}") from error
    _write_json_atomic(Path(args.output_trace), annotated)
    _write_json_atomic(Path(args.output_summary), summary)
    metrics = summary["evaluation_metrics"]
    print(
        json.dumps(
            {
                "annotation_coverage": metrics["annotation_coverage"],
                "conditional_exact_recall": metrics["conditional_exact_recall"],
                "exact_precision": metrics["exact_precision"],
                "raw_exact_rate": metrics["raw_exact_rate"],
                "system_defect_rate": metrics["system_defect_rate"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


def apply_annotations(
    results_path: str | Path,
    trace_path: str | Path,
    summary_path: str | Path,
    annotations_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results_file = Path(results_path)
    trace_file = Path(trace_path)
    summary_file = Path(summary_path)
    annotations_file = Path(annotations_path)
    results_bytes = results_file.read_bytes()
    trace_bytes = trace_file.read_bytes()
    summary_bytes = summary_file.read_bytes()
    annotation_bytes = annotations_file.read_bytes()
    results = _load_record_list(results_bytes, "results")
    traces = _load_record_list(trace_bytes, "trace")
    source_summary = json.loads(summary_bytes)
    if not isinstance(source_summary, dict):
        raise EvaluationAnnotationError("summary must contain a JSON object")
    manifest = json.loads(annotation_bytes)
    _validate_manifest(
        manifest,
        results_sha256=hashlib.sha256(results_bytes).hexdigest(),
        trace_sha256=hashlib.sha256(trace_bytes).hexdigest(),
        summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
    )

    result_by_key = _unique_records_by_key(results, "results")
    trace_by_key = _unique_records_by_key(traces, "trace")
    if set(result_by_key) != set(trace_by_key):
        raise EvaluationAnnotationError("results and trace cohort identities differ")

    annotations = manifest["records"]
    annotation_by_key = _unique_annotations_by_key(annotations)
    if set(annotation_by_key) != set(result_by_key):
        missing = sorted(set(result_by_key) - set(annotation_by_key))
        extra = sorted(set(annotation_by_key) - set(result_by_key))
        raise EvaluationAnnotationError(
            f"annotation cohort mismatch: missing={missing!r}, extra={extra!r}"
        )

    annotated: list[dict[str, Any]] = []
    for result in results:
        key = _record_key(result)
        annotation = annotation_by_key[key]
        _validate_expected_identity(result, annotation)
        evaluation = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "record_disposition": annotation["record_disposition"],
            "eligible_exact_opening": annotation["eligible_exact_opening"],
            "identity_verdict": annotation["identity_verdict"],
        }
        reviewed = {
            **trace_by_key[key],
            "evaluation": evaluation,
            "evaluation_review": {
                "evidence": annotation["evidence"],
                "notes": annotation["review_notes"],
            },
        }
        try:
            validate_evaluation_record(reviewed)
        except ValueError as error:
            raise EvaluationAnnotationError(
                f"invalid annotation for {key!r}: {error}"
            ) from error
        annotated.append(reviewed)

    summary = {**source_summary, **summarize_results(annotated)}
    summary["review_manifest"] = {
        "schema_version": ANNOTATION_MANIFEST_SCHEMA_VERSION,
        "cohort_provenance": manifest["cohort_provenance"],
        "reviewed_at": manifest["reviewed_at"],
        "review_method": manifest["review_method"],
        "reviewer": manifest["reviewer"],
        "source_results_sha256": manifest["source_results_sha256"],
        "source_trace_sha256": manifest["source_trace_sha256"],
        "source_summary_sha256": manifest["source_summary_sha256"],
        "annotations_sha256": hashlib.sha256(annotation_bytes).hexdigest(),
        "reviewed_record_count": len(annotated),
    }
    return annotated, summary


def _load_record_list(content: bytes, label: str) -> list[dict[str, Any]]:
    payload = json.loads(content)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise EvaluationAnnotationError(f"{label} must contain a JSON array of objects")
    if not payload:
        raise EvaluationAnnotationError(f"{label} must not be empty")
    return payload


def _validate_manifest(
    manifest: object,
    *,
    results_sha256: str,
    trace_sha256: str,
    summary_sha256: str,
) -> None:
    expected_fields = {
        "schema_version",
        "cohort_provenance",
        "reviewed_at",
        "review_method",
        "reviewer",
        "source_results_sha256",
        "source_trace_sha256",
        "source_summary_sha256",
        "records",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise EvaluationAnnotationError("annotation manifest fields do not match schema")
    if manifest["schema_version"] != ANNOTATION_MANIFEST_SCHEMA_VERSION:
        raise EvaluationAnnotationError("annotation manifest schema version is unsupported")
    if manifest["cohort_provenance"] != "frozen_observed":
        raise EvaluationAnnotationError("cohort provenance must be frozen_observed")
    for field in ("reviewed_at", "review_method", "reviewer"):
        value = manifest[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise EvaluationAnnotationError(f"annotation {field} is invalid")
    if manifest["source_results_sha256"] != results_sha256:
        raise EvaluationAnnotationError("source results digest does not match")
    if manifest["source_trace_sha256"] != trace_sha256:
        raise EvaluationAnnotationError("source trace digest does not match")
    if manifest["source_summary_sha256"] != summary_sha256:
        raise EvaluationAnnotationError("source summary digest does not match")
    if not isinstance(manifest["records"], list) or not manifest["records"]:
        raise EvaluationAnnotationError("annotation records must be a non-empty array")


def _unique_records_by_key(
    records: list[dict[str, Any]],
    label: str,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = _record_key(record)
        if key in indexed:
            raise EvaluationAnnotationError(f"{label} contains duplicate identity {key!r}")
        indexed[key] = record
    return indexed


def _unique_annotations_by_key(
    annotations: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for annotation in annotations:
        _validate_annotation(annotation)
        key = _record_key(annotation)
        if key in indexed:
            raise EvaluationAnnotationError(f"annotations contain duplicate identity {key!r}")
        indexed[key] = annotation
    return indexed


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    values = tuple(
        record.get(field)
        for field in ("company_name", "linkedin_job_url", "linkedin_job_title")
    )
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise EvaluationAnnotationError("record identity fields must be non-empty strings")
    return values  # type: ignore[return-value]


def _validate_annotation(annotation: object) -> None:
    if not isinstance(annotation, dict) or set(annotation) != RECORD_FIELDS:
        raise EvaluationAnnotationError("annotation record fields do not match schema")
    for field in ("expected_open_position_url", "expected_candidate_opening_url"):
        value = annotation[field]
        if value is not None and not _safe_https_url(value):
            raise EvaluationAnnotationError(f"annotation {field} must be HTTPS or null")
    evidence = annotation["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise EvaluationAnnotationError("annotation evidence must be a non-empty array")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            raise EvaluationAnnotationError("annotation evidence fields do not match schema")
        if not all(isinstance(item[field], str) and item[field].strip() for field in EVIDENCE_FIELDS):
            raise EvaluationAnnotationError("annotation evidence values must be non-empty strings")
        if not _safe_https_url(item["url"]):
            raise EvaluationAnnotationError("annotation evidence URL must be HTTPS")
    notes = annotation["review_notes"]
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 1000:
        raise EvaluationAnnotationError("annotation review_notes is invalid")


def _validate_expected_identity(
    result: dict[str, Any],
    annotation: dict[str, Any],
) -> None:
    if result.get("open_position_url") != annotation["expected_open_position_url"]:
        raise EvaluationAnnotationError(
            f"opening URL drift for {_record_key(result)!r}"
        )
    assertion = result.get("identity_assertion")
    candidate = assertion.get("candidate_opening_url") if isinstance(assertion, dict) else None
    if candidate != annotation["expected_candidate_opening_url"]:
        raise EvaluationAnnotationError(
            f"candidate opening URL drift for {_record_key(result)!r}"
        )


def _safe_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
