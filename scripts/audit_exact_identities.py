#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-exact-count", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        report = audit_exact_identities(
            payload,
            require_exact_count=args.require_exact_count,
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
) -> dict[str, Any]:
    if not isinstance(records, list):
        raise ExactIdentityAuditError("trace must be a JSON array")
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
        issues = _record_issues(record)
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
    return {
        "schema_version": "1.0",
        "status": "passed" if failed_count == 0 else "failed",
        "trace_record_count": len(records),
        "exact_count": len(exact_records),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "issue_counts": dict(sorted(issue_counts.items())),
        "records": audited,
    }


def _record_issues(record: dict[str, Any]) -> list[str]:
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
    return sorted(set(issues))


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
