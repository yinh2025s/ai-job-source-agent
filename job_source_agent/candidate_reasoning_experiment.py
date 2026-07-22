"""Sealed capture and evaluator-only helpers for the fixed LLM experiment."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .candidate_reasoning_contracts import CandidateEvidence
from .candidate_reasoning_inputs import PublicCompanyReasoningInput
from .candidate_reasoning_service import (
    CandidateReasoningInvocationService,
    candidate_reasoning_input_evidence_digest,
)


EXPERIMENT_SCHEMA_VERSION = "1.0"
EXPECTED_RECORD_COUNT = 18
PUBLIC_COHORT_FIELDS = {
    "record_id",
    "company_name",
    "linkedin_company_url",
    "linkedin_job_url",
    "job_title",
    "job_location",
}


class ExperimentIntegrityError(ValueError):
    """A frozen cohort, sealed artifact, or evaluator input is incompatible."""


class RecordingCandidateReasoningService:
    """Record only public candidate evidence returned by the advisory service."""

    def __init__(self, delegate: CandidateReasoningInvocationService) -> None:
        if not isinstance(delegate, CandidateReasoningInvocationService):
            raise TypeError("delegate must use CandidateReasoningInvocationService")
        self._delegate = delegate
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._delegate.enabled

    def reason(
        self,
        company: PublicCompanyReasoningInput,
        outcome,
        *,
        baseline_candidates: tuple[CandidateEvidence, ...] = (),
    ):
        digest = candidate_reasoning_input_evidence_digest(company)
        result = self._delegate.reason(
            company,
            outcome,
            baseline_candidates=baseline_candidates,
        )
        payload = {
            "input_evidence_digest": digest,
            "eligibility_state": result.eligibility.state,
            "eligibility_reason": result.eligibility.reason_code,
            "used_llm_ranking": result.used_llm_ranking,
            "llm_plan_used": result.llm_plan_used,
            "llm_rank_used": result.llm_rank_used,
            "llm_causal_contribution": "not_evaluated",
            "advisory_failure": (
                {
                    "code": result.advisory_failure.code,
                    "decision_kind": result.advisory_failure.decision_kind,
                }
                if result.advisory_failure is not None
                else None
            ),
            "candidates": [_candidate_payload(item) for item in result.candidates],
        }
        with self._lock:
            prior = self._records.get(digest)
            if prior is not None and prior != payload:
                raise ExperimentIntegrityError(
                    "candidate reasoning invocation changed within one frozen run"
                )
            self._records[digest] = payload
        return result

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))


def load_public_cohort(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Load the answer-free 18-record input and verify its embedded digest."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise ExperimentIntegrityError("public cohort schema is incompatible")
    if payload.get("record_count") != EXPECTED_RECORD_COUNT:
        raise ExperimentIntegrityError("public cohort must contain exactly 18 records")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != EXPECTED_RECORD_COUNT:
        raise ExperimentIntegrityError("public cohort records are incomplete")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != PUBLIC_COHORT_FIELDS:
            raise ExperimentIntegrityError("public cohort record fields are invalid")
        record_id = record.get("record_id")
        company_name = record.get("company_name")
        if (
            not isinstance(record_id, str)
            or len(record_id) != 3
            or not record_id.isdigit()
            or record_id in seen
        ):
            raise ExperimentIntegrityError("public cohort record_id is invalid")
        if not isinstance(company_name, str) or not company_name.strip():
            raise ExperimentIntegrityError("public cohort company_name is invalid")
        for field in PUBLIC_COHORT_FIELDS - {"record_id", "company_name"}:
            if record[field] is not None and not isinstance(record[field], str):
                raise ExperimentIntegrityError(f"public cohort {field} is invalid")
        if any("reference" in key.casefold() for key in record):
            raise ExperimentIntegrityError("evaluator answer leaked into public cohort")
        seen.add(record_id)
        normalized.append(dict(record))
    if payload.get("records_sha256") != json_digest(normalized):
        raise ExperimentIntegrityError("public cohort record digest is incompatible")
    return tuple(normalized)


def load_evaluator_labels(path: str | Path) -> dict[str, str]:
    """Load answer labels only after capture sealing."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise ExperimentIntegrityError("evaluator label schema is incompatible")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != EXPECTED_RECORD_COUNT:
        raise ExperimentIntegrityError("evaluator labels must contain 18 records")
    labels: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "record_id",
            "reference_website_url",
        }:
            raise ExperimentIntegrityError("evaluator label fields are invalid")
        record_id = record["record_id"]
        url = record["reference_website_url"]
        if record_id in labels or not isinstance(url, str) or not url.startswith("https://"):
            raise ExperimentIntegrityError("evaluator label is invalid")
        labels[record_id] = url
    return labels


def reasoning_input_digest(record: Mapping[str, Any]) -> str:
    return candidate_reasoning_input_evidence_digest(
        PublicCompanyReasoningInput(
            company_name=record["company_name"],
            linkedin_company_slug=_linkedin_slug(record.get("linkedin_company_url")),
            job_title=record.get("job_title"),
            job_location=record.get("job_location"),
        )
    )


def extract_deterministic_candidate_urls(trace_record: Mapping[str, Any]) -> tuple[str, ...]:
    trace = trace_record.get("trace")
    stages = trace.get("stages") if isinstance(trace, Mapping) else None
    website = stages.get("website_resolution") if isinstance(stages, Mapping) else None
    candidates = website.get("candidates") if isinstance(website, Mapping) else None
    if not isinstance(candidates, list):
        return ()
    urls: list[str] = []
    for candidate in candidates:
        url = candidate.get("url") if isinstance(candidate, Mapping) else None
        if isinstance(url, str) and url.startswith("https://") and url not in urls:
            urls.append(url)
    return tuple(urls[:3])


def load_ranker_evidence_urls(decisions_path: str | Path) -> dict[str, tuple[str, ...]]:
    """Index pre-model ranker candidates by answer-free invocation digest."""
    indexed: dict[str, tuple[str, ...]] = {}
    path = Path(decisions_path)
    if not path.exists():
        return indexed
    for line in path.read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        record = envelope.get("record") if isinstance(envelope, dict) else None
        if not isinstance(record, dict):
            raise ExperimentIntegrityError("decision artifact envelope is invalid")
        key = record.get("key")
        request = record.get("sanitized_request")
        if not isinstance(key, dict) or key.get("decision_kind") != "candidate_rank":
            continue
        if not isinstance(request, dict):
            raise ExperimentIntegrityError("ranker request artifact is invalid")
        digest = request.get("invocation_input_evidence_digest")
        candidates = request.get("candidates")
        if not isinstance(digest, str) or not isinstance(candidates, list):
            raise ExperimentIntegrityError("ranker invocation linkage is missing")
        urls: list[str] = []
        for candidate in candidates:
            url = candidate.get("url") if isinstance(candidate, dict) else None
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ExperimentIntegrityError("ranker evidence URL is invalid")
            if url not in urls:
                urls.append(url)
        indexed[digest] = tuple(urls)
    return indexed


def verify_sealed_files(root: str | Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ExperimentIntegrityError("capture manifest files are missing")
    base = Path(root)
    for relative_path, expected_digest in files.items():
        path = base / relative_path
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_digest, str)
            or not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != expected_digest
        ):
            raise ExperimentIntegrityError(f"sealed artifact mismatch: {relative_path}")


def write_json_atomic(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_payload(candidate: CandidateEvidence) -> dict[str, Any]:
    return asdict(candidate)


def _linkedin_slug(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    marker = "/company/"
    if marker not in url:
        return None
    slug = url.split(marker, 1)[1].split("/", 1)[0].strip()
    return slug or None
