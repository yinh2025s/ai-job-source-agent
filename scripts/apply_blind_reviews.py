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

from job_source_agent.evaluation import EVALUATION_SCHEMA_VERSION, summarize_results, validate_evaluation_record
from job_source_agent.result_identity import identity_urls_equivalent
from scripts.blind_review_contract import BlindChainError, verify_execution_chain, verify_ssh_signature


SCHEMA_VERSION = "2.0"
DISPOSITIONS = {
    "exact_public", "verified_closed", "no_public_opening",
    "recruiter_client_undisclosed", "external_blocked", "system_gap",
}
IDENTITY_VERDICTS = {"verified", "rejected", "unreviewed", "not_applicable"}
RELATIONSHIPS = {
    "same_entity", "brand_parent", "acquired_brand", "alternate_employer",
    "recruiter_client_undisclosed", "unknown",
}
CHECK_VERDICTS = {"verified", "rejected", "unknown", "not_applicable"}
TITLE_VERDICTS = {"exact", "equivalent", "mismatch", "unknown", "not_applicable"}
LOCATION_VERDICTS = {"match", "compatible_remote", "mismatch", "unknown", "not_applicable"}
ACCESS_VERDICTS = {
    "publicly_accessible", "closed_or_removed", "access_blocked", "unknown", "not_applicable",
}
IDENTITY_FIELDS = {
    "company_name", "linkedin_job_url", "linkedin_job_title", "linkedin_job_location",
    "expected_open_position_url", "expected_candidate_opening_url",
}
CODEX_FIELDS = IDENTITY_FIELDS | {
    "suggested_record_disposition", "suggested_eligible_exact_opening",
    "suggested_identity_verdict", "evidence", "review_notes",
}
HUMAN_FIELDS = IDENTITY_FIELDS | {
    "hiring_entity_name", "hiring_relationship", "hiring_relationship_verdict",
    "provider", "provider_tenant", "canonical_board_url", "provider_tenant_verdict",
    "observed_opening_title", "title_verdict", "observed_opening_location",
    "location_verdict", "accessibility_verdict", "accessibility_checked_at",
    "record_disposition", "eligible_exact_opening", "identity_verdict", "evidence",
    "review_notes",
}


class BlindReviewError(ValueError):
    pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Merge human-labelled blind review separately from Codex artifact review.")
    for name in (
        "results", "trace", "summary", "cohort", "holdout-manifest",
        "execution-manifest", "codex-review", "human-review", "human-signature",
        "allowed-signers", "signer-identity", "output-trace", "output-summary",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        traces, summary = apply_reviews(
            Path(args.results), Path(args.trace), Path(args.summary),
            Path(args.cohort), Path(args.holdout_manifest), Path(args.execution_manifest),
            Path(args.codex_review), Path(args.human_review), Path(args.human_signature),
            Path(args.allowed_signers), args.signer_identity,
        )
    except (BlindReviewError, BlindChainError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"blind review merge failed: {error}") from error
    _write_json_atomic(Path(args.output_trace), traces)
    _write_json_atomic(Path(args.output_summary), summary)
    print(json.dumps(summary["evaluation_metrics"], sort_keys=True))


def apply_reviews(
    results_path: Path,
    trace_path: Path,
    summary_path: Path,
    cohort_path: Path,
    holdout_manifest_path: Path,
    execution_manifest_path: Path,
    codex_path: Path,
    human_path: Path,
    human_signature_path: Path,
    allowed_signers_path: Path,
    signer_identity: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results, traces, source_summary, provenance = verify_execution_chain(
        results_path=results_path, trace_path=trace_path, summary_path=summary_path,
        cohort_path=cohort_path, holdout_manifest_path=holdout_manifest_path,
        execution_manifest_path=execution_manifest_path,
    )
    codex_bytes, human_bytes = codex_path.read_bytes(), human_path.read_bytes()
    codex, human = json.loads(codex_bytes), json.loads(human_bytes)
    _validate_manifest(codex, "codex_artifact", provenance, CODEX_FIELDS)
    _validate_manifest(human, "user_human", provenance, HUMAN_FIELDS)
    if human["reviewer_id"] != signer_identity:
        raise BlindReviewError("human reviewer_id differs from verified signer identity")
    verify_ssh_signature(
        content=human_bytes, signature_path=human_signature_path,
        allowed_signers_path=allowed_signers_path, signer_identity=signer_identity,
    )
    codex_sha = hashlib.sha256(codex_bytes).hexdigest()

    result_map = _unique(results, "results")
    trace_map = _unique(traces, "trace")
    codex_map = _unique(codex["records"], "codex review")
    human_map = _unique(human["records"], "human review")
    if not (set(result_map) == set(trace_map) == set(codex_map) == set(human_map)):
        raise BlindReviewError("source and review cohort identities differ")

    annotated = []
    for result in results:
        key = _key(result)
        codex_record, human_record = codex_map[key], human_map[key]
        _validate_source_identity(result, codex_record)
        _validate_source_identity(result, human_record)
        _validate_codex_record(codex_record)
        _validate_human_record(result, human_record)
        evaluation = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "record_disposition": human_record["record_disposition"],
            "eligible_exact_opening": human_record["eligible_exact_opening"],
            "identity_verdict": human_record["identity_verdict"],
        }
        reviewed = {
            **result,
            "trace": trace_map[key].get("trace", {}),
            "evaluation": evaluation,
            "codex_artifact_review": codex_record,
            "human_evaluation_review": human_record,
        }
        try:
            validate_evaluation_record(reviewed)
        except ValueError as error:
            raise BlindReviewError(f"evaluation contract failed for {key!r}: {error}") from error
        annotated.append(reviewed)

    summary = {**source_summary, **summarize_results(annotated)}
    summary["review_manifests"] = {
        "codex_artifact": _review_provenance(codex, codex_sha),
        "user_human": _review_provenance(human, hashlib.sha256(human_bytes).hexdigest()),
        "metrics_authority": "user_human",
    }
    return annotated, summary


def _validate_manifest(manifest: Any, role: str, provenance: dict[str, str], fields: set[str]) -> None:
    top = {
        "schema_version", "review_type", "reviewer_id", "reviewed_at",
        *provenance.keys(), "records",
    }
    if not isinstance(manifest, dict) or set(manifest) != top:
        raise BlindReviewError(f"{role} manifest fields do not match schema")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BlindReviewError(f"{role} manifest schema/provenance is invalid")
    if manifest["review_type"] != role:
        raise BlindReviewError(f"{role} review_type is invalid")
    for key in ("reviewer_id", "reviewed_at"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise BlindReviewError(f"{role} {key} is required")
    for key, value in provenance.items():
        if manifest.get(key) != value:
            raise BlindReviewError(f"{role} {key} does not match execution chain")
    if not isinstance(manifest["records"], list) or not manifest["records"]:
        raise BlindReviewError(f"{role} records must be non-empty")
    if any(not isinstance(record, dict) or set(record) != fields for record in manifest["records"]):
        raise BlindReviewError(f"{role} record fields do not match schema")


def _validate_codex_record(record: dict[str, Any]) -> None:
    if record["suggested_record_disposition"] not in DISPOSITIONS:
        raise BlindReviewError("Codex disposition suggestion is invalid")
    if record["suggested_eligible_exact_opening"] not in (True, False, "unknown"):
        raise BlindReviewError("Codex eligibility suggestion is invalid")
    if record["suggested_identity_verdict"] not in IDENTITY_VERDICTS:
        raise BlindReviewError("Codex identity suggestion is invalid")
    _validate_evidence(record["evidence"])


def _validate_human_record(result: dict[str, Any], record: dict[str, Any]) -> None:
    if record["hiring_relationship"] not in RELATIONSHIPS:
        raise BlindReviewError("human hiring_relationship is invalid")
    if record["hiring_relationship_verdict"] not in CHECK_VERDICTS:
        raise BlindReviewError("human hiring relationship verdict is invalid")
    if record["provider_tenant_verdict"] not in CHECK_VERDICTS:
        raise BlindReviewError("human provider tenant verdict is invalid")
    if record["title_verdict"] not in TITLE_VERDICTS:
        raise BlindReviewError("human title verdict is invalid")
    if record["location_verdict"] not in LOCATION_VERDICTS:
        raise BlindReviewError("human location verdict is invalid")
    if record["accessibility_verdict"] not in ACCESS_VERDICTS:
        raise BlindReviewError("human accessibility verdict is invalid")
    if record["record_disposition"] not in DISPOSITIONS:
        raise BlindReviewError("human disposition is invalid")
    if record["eligible_exact_opening"] not in (True, False, "unknown"):
        raise BlindReviewError("human eligibility is invalid")
    if record["identity_verdict"] not in IDENTITY_VERDICTS:
        raise BlindReviewError("human identity verdict is invalid")
    _validate_evidence(record["evidence"])
    if result.get("open_position_url") and record["record_disposition"] == "exact_public":
        required = {
            "hiring_relationship_verdict": "verified",
            "provider_tenant_verdict": "verified",
            "accessibility_verdict": "publicly_accessible",
            "identity_verdict": "verified",
            "eligible_exact_opening": True,
        }
        if any(record[field] != expected for field, expected in required.items()):
            raise BlindReviewError("exact_public lacks required human verification")
        if record["title_verdict"] not in {"exact", "equivalent"}:
            raise BlindReviewError("exact_public title was not manually verified")
        if record["location_verdict"] not in {"match", "compatible_remote", "not_applicable"}:
            raise BlindReviewError("exact_public location was not manually verified")
        for field in (
            "hiring_entity_name", "provider", "provider_tenant", "canonical_board_url",
            "observed_opening_title", "accessibility_checked_at",
        ):
            if not isinstance(record[field], str) or not record[field].strip():
                raise BlindReviewError(f"exact_public requires human field {field}")
        evidence_by_kind = {item["kind"]: item for item in record["evidence"]}
        opening_evidence = evidence_by_kind.get("official_public_opening")
        board_evidence = evidence_by_kind.get("official_job_board")
        if not opening_evidence or not identity_urls_equivalent(
            opening_evidence["url"], result["open_position_url"]
        ):
            raise BlindReviewError("exact_public lacks evidence for the scored opening URL")
        if not board_evidence or not identity_urls_equivalent(
            board_evidence["url"], record["canonical_board_url"]
        ):
            raise BlindReviewError("exact_public lacks evidence for the canonical board")
        if "hiring_entity_identity" not in evidence_by_kind:
            raise BlindReviewError("exact_public lacks hiring entity evidence")


def _validate_evidence(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise BlindReviewError("review evidence must be non-empty")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "url", "finding"}:
            raise BlindReviewError("review evidence entry is invalid")
        if not all(isinstance(item[field], str) and item[field].strip() for field in item):
            raise BlindReviewError("review evidence values must be non-empty strings")
        if not _safe_https(item["url"]):
            raise BlindReviewError("review evidence URL must be public HTTPS")


def _validate_source_identity(result: dict[str, Any], review: dict[str, Any]) -> None:
    expected = {
        "company_name": result.get("company_name"),
        "linkedin_job_url": result.get("linkedin_job_url"),
        "linkedin_job_title": result.get("linkedin_job_title"),
        "linkedin_job_location": result.get("linkedin_job_location"),
        "expected_open_position_url": result.get("open_position_url"),
        "expected_candidate_opening_url": result.get("candidate_open_position_url"),
    }
    if any(review.get(key) != value for key, value in expected.items()):
        raise BlindReviewError("review source identity or expected URL drifted")


def _key(record: dict[str, Any]) -> tuple[Any, ...]:
    key = tuple(record.get(field) for field in (
        "company_name", "linkedin_job_url", "linkedin_job_title", "linkedin_job_location"
    ))
    if any(not isinstance(value, str) or not value.strip() for value in key):
        raise BlindReviewError("record identity fields must be non-empty strings")
    return key


def _unique(records: list[dict[str, Any]], label: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    indexed = {}
    for record in records:
        key = _key(record)
        if key in indexed:
            raise BlindReviewError(f"{label} contains duplicate record identity")
        indexed[key] = record
    return indexed


def _safe_https(value: str) -> bool:
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _review_provenance(manifest: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"], "review_type": manifest["review_type"],
        "reviewer_id": manifest["reviewer_id"], "reviewed_at": manifest["reviewed_at"],
        "manifest_sha256": digest, "reviewed_record_count": len(manifest["records"]),
    }


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


if __name__ == "__main__":
    main()
