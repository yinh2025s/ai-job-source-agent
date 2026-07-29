#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


SCHEMA_VERSION = "1.0"
COMPANY_KEYS = frozenset({"company", "company_name", "companyname"})
JOB_URL_KEYS = frozenset({"job_url", "linkedin_job_url", "linkedinjoburl"})
COMPANY_URL_KEYS = frozenset(
    {"company_url", "linkedin_company_url", "linkedincompanyurl"}
)
FORBIDDEN_PREFILLS = frozenset(
    {
        "career_page_url",
        "career_root_url",
        "company_website_url",
        "external_apply_url",
        "job_list_page_url",
        "open_position_url",
    }
)


class DiagnosticCohortError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze a zero-overlap development diagnostic cohort from S1-only "
            "LinkedIn candidate pools."
        )
    )
    parser.add_argument("--candidates", action="append", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--quota", action="append", required=True)
    parser.add_argument("--cohort-name", required=True)
    parser.add_argument("--output-cohort", required=True)
    parser.add_argument("--output-manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        quotas = parse_quotas(args.quota)
        cohort, manifest = prepare_diagnostic_cohort(
            candidate_paths=[Path(path) for path in args.candidates],
            excluded_paths=[Path(path) for path in args.exclude],
            quotas=quotas,
            cohort_name=args.cohort_name,
        )
    except (DiagnosticCohortError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"diagnostic cohort preparation failed: {error}") from error
    _write_json_atomic(Path(args.output_cohort), cohort)
    _write_json_atomic(Path(args.output_manifest), manifest)
    print(
        json.dumps(
            {
                "cohort_sha256": manifest["cohort_sha256"],
                "record_count": manifest["record_count"],
                "rejected_overlap_count": manifest["selection"][
                    "rejected_overlap_count"
                ],
                "role_counts": manifest["role_counts"],
            },
            sort_keys=True,
        )
    )


def parse_quotas(values: list[str]) -> list[tuple[str, int]]:
    quotas: list[tuple[str, int]] = []
    seen: set[str] = set()
    for raw in values:
        keyword, separator, count_text = raw.rpartition("=")
        keyword = " ".join(keyword.split())
        try:
            count = int(count_text)
        except ValueError as error:
            raise DiagnosticCohortError(
                f"invalid quota {raw!r}; expected KEYWORD=COUNT"
            ) from error
        key = keyword.casefold()
        if not separator or not keyword or count <= 0 or key in seen:
            raise DiagnosticCohortError(
                f"invalid quota {raw!r}; quotas must be unique and positive"
            )
        seen.add(key)
        quotas.append((keyword, count))
    if not quotas:
        raise DiagnosticCohortError("at least one quota is required")
    return quotas


def prepare_diagnostic_cohort(
    *,
    candidate_paths: list[Path],
    excluded_paths: list[Path],
    quotas: list[tuple[str, int]],
    cohort_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = " ".join(cohort_name.split())
    if not name:
        raise DiagnosticCohortError("cohort name must not be empty")
    if not candidate_paths:
        raise DiagnosticCohortError("at least one candidate pool is required")
    if not quotas or any(not keyword or count <= 0 for keyword, count in quotas):
        raise DiagnosticCohortError("quotas must be nonempty and positive")
    folded_quotas = [keyword.casefold() for keyword, _count in quotas]
    if len(set(folded_quotas)) != len(folded_quotas):
        raise DiagnosticCohortError("quota keywords must be unique")

    source_digests: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_pool_job_ids: set[str] = set()
    for path in candidate_paths:
        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes)
        if not isinstance(payload, list):
            raise DiagnosticCohortError(f"candidate pool must be an array: {path}")
        source_digests.append(
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "record_count": len(payload),
            }
        )
        for value in payload:
            record = _normalize_candidate(value)
            job_id = _linkedin_job_id(record["linkedin_job_url"])
            if job_id in seen_pool_job_ids:
                continue
            seen_pool_job_ids.add(job_id)
            candidates.append(record)

    excluded_companies: set[str] = set()
    excluded_job_ids: set[str] = set()
    excluded_company_slugs: set[str] = set()
    exclusion_digests: list[dict[str, Any]] = []
    for path in excluded_paths:
        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes)
        _collect_identities(
            payload,
            excluded_companies,
            excluded_job_ids,
            excluded_company_slugs,
        )
        exclusion_digests.append(
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            }
        )

    selected: list[dict[str, Any]] = []
    selected_companies: set[str] = set()
    selected_job_ids: set[str] = set()
    selected_company_slugs: set[str] = set()
    rejection_events: set[tuple[str, str]] = set()
    role_counts: Counter[str] = Counter()
    for keyword, required_count in quotas:
        keyword_key = keyword.casefold()
        for record in candidates:
            if role_counts[keyword] >= required_count:
                break
            company_key = _company_key(record["company_name"])
            job_id = _linkedin_job_id(record["linkedin_job_url"])
            company_slug = _linkedin_company_slug(
                record.get("linkedin_company_url"), required=False
            )
            matched_keywords = {
                value.casefold() for value in _matched_keywords(record)
            }
            if keyword_key not in matched_keywords:
                continue
            if company_key in excluded_companies:
                rejection_events.add(("historical_company", job_id))
                continue
            if job_id in excluded_job_ids:
                rejection_events.add(("historical_linkedin_job", job_id))
                continue
            if company_slug and company_slug in excluded_company_slugs:
                rejection_events.add(("historical_linkedin_company", job_id))
                continue
            if company_key in selected_companies:
                rejection_events.add(("duplicate_selected_company", job_id))
                continue
            if company_slug and company_slug in selected_company_slugs:
                rejection_events.add(
                    ("duplicate_selected_linkedin_company", job_id)
                )
                continue
            if job_id in selected_job_ids:
                rejection_events.add(("duplicate_selected_job", job_id))
                continue
            selected_record = dict(record)
            source_trace = dict(selected_record.get("source_trace") or {})
            source_trace["diagnostic_cohort"] = {
                "cohort_name": name,
                "provenance": "development_diagnostic_nonsealed",
                "role_family": keyword,
                "frozen": True,
            }
            selected_record["source"] = "linkedin_public_jobs_diagnostic"
            selected_record["source_trace"] = source_trace
            selected.append(selected_record)
            selected_companies.add(company_key)
            selected_job_ids.add(job_id)
            if company_slug:
                selected_company_slugs.add(company_slug)
            role_counts[keyword] += 1

    shortfalls = {
        keyword: count - role_counts[keyword]
        for keyword, count in quotas
        if role_counts[keyword] != count
    }
    if shortfalls:
        raise DiagnosticCohortError(f"quota shortfall: {shortfalls}")

    identity_rows = _cohort_identity_rows(selected)
    cohort_bytes = _canonical_json_bytes(selected)
    contract_digests = [
        digest
        for digest in (
            _candidate_query_contract_sha256(record) for record in selected
        )
        if digest is not None
    ]
    unique_contract_digests = sorted(set(contract_digests))
    if len(unique_contract_digests) > 1:
        raise DiagnosticCohortError(
            "selected candidates use different query collection contracts"
        )
    rejection_counts = Counter(reason for reason, _job_id in rejection_events)
    overlap_reasons = {
        "historical_company",
        "historical_linkedin_company",
        "historical_linkedin_job",
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "cohort_name": name,
        "cohort_provenance": "development_diagnostic_nonsealed",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(selected),
        "independent_company_count": len(selected_companies),
        "unique_linkedin_job_id_count": len(selected_job_ids),
        "unique_linkedin_company_slug_count": len(selected_company_slugs),
        "cohort_sha256": hashlib.sha256(cohort_bytes).hexdigest(),
        "cohort_identity_sha256": hashlib.sha256(
            _canonical_json_bytes(identity_rows)
        ).hexdigest(),
        "candidate_sources": source_digests,
        "candidate_collection_contract": {
            "status": (
                "bound"
                if len(contract_digests) == len(selected)
                and len(unique_contract_digests) == 1
                else "unbound"
            ),
            "sha256": (
                unique_contract_digests[0]
                if len(contract_digests) == len(selected)
                and len(unique_contract_digests) == 1
                else None
            ),
            "bound_record_count": len(contract_digests),
        },
        "exclusion_sources": exclusion_digests,
        "historical_identity_counts": {
            "company_count": len(excluded_companies),
            "linkedin_company_slug_count": len(excluded_company_slugs),
            "linkedin_job_id_count": len(excluded_job_ids),
        },
        "quotas": [{"keyword": keyword, "count": count} for keyword, count in quotas],
        "role_counts": dict(role_counts),
        "selection": {
            "policy": "quota order, then frozen candidate source order",
            "company_overlap_policy": "reject explicit structured historical company",
            "linkedin_job_overlap_policy": "reject canonical historical LinkedIn job id",
            "linkedin_company_overlap_policy": (
                "reject normalized historical LinkedIn company slug"
            ),
            "rejected_candidate_count": len(rejection_events),
            "rejected_overlap_count": sum(
                count
                for reason, count in rejection_counts.items()
                if reason in overlap_reasons
            ),
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "post_selection_company_overlap_count": 0,
            "post_selection_linkedin_job_overlap_count": 0,
            "post_selection_linkedin_company_overlap_count": 0,
            "direct_answer_prefills_allowed": False,
        },
        "pipeline_stages_executed": ["linkedin_public_search_collection"],
        "s2_s7_executed_during_selection": False,
        "historical_input_policy": {
            "automatic_history_scan": False,
            "explicit_exclusion_paths_only": True,
            "sealed_holdout_access_claimed": False,
        },
        "records": identity_rows,
    }
    return selected, manifest


def _normalize_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiagnosticCohortError("each candidate must be an object")
    required = ("company_name", "linkedin_job_url", "job_title", "job_location")
    if any(
        not isinstance(value.get(field), str) or not value[field].strip()
        for field in required
    ):
        raise DiagnosticCohortError("candidate is missing a required identity field")
    _linkedin_job_id(value["linkedin_job_url"])
    if any(value.get(field) for field in FORBIDDEN_PREFILLS):
        raise DiagnosticCohortError("diagnostic input cannot contain answer prefills")
    allowed = {
        "company_name",
        "job_location",
        "job_title",
        "linkedin_company_url",
        "linkedin_job_url",
        "source",
        "source_trace",
    }
    return {
        key: value[key]
        for key in allowed
        if key in value and value[key] is not None
    }


def _matched_keywords(record: dict[str, Any]) -> tuple[str, ...]:
    trace = record.get("source_trace")
    if not isinstance(trace, dict):
        return ()
    for key in ("candidate_collection", "blind_candidate_collection"):
        collection = trace.get(key)
        if not isinstance(collection, dict):
            continue
        values = collection.get("matched_keywords")
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            return tuple(" ".join(value.split()) for value in values if value.strip())
        first = collection.get("first_seen_keyword")
        if isinstance(first, str) and first.strip():
            return (" ".join(first.split()),)
    return ()


def _candidate_query_contract_sha256(
    record: dict[str, Any],
) -> str | None:
    trace = record.get("source_trace")
    if not isinstance(trace, dict):
        return None
    for key in ("candidate_collection", "blind_candidate_collection"):
        collection = trace.get(key)
        if not isinstance(collection, dict):
            continue
        value = collection.get("query_contract_sha256")
        if value is None:
            return None
        if (
            isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value)
        ):
            return value
        raise DiagnosticCohortError(
            "candidate query contract digest is invalid"
        )
    return None


def _collect_identities(
    value: Any,
    companies: set[str],
    job_ids: set[str],
    company_slugs: set[str],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).rsplit(".", 1)[-1].casefold()
            if normalized_key in COMPANY_KEYS and isinstance(item, str) and item.strip():
                companies.add(_company_key(item))
            if normalized_key in JOB_URL_KEYS and isinstance(item, str):
                job_id = _linkedin_job_id(item, required=False)
                if job_id:
                    job_ids.add(job_id)
            if normalized_key in COMPANY_URL_KEYS and isinstance(item, str):
                slug = _linkedin_company_slug(item, required=False)
                if slug:
                    company_slugs.add(slug)
            _collect_identities(item, companies, job_ids, company_slugs)
    elif isinstance(value, list):
        for item in value:
            _collect_identities(item, companies, job_ids, company_slugs)


def _record_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "company_name": record["company_name"],
        "linkedin_job_id": _linkedin_job_id(record["linkedin_job_url"]),
        "linkedin_job_url": _canonical_linkedin_url(record["linkedin_job_url"]),
        "job_title": record["job_title"],
        "job_location": record["job_location"],
    }


def _cohort_identity_rows(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records):
        row: dict[str, Any] = _record_identity(record)
        row["ordinal"] = ordinal
        trace = record.get("source_trace")
        diagnostic = (
            trace.get("diagnostic_cohort")
            if isinstance(trace, dict)
            else None
        )
        row["role_family"] = (
            diagnostic.get("role_family")
            if isinstance(diagnostic, dict)
            else None
        )
        rows.append(row)
    return rows


def _company_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _linkedin_job_id(value: str, *, required: bool = True) -> str:
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        parsed = None
    host = (parsed.hostname or "").casefold() if parsed else ""
    match = (
        re.match(r"^/jobs/view/(?:.*-)?([0-9]{6,})/?$", parsed.path)
        if parsed
        else None
    )
    if host in {"linkedin.com", "www.linkedin.com"} and match:
        return match.group(1)
    if required:
        raise DiagnosticCohortError(f"invalid LinkedIn job URL: {value!r}")
    return ""


def _linkedin_company_slug(value: Any, *, required: bool = True) -> str:
    try:
        parsed = urlparse(value if isinstance(value, str) else "")
    except (TypeError, ValueError):
        parsed = None
    host = (parsed.hostname or "").casefold() if parsed else ""
    match = re.fullmatch(r"/company/([^/?#]+)/?", parsed.path) if parsed else None
    if host in {"linkedin.com", "www.linkedin.com"} and match:
        return match.group(1).casefold()
    if required:
        raise DiagnosticCohortError(f"invalid LinkedIn company URL: {value!r}")
    return ""


def _canonical_linkedin_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse(("https", "www.linkedin.com", parsed.path.rstrip("/"), "", "", ""))


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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
