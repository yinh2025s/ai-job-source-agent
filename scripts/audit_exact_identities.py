#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
    validate_opening_identity_chain,
)
from job_source_agent.opening_selection_validation import validate_opening_selection


class ExactIdentityAuditError(ValueError):
    pass


_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit every published Exact opening through its serialized S7 chain."
    )
    parser.add_argument("--trace", required=True)
    parser.add_argument("--cohort")
    parser.add_argument("--results")
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-exact-count", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        cohort = (
            json.loads(Path(args.cohort).read_text(encoding="utf-8"))
            if args.cohort
            else None
        )
        results = (
            json.loads(Path(args.results).read_text(encoding="utf-8"))
            if args.results
            else None
        )
        report = audit_exact_identities(
            payload,
            require_exact_count=args.require_exact_count,
            cohort_records=cohort,
            result_records=results,
        )
    except (ExactIdentityAuditError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Exact identity audit failed: {error}") from error
    _write_json_atomic(Path(args.output), report)
    print(
        json.dumps(
            {
                "exact_count": report["exact_count"],
                "failed_count": report["failed_count"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    if report["status"] != "passed":
        raise SystemExit(1)


def audit_exact_identities(
    records: Any,
    *,
    require_exact_count: int | None = None,
    cohort_records: Any = None,
    result_records: Any = None,
) -> dict[str, Any]:
    if not isinstance(records, list):
        raise ExactIdentityAuditError("trace must be a JSON array")
    measurement_bound = cohort_records is not None or result_records is not None
    source_by_job_id: dict[str, dict[str, Any]] = {}
    if measurement_bound:
        if cohort_records is None or result_records is None:
            raise ExactIdentityAuditError(
                "measurement audit requires both cohort and results"
            )
        source_by_job_id = _validate_measurement_binding(
            cohort_records=cohort_records,
            result_records=result_records,
            trace_records=records,
        )

    exact_records = [
        (index, record)
        for index, record in enumerate(records)
        if isinstance(record, dict) and record.get("open_position_url")
    ]
    if require_exact_count is not None and len(exact_records) != require_exact_count:
        raise ExactIdentityAuditError(
            f"published Exact count is {len(exact_records)}, expected {require_exact_count}"
        )

    audited: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    passed_count = 0
    for index, record in exact_records:
        source_record = (
            source_by_job_id[_linkedin_job_id(record.get("linkedin_job_url"))]
            if measurement_bound
            else None
        )
        issues = _record_issues(record, source_record=source_record)
        issue_counts.update(issues)
        if not issues:
            passed_count += 1
        audited.append(
            {
                "ordinal": index,
                "company_name": record.get("company_name"),
                "linkedin_job_title": record.get("linkedin_job_title"),
                "linkedin_job_location": record.get("linkedin_job_location"),
                "opening_url": record.get("open_position_url"),
                "provider": _nested(
                    record, "identity_assertion", "provider", "provider"
                ),
                "tenant": _nested(
                    record, "identity_assertion", "provider", "tenant"
                ),
                "status": "passed" if not issues else "failed",
                "issues": issues,
            }
        )
    failed_count = len(exact_records) - passed_count
    report = {
        "schema_version": "1.0",
        "status": "passed" if failed_count == 0 else "failed",
        "audit_mode": (
            "measurement_bound" if measurement_bound else "legacy_trace_only"
        ),
        "measurement_bound": measurement_bound,
        "trace_record_count": len(records),
        "exact_count": len(exact_records),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "issue_counts": dict(sorted(issue_counts.items())),
        "records": audited,
    }
    if measurement_bound:
        report["measurement_binding"] = {
            "cohort_record_count": len(cohort_records),
            "result_record_count": len(result_records),
            "trace_record_count": len(records),
            "cohort_sha256": _payload_sha256(cohort_records),
            "results_sha256": _payload_sha256(result_records),
            "trace_sha256": _payload_sha256(records),
        }
    return report


def _record_issues(
    record: dict[str, Any],
    *,
    source_record: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    opening_url = record.get("open_position_url")
    assertion = record.get("identity_assertion")
    if record.get("pipeline_status") != "success":
        issues.append("pipeline_not_success")
    if record.get("output_validation_status") != "success":
        issues.append("output_validation_not_success")
    if not isinstance(assertion, dict):
        return sorted(set((*issues, "identity_assertion_missing")))
    if assertion.get("verdict") != "verified":
        issues.append("identity_not_verified")
    if assertion.get("failure_codes") not in ([], ()):
        issues.append("identity_failure_codes_present")

    hiring = assertion.get("hiring")
    provider = assertion.get("provider")
    opening = assertion.get("opening")
    selection = assertion.get("selection")
    if not isinstance(hiring, dict) or hiring.get("verified") is not True:
        issues.append("hiring_relationship_unverified")
    if (
        not isinstance(provider, dict)
        or provider.get("relationship_verified") is not True
    ):
        issues.append("provider_relationship_unverified")
    if not isinstance(opening, dict):
        issues.append("opening_identity_missing")
    if not isinstance(selection, dict):
        issues.append("selection_identity_missing")
    if issues and (
        not isinstance(hiring, dict)
        or not isinstance(provider, dict)
        or not isinstance(opening, dict)
        or not isinstance(selection, dict)
    ):
        return sorted(set(issues))

    assert isinstance(hiring, dict)
    assert isinstance(provider, dict)
    assert isinstance(opening, dict)
    assert isinstance(selection, dict)
    provider_name = provider.get("provider")
    if provider_name in {None, "", "unknown"}:
        issues.append("provider_not_typed")
    for field in ("provider", "tenant", "canonical_board_url"):
        values = [provider.get(field), opening.get(field), selection.get(field)]
        if not all(isinstance(value, str) and value for value in values):
            issues.append(f"{field}_missing")
        elif len({_normalized_identity_value(value) for value in values}) != 1:
            issues.append(f"{field}_continuity_mismatch")

    hiring_entities = [
        hiring.get("hiring_entity_name"),
        provider.get("hiring_entity_name"),
        opening.get("hiring_entity_name"),
    ]
    if not all(isinstance(value, str) and value for value in hiring_entities):
        issues.append("hiring_entity_missing")
    elif len({_normalized_name(value) for value in hiring_entities}) != 1:
        issues.append("hiring_entity_continuity_mismatch")
    source_company = hiring.get("source_company_name")
    company_name = record.get("company_name")
    if (
        not isinstance(source_company, str)
        or not isinstance(company_name, str)
        or _normalized_name(source_company) != _normalized_name(company_name)
    ):
        issues.append("source_company_continuity_mismatch")

    identity_urls = [
        opening_url,
        assertion.get("candidate_opening_url"),
        opening.get("canonical_opening_url"),
        selection.get("canonical_opening_url"),
    ]
    if not all(isinstance(value, str) and value for value in identity_urls):
        issues.append("opening_url_missing")
    elif len({_canonical_url(value) for value in identity_urls}) != 1:
        issues.append("opening_url_continuity_mismatch")
    if not isinstance(opening_url, str) or not _safe_public_https_url(opening_url):
        issues.append("opening_url_not_safe_public_https")

    if not isinstance(selection.get("title"), str) or not selection["title"].strip():
        issues.append("selection_title_missing")
    location_classification = assertion.get("location_classification")
    location_may_be_qualified = location_classification in {
        "title_qualifier",
        "url_qualifier",
        "title_location_independent",
        "url_location_independent",
    }
    if (
        not location_may_be_qualified
        and (
            not isinstance(selection.get("location"), str)
            or not selection["location"].strip()
        )
    ):
        issues.append("selection_location_missing")
    if location_classification in {
        None,
        "",
        "mismatch",
        "missing",
        "not_applicable",
    }:
        issues.append("location_not_verified")
    if source_record is not None:
        issues.extend(_production_contract_issues(record, source_record))
    return sorted(set(issues))


def _production_contract_issues(
    record: dict[str, Any],
    source_record: dict[str, Any],
) -> list[str]:
    assertion = record.get("identity_assertion")
    if not isinstance(assertion, dict):
        return ["production_identity_contract_invalid"]
    try:
        hiring = HiringIdentityEvidence.from_checkpoint_payload(assertion.get("hiring"))
        provider = ProviderIdentity.from_checkpoint_payload(assertion.get("provider"))
        opening = OpeningIdentity.from_checkpoint_payload(assertion.get("opening"))
        selection = OpeningSelectionEvidence.from_checkpoint_payload(
            assertion.get("selection")
        )
    except (TypeError, ValueError):
        return ["production_identity_contract_invalid"]

    opening_url = record.get("open_position_url")
    identity_failures = validate_opening_identity_chain(
        hiring=hiring,
        provider=provider,
        opening=opening,
        open_position_url=opening_url,
    )
    selection_failures, location_classification = validate_opening_selection(
        selection=selection,
        provider=provider,
        opening=opening,
        open_position_url=opening_url,
        target_title=source_record["job_title"],
        target_location=source_record["job_location"],
    )
    issues = [
        failure.casefold()
        for failure in (*identity_failures, *selection_failures)
    ]
    if assertion.get("location_classification") != location_classification:
        issues.append("location_classification_mismatch")
    return issues


def _validate_measurement_binding(
    *,
    cohort_records: Any,
    result_records: Any,
    trace_records: list[Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(cohort_records, list) or not cohort_records:
        raise ExactIdentityAuditError(
            "measurement cohort must be a nonempty JSON array"
        )
    if not isinstance(result_records, list):
        raise ExactIdentityAuditError("measurement results must be a JSON array")
    if not (
        len(cohort_records) == len(result_records) == len(trace_records)
    ):
        raise ExactIdentityAuditError(
            "measurement cohort, results and trace counts do not match"
        )

    source_by_job_id: dict[str, dict[str, Any]] = {}
    expected_job_ids: list[str] = []
    for index, source in enumerate(cohort_records):
        if not isinstance(source, dict):
            raise ExactIdentityAuditError(
                f"measurement cohort record {index} is not an object"
            )
        for field in (
            "company_name",
            "job_title",
            "job_location",
            "linkedin_job_url",
        ):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ExactIdentityAuditError(
                    f"measurement cohort record {index} has invalid {field}"
                )
        job_id = _linkedin_job_id(source["linkedin_job_url"])
        if job_id in source_by_job_id:
            raise ExactIdentityAuditError(
                f"measurement cohort contains duplicate LinkedIn job ID {job_id}"
            )
        source_by_job_id[job_id] = source
        expected_job_ids.append(job_id)

    for label, values in (
        ("results", result_records),
        ("trace", trace_records),
    ):
        observed_job_ids: list[str] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise ExactIdentityAuditError(
                    f"measurement {label} record {index} is not an object"
                )
            job_id = _linkedin_job_id(value.get("linkedin_job_url"))
            observed_job_ids.append(job_id)
            source = source_by_job_id.get(job_id)
            if source is None:
                raise ExactIdentityAuditError(
                    f"measurement {label} record {index} is not in frozen cohort"
                )
            _validate_source_projection(
                source=source,
                live_record=value,
                label=label,
                index=index,
            )
        if observed_job_ids != expected_job_ids:
            raise ExactIdentityAuditError(
                f"measurement {label} order or LinkedIn job IDs do not match cohort"
            )

    for index, (result, trace) in enumerate(zip(result_records, trace_records)):
        assert isinstance(result, dict)
        assert isinstance(trace, dict)
        trace_projection = {key: value for key, value in trace.items() if key != "trace"}
        if trace_projection != result:
            raise ExactIdentityAuditError(
                f"measurement result and trace record {index} are not identical"
            )
        if not isinstance(trace.get("trace"), dict):
            raise ExactIdentityAuditError(
                f"measurement trace record {index} lacks full trace evidence"
            )
    return source_by_job_id


def _validate_source_projection(
    *,
    source: dict[str, Any],
    live_record: dict[str, Any],
    label: str,
    index: int,
) -> None:
    projections = (
        ("company_name", "company_name"),
        ("job_title", "linkedin_job_title"),
        ("job_location", "linkedin_job_location"),
    )
    for source_key, live_key in projections:
        source_value = _normalized_source_value(source[source_key])
        live_value = live_record.get(live_key)
        if (
            not isinstance(live_value, str)
            or _normalized_source_value(live_value) != source_value
        ):
            raise ExactIdentityAuditError(
                f"measurement {label} record {index} does not preserve {source_key}"
            )


def _linkedin_job_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ExactIdentityAuditError("LinkedIn job URL is missing")
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise ExactIdentityAuditError("LinkedIn job URL is invalid") from error
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ):
        raise ExactIdentityAuditError("LinkedIn job URL is invalid")
    match = re.search(r"(?:^|[-/])(\d{6,})(?:/)?$", parsed.path)
    if match is None:
        raise ExactIdentityAuditError("LinkedIn job URL lacks a canonical job ID")
    return match.group(1)


def _normalized_source_value(value: str) -> str:
    return " ".join(value.casefold().split())


def _payload_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_public_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or "." not in host
        or not host.isascii()
        or not _HOSTNAME.fullmatch(host)
        or any(not label or len(label) > 63 for label in host.split("."))
        or host in {"localhost"}
        or host.endswith((".localhost", ".local"))
        or parsed.fragment
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def _canonical_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    port = f":{parsed.port}" if parsed.port not in {None, 443} else ""
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.casefold()}://{host}{port}{path}?{parsed.query}".rstrip("?")


def _normalized_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalized_identity_value(value: str) -> str:
    return value.strip().casefold()


def _nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


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
