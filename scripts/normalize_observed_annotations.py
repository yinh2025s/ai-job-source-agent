#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
UNKNOWN = "unknown"
RECORD_FIELDS = {
    "company_name",
    "linkedin_job_url",
    "target_title",
    "target_location",
    "observed_result",
    "expected_disposition",
    "expected_opening_url",
    "eligible_exact_opening",
    "failure_stage",
    "root_cause",
    "evidence",
    "reviewer_notes",
}
DISPOSITION_ELIGIBILITY: dict[str, bool | str] = {
    "system_gap": True,
    "verified_closed": False,
    "no_public_opening": False,
    "external_blocked": UNKNOWN,
    "identity_rejected": UNKNOWN,
    "eligibility_unknown": UNKNOWN,
}
RECORD_RE = re.compile(
    r"^- \[(?P<checked>[ xX])\] \*\*(?P<company>.+?)\*\* - .*?"
    r"\[LinkedIn\]\((?P<linkedin>https://www\.linkedin\.com/jobs/view/[^)]+)\)"
)
FIELD_RE = re.compile(
    r"^  - (?P<label>Automated evidence|Later investigation|Later targeted result|"
    r"Later targeted recovery|Review warning|Manual finding|Manual website / finding|"
    r"Manual Career URL / finding|Manual disposition):\s*(?P<value>.*)$"
)
LINK_RE = re.compile(r"\[[^]]+\]\((https?://[^)]+)\)")


class AnnotationNormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownRecord:
    company_name: str
    linkedin_job_url: str
    reviewed: bool
    fields: dict[str, list[str]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize manual review notes against one frozen observed run."
    )
    parser.add_argument("--checklist", required=True)
    parser.add_argument("--raw-annotations", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--annotation-sha256", required=True)
    parser.add_argument("--results-sha256", required=True)
    parser.add_argument("--trace-sha256", required=True)
    parser.add_argument("--summary-sha256", required=True)
    parser.add_argument("--companies-sha256", required=True)
    parser.add_argument("--run-configuration-digest", required=True)
    parser.add_argument("--reviewed-at", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    expected = {
        "source_annotation_sha256": args.annotation_sha256,
        "source_results_sha256": args.results_sha256,
        "source_trace_sha256": args.trace_sha256,
        "source_summary_sha256": args.summary_sha256,
        "companies_sha256": args.companies_sha256,
        "run_configuration_digest": args.run_configuration_digest,
    }
    try:
        payload = normalize_annotations(
            checklist_path=args.checklist,
            raw_annotations_path=args.raw_annotations,
            results_path=args.results,
            trace_path=args.trace,
            summary_path=args.summary,
            expected_bindings=expected,
            reviewed_at=args.reviewed_at,
        )
        _write_json_atomic(Path(args.output), payload)
    except (AnnotationNormalizationError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"annotation normalization failed: {error}") from error
    print(json.dumps({"records": len(payload["records"]), "output": args.output}))


def normalize_annotations(
    *,
    checklist_path: str | Path,
    raw_annotations_path: str | Path,
    results_path: str | Path,
    trace_path: str | Path,
    summary_path: str | Path,
    expected_bindings: dict[str, str],
    reviewed_at: str,
) -> dict[str, Any]:
    files = {
        "source_annotation_sha256": Path(raw_annotations_path),
        "source_results_sha256": Path(results_path),
        "source_trace_sha256": Path(trace_path),
        "source_summary_sha256": Path(summary_path),
    }
    _validate_digest_bindings(files, expected_bindings)
    if not isinstance(reviewed_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", reviewed_at
    ):
        raise AnnotationNormalizationError("reviewed_at must be an ISO-8601 timestamp")

    checklist = _index_markdown_records(
        _parse_markdown(Path(checklist_path).read_text(encoding="utf-8")), "checklist"
    )
    raw = _index_markdown_records(
        _parse_markdown(Path(raw_annotations_path).read_text(encoding="utf-8")),
        "raw annotations",
    )
    results = _load_record_list(Path(results_path), "results")
    traces = _load_record_list(Path(trace_path), "trace")
    result_index = _index_runtime_records(results, "results")
    trace_index = _index_runtime_records(traces, "trace")
    if set(result_index) != set(trace_index):
        raise AnnotationNormalizationError("results and trace cohort identities differ")

    failure_keys = {
        key for key, record in result_index.items() if record.get("open_position_url") is None
    }
    if set(checklist) != failure_keys:
        _raise_cohort_mismatch("checklist", failure_keys, set(checklist))
    if set(raw) != failure_keys:
        _raise_cohort_mismatch("raw annotations", failure_keys, set(raw))

    summary = json.loads(Path(summary_path).read_bytes())
    if not isinstance(summary, dict):
        raise AnnotationNormalizationError("summary must contain a JSON object")
    evaluation_manifest = summary.get("evaluation_manifest")
    if not isinstance(evaluation_manifest, dict):
        raise AnnotationNormalizationError("summary evaluation_manifest is missing")
    _require_binding(
        "companies_sha256",
        evaluation_manifest.get("companies_sha256"),
        expected_bindings,
    )
    _require_binding(
        "run_configuration_digest",
        summary.get("run_configuration_digest"),
        expected_bindings,
    )
    if evaluation_manifest.get("run_configuration_digest") != summary.get(
        "run_configuration_digest"
    ):
        raise AnnotationNormalizationError("summary run configuration digests differ")

    records = []
    for result in results:
        key = _runtime_key(result)
        if key not in failure_keys:
            continue
        record = _normalize_record(result, checklist[key], raw[key])
        if set(record) != RECORD_FIELDS:
            raise AssertionError("normalized record fields drifted from schema")
        records.append(record)

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest": {
            **{name: _sha256(path) for name, path in files.items()},
            "companies_sha256": expected_bindings["companies_sha256"],
            "run_configuration_digest": expected_bindings[
                "run_configuration_digest"
            ],
            "reviewed_at": reviewed_at,
            "cohort_record_count": len(results),
            "annotation_record_count": len(records),
        },
        "records": records,
    }


def _normalize_record(
    result: dict[str, Any], checklist: MarkdownRecord, raw: MarkdownRecord
) -> dict[str, Any]:
    dispositions = checklist.fields.get("Manual disposition", [])
    if checklist.reviewed:
        if len(dispositions) != 1:
            raise AnnotationNormalizationError(
                f"reviewed record {checklist.company_name!r} needs one disposition"
            )
        disposition = dispositions[0].split()[0].strip("`")
        if disposition not in DISPOSITION_ELIGIBILITY:
            raise AnnotationNormalizationError(
                f"unsupported disposition for {checklist.company_name!r}: {disposition!r}"
            )
        eligibility: bool | str = DISPOSITION_ELIGIBILITY[disposition]
    else:
        if dispositions:
            raise AnnotationNormalizationError(
                f"unreviewed record {checklist.company_name!r} has a disposition"
            )
        disposition = UNKNOWN
        eligibility = UNKNOWN

    raw_findings = _manual_findings(raw)
    if checklist.reviewed and not raw_findings:
        raise AnnotationNormalizationError(
            f"reviewed record {checklist.company_name!r} lacks original manual evidence"
        )
    if not checklist.reviewed and any(value.strip() for value in raw_findings):
        raise AnnotationNormalizationError(
            f"unreviewed record {checklist.company_name!r} contains manual evidence"
        )

    failure = _failure_stage(result)
    evidence = []
    for label, values in checklist.fields.items():
        if label == "Manual disposition":
            continue
        for value in values:
            if value.strip():
                evidence.append(
                    {
                        "source": _evidence_source(label),
                        "finding": value.strip(),
                        "urls": LINK_RE.findall(value),
                    }
                )
    if not evidence:
        raise AnnotationNormalizationError(
            f"record {checklist.company_name!r} has no evidence"
        )

    return {
        "company_name": result["company_name"],
        "linkedin_job_url": result["linkedin_job_url"],
        "target_title": result["linkedin_job_title"],
        "target_location": result["linkedin_job_location"],
        "observed_result": {
            "pipeline_status": result.get("pipeline_status"),
            "error_code": result.get("error_code"),
            "company_website_url": result.get("company_website_url"),
            "career_page_url": result.get("career_page_url"),
            "job_list_page_url": result.get("job_list_page_url"),
            "open_position_url": result.get("open_position_url"),
        },
        "expected_disposition": disposition,
        "expected_opening_url": _expected_opening_url(checklist, disposition),
        "eligible_exact_opening": eligibility,
        "failure_stage": failure["stage"],
        "root_cause": failure["reason_code"],
        "evidence": evidence,
        "reviewer_notes": "\n".join(raw_findings).strip() or "Pending manual review.",
    }


def _parse_markdown(content: str) -> list[MarkdownRecord]:
    records: list[MarkdownRecord] = []
    current: dict[str, Any] | None = None
    for line in content.splitlines():
        match = RECORD_RE.match(line)
        if match:
            if current is not None:
                records.append(MarkdownRecord(**current))
            current = {
                "company_name": match.group("company"),
                "linkedin_job_url": match.group("linkedin"),
                "reviewed": match.group("checked").lower() == "x",
                "fields": {},
            }
            continue
        field_match = FIELD_RE.match(line)
        if current is not None and field_match:
            current["fields"].setdefault(field_match.group("label"), []).append(
                field_match.group("value")
            )
    if current is not None:
        records.append(MarkdownRecord(**current))
    if not records:
        raise AnnotationNormalizationError("annotation markdown contains no records")
    return records


def _index_markdown_records(
    records: list[MarkdownRecord], label: str
) -> dict[tuple[str, str], MarkdownRecord]:
    indexed = {}
    for record in records:
        key = (record.company_name, record.linkedin_job_url)
        if key in indexed:
            raise AnnotationNormalizationError(f"{label} contains duplicate record {key!r}")
        indexed[key] = record
    return indexed


def _load_record_list(path: Path, label: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, list) or not payload or not all(
        isinstance(item, dict) for item in payload
    ):
        raise AnnotationNormalizationError(f"{label} must be a non-empty array of objects")
    return payload


def _index_runtime_records(
    records: list[dict[str, Any]], label: str
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed = {}
    for record in records:
        key = _runtime_key(record)
        if key in indexed:
            raise AnnotationNormalizationError(f"{label} contains duplicate record {key!r}")
        indexed[key] = record
    return indexed


def _runtime_key(record: dict[str, Any]) -> tuple[str, str]:
    values = (record.get("company_name"), record.get("linkedin_job_url"))
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise AnnotationNormalizationError("runtime record identity is invalid")
    return values  # type: ignore[return-value]


def _failure_stage(result: dict[str, Any]) -> dict[str, str]:
    stages = result.get("stages")
    failures = [
        stage
        for stage in stages if isinstance(stages, list) and isinstance(stage, dict)
        and stage.get("reason_code")
    ] if isinstance(stages, list) else []
    if len(failures) != 1:
        raise AnnotationNormalizationError(
            f"record {result.get('company_name')!r} must have one typed failure stage"
        )
    stage = failures[0]
    if not isinstance(stage.get("stage"), str) or not isinstance(
        stage.get("reason_code"), str
    ):
        raise AnnotationNormalizationError("failure stage is malformed")
    return {"stage": stage["stage"], "reason_code": stage["reason_code"]}


def _manual_findings(record: MarkdownRecord) -> list[str]:
    labels = ("Manual finding", "Manual website / finding", "Manual Career URL / finding")
    return [value for label in labels for value in record.fields.get(label, [])]


def _expected_opening_url(record: MarkdownRecord, disposition: str) -> str | None:
    if disposition != "system_gap":
        return None
    candidates = []
    for label in (
        "Later targeted recovery",
        "Later targeted result",
        "Manual website / finding",
        "Manual Career URL / finding",
    ):
        for value in record.fields.get(label, []):
            links = LINK_RE.findall(value)
            if label.startswith("Later targeted"):
                exact = re.search(r"\[exact opening\]\((https?://[^)]+)\)", value)
                if exact:
                    candidates.append(exact.group(1))
            elif links:
                candidates.extend(links)
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 1:
        raise AnnotationNormalizationError(
            f"record {record.company_name!r} has ambiguous expected opening URLs"
        )
    return unique[0] if unique else None


def _evidence_source(label: str) -> str:
    if label.startswith("Manual"):
        return "manual_review"
    if label.startswith("Later"):
        return "targeted_follow_up"
    if label == "Review warning":
        return "review_warning"
    return "frozen_runtime"


def _validate_digest_bindings(
    files: dict[str, Path], expected_bindings: dict[str, str]
) -> None:
    required = set(files) | {"companies_sha256", "run_configuration_digest"}
    if set(expected_bindings) != required:
        raise AnnotationNormalizationError("expected binding fields do not match schema")
    for name, value in expected_bindings.items():
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise AnnotationNormalizationError(f"{name} must be a lowercase SHA-256 digest")
    for name, path in files.items():
        _require_binding(name, _sha256(path), expected_bindings)


def _require_binding(name: str, actual: object, expected: dict[str, str]) -> None:
    if actual != expected[name]:
        raise AnnotationNormalizationError(f"{name} digest does not match frozen binding")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raise_cohort_mismatch(
    label: str, expected: set[tuple[str, str]], actual: set[tuple[str, str]]
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise AnnotationNormalizationError(
        f"{label} cohort mismatch: missing={missing!r}, extra={extra!r}"
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
