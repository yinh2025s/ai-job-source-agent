from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_discovery_evidence import (
    VerifiedCareerEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.result_identity import tenant_locator
from job_source_agent.web import domain_of, safe_normalize_url


MANIFEST_SCHEMA_VERSION = 1
_FIRST_PARTY_PROVIDER_METHODS = {
    "first_party_handoff",
    "identity_career_root",
    "verified_declared_inventory",
    "verified_first_party_action",
    "verified_first_party_handoff",
    "verified_first_party_provider_page",
    "verified_provider_career_page",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed ADR-0028 discovery candidates from previously verified live "
            "batch results and trace records."
        )
    )
    parser.add_argument("--results", required=True, help="Historical live batch results JSON.")
    parser.add_argument("--trace", required=True, help="Matching historical live batch trace JSON.")
    parser.add_argument("--store", required=True, help="Destination evidence store JSON.")
    parser.add_argument("--manifest", required=True, help="Destination audit manifest JSON.")
    parser.add_argument(
        "--source-run",
        required=True,
        help="Stable, human-readable identifier for the historical source run.",
    )
    parser.add_argument(
        "--observed-at",
        type=float,
        help=(
            "Unix timestamp for the historical observation. Defaults to the older "
            "mtime of the results and trace files."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = seed_company_discovery_evidence(
        results_path=Path(args.results),
        trace_path=Path(args.trace),
        store_path=Path(args.store),
        manifest_path=Path(args.manifest),
        source_run=args.source_run,
        observed_at=args.observed_at,
    )
    print(json.dumps(manifest["summary"], sort_keys=True))


def seed_company_discovery_evidence(
    *,
    results_path: Path,
    trace_path: Path,
    store_path: Path,
    manifest_path: Path,
    source_run: str,
    observed_at: float | None = None,
    clock=time.time,
) -> dict[str, Any]:
    results = _load_record_array(results_path, "results")
    traces = _load_record_array(trace_path, "trace")
    source_run = source_run.strip()
    if not source_run:
        raise ValueError("source_run must not be empty")
    if observed_at is None:
        observed_at = min(results_path.stat().st_mtime, trace_path.stat().st_mtime)
    observed_at = float(observed_at)
    generated_at = float(clock())
    if (
        not math.isfinite(observed_at)
        or not math.isfinite(generated_at)
        or observed_at < 0
        or observed_at > generated_at
    ):
        raise ValueError("observed_at must be finite, non-negative, and not in the future")

    trace_buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace_record in traces:
        trace_buckets[_record_key(trace_record)].append(trace_record)

    store = FilesystemCompanyDiscoveryEvidenceStore(store_path, clock=clock)
    rejection_counts: Counter[str] = Counter()
    rejections: list[dict[str, Any]] = []
    seeded: dict[tuple[str, str], dict[str, Any]] = {}

    def reject(index: int, record: dict[str, Any], layer: str, reason: str) -> None:
        rejection_counts[reason] += 1
        item = {
            "record_index": index,
            "company_name": _text(record.get("company_name")),
            "identity_sha256": _identity_audit_hash(record),
            "layer": layer,
            "reason": reason,
        }
        rejections.append(item)

    for index, result in enumerate(results):
        bucket = trace_buckets.get(_record_key(result), [])
        trace_record = bucket.pop(0) if bucket else None
        if trace_record is None:
            reject(index, result, "record", "matching_trace_missing")
            continue

        company_name = _text(result.get("company_name"))
        linkedin_url = _text(result.get("linkedin_company_url"))
        if not company_name or not linkedin_url:
            reject(index, result, "record", "mandatory_identity_missing")
            continue

        identity_key = (company_name.casefold(), linkedin_url)
        audit = seeded.setdefault(
            identity_key,
            {
                "company_name": company_name,
                "linkedin_company_url": linkedin_url,
                "source_record_indices": [],
                "layers": set(),
            },
        )
        audit["source_record_indices"].append(index)

        website_url = _verified_stage_url(
            result, "website_resolution", "company_website_url"
        )
        if website_url is None:
            reject(index, result, "website", "website_not_verified")
            continue
        try:
            store.save(
                company_name,
                linkedin_url,
                website=VerifiedWebsiteEvidence(
                    url=website_url,
                    source=_website_source(trace_record),
                    evidence_url=website_url,
                    observed_at=observed_at,
                ),
            )
        except (OSError, TypeError, ValueError):
            reject(index, result, "website", "website_url_rejected")
            continue
        audit["layers"].add("website")

        career_url = _verified_stage_url(result, "career_discovery", "career_page_url")
        if career_url is None:
            if result.get("career_page_url"):
                reject(index, result, "career", "career_not_verified")
            continue
        try:
            store.save(
                company_name,
                linkedin_url,
                career=VerifiedCareerEvidence(
                    url=career_url,
                    website_url=website_url,
                    source=_career_source(trace_record, website_url, career_url),
                    evidence_url=website_url,
                    observed_at=observed_at,
                ),
            )
        except (OSError, TypeError, ValueError):
            reject(index, result, "career", "career_url_rejected")
            continue
        audit["layers"].add("career")

        board_url = _verified_stage_url(
            result, "job_board_discovery", "job_list_page_url"
        )
        if board_url is None:
            if result.get("job_list_page_url"):
                reject(index, result, "provider_board", "provider_board_not_verified")
            continue
        provider_evidence, reason = _provider_evidence(
            result, trace_record, board_url, observed_at
        )
        if provider_evidence is None:
            reject(index, result, "provider_board", reason)
            continue
        try:
            store.save(
                company_name,
                linkedin_url,
                provider_board=provider_evidence,
            )
        except (OSError, TypeError, ValueError):
            reject(index, result, "provider_board", "provider_board_url_rejected")
            continue
        audit["layers"].add("provider_board")

    entries = []
    for item in seeded.values():
        layers = sorted(item.pop("layers"))
        if not layers:
            continue
        entries.append({**item, "layers": layers})
    entries.sort(key=lambda item: (item["company_name"].casefold(), item["linkedin_company_url"]))
    layer_counts = Counter(layer for item in entries for layer in item["layers"])
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "source_run": source_run,
        "authority": "discovery_candidates_requiring_current_revalidation",
        "observed_at": observed_at,
        "generated_at": generated_at,
        "inputs": {
            "results": _file_descriptor(results_path),
            "trace": _file_descriptor(trace_path),
        },
        "destination_store": str(store_path),
        "summary": {
            "result_records_read": len(results),
            "trace_records_read": len(traces),
            "seeded_identity_count": len(entries),
            "seeded_layer_counts": dict(sorted(layer_counts.items())),
            "rejection_count": len(rejections),
            "rejection_counts": dict(sorted(rejection_counts.items())),
        },
        "seeded": entries,
        "rejected": rejections,
        "excluded_data_classes": [
            "cookies_and_tokens",
            "html_and_response_bodies",
            "inventory",
            "exact_opening",
        ],
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def _provider_evidence(
    result: dict[str, Any],
    trace_record: dict[str, Any],
    board_url: str,
    observed_at: float,
) -> tuple[VerifiedProviderBoardEvidence | None, str]:
    stage = _stage(result, "job_board_discovery")
    provider = _text(stage.get("provider") if stage else None)
    trace = _stage_trace(trace_record, "job_board_discovery")
    relationship = trace.get("relationship_evidence")
    if isinstance(relationship, dict):
        if relationship.get("verified") is not True:
            return None, "provider_relationship_unverified"
        return _provider_evidence_from_claim(
            provider=provider,
            claim=relationship,
            method_key="evidence_type",
            require_canonical_board_url=False,
            board_url=board_url,
            observed_at=observed_at,
        )

    assertion = result.get("identity_assertion")
    identity_provider = assertion.get("provider") if isinstance(assertion, dict) else None
    if (
        not isinstance(identity_provider, dict)
        or not _verified_result_provider_relationship(assertion, identity_provider)
    ):
        return None, "provider_relationship_unverified"

    return _provider_evidence_from_claim(
        provider=provider,
        claim=identity_provider,
        method_key="verification_method",
        require_canonical_board_url=True,
        board_url=board_url,
        observed_at=observed_at,
    )


def _verified_result_provider_relationship(
    assertion: dict[str, Any], provider: dict[str, Any]
) -> bool:
    if assertion.get("verdict") not in {"verified", "not_applicable"}:
        return False
    hiring = assertion.get("hiring")
    if not isinstance(hiring, dict) or hiring.get("verified") is not True:
        return False
    failure_codes = assertion.get("failure_codes")
    return failure_codes in (None, []) and provider.get("relationship_verified") is True


def _provider_evidence_from_claim(
    *,
    provider: str,
    claim: dict[str, Any],
    method_key: str,
    require_canonical_board_url: bool,
    board_url: str,
    observed_at: float,
) -> tuple[VerifiedProviderBoardEvidence | None, str]:
    evidence_provider = _text(claim.get("provider"))
    tenant = _text(claim.get("tenant"))
    canonical_board_url = _text(claim.get("canonical_board_url"))
    method = _text(claim.get(method_key))
    evidence_url = _text(claim.get("evidence_url"))
    source = _provider_source(method)
    if (
        not provider
        or provider != evidence_provider
        or not tenant
        or (require_canonical_board_url and not canonical_board_url)
        or not evidence_url
        or not source
    ):
        return None, "provider_identity_incomplete_or_unsupported"
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_for(board_url)
    board = adapter.identify_board(board_url) if adapter is not None else None
    if adapter is None or board is None:
        return None, "provider_board_unrecognized"
    canonicalize = getattr(adapter, "canonicalize_board", None)
    if callable(canonicalize):
        board = canonicalize(board)
    canonical_tenant = board.identifier or tenant_locator(board.url)
    if (
        board.provider != provider
        or canonical_tenant.casefold() != tenant.casefold()
        or (canonical_board_url and not _same_url(canonical_board_url, board.url))
    ):
        return None, "provider_identity_mismatch"
    try:
        return (
            VerifiedProviderBoardEvidence(
                provider=provider,
                tenant=tenant,
                canonical_board_url=board.url,
                relationship_evidence_url=evidence_url,
                verification_method=method,
                source=source,
                observed_at=observed_at,
            ),
            "",
        )
    except (TypeError, ValueError):
        return None, "provider_identity_incomplete_or_unsupported"


def _provider_source(method: str) -> str | None:
    if method in _FIRST_PARTY_PROVIDER_METHODS:
        return "first_party_handoff"
    if method == "provider_inventory":
        return "provider_page_identity"
    return None


def _website_source(trace_record: dict[str, Any]) -> str:
    reasons = _stage_trace(trace_record, "website_resolution").get("selected", {}).get(
        "reasons", []
    )
    if isinstance(reasons, list) and any(
        "LinkedIn company page identifies official website" in str(reason)
        for reason in reasons
    ):
        return "linkedin_official_website"
    return "verified_resolver"


def _career_source(
    trace_record: dict[str, Any], website_url: str, career_url: str
) -> str:
    selected = _stage_trace(trace_record, "career_discovery").get("selected", {})
    marker = " ".join(
        str(selected.get(key) or "") for key in ("reason", "origin", "selected_from")
    ).casefold() if isinstance(selected, dict) else ""
    if "provider" in marker or "ats" in marker:
        return "provider_handoff"
    if domain_of(website_url) == domain_of(career_url):
        return "first_party_navigation"
    return "verified_career_search"


def _verified_stage_url(
    record: dict[str, Any], stage_name: str, field: str
) -> str | None:
    value = _text(record.get(field))
    stage = _stage(record, stage_name)
    if not value or stage is None or stage.get("status") != "success":
        return None
    evidence = stage.get("evidence")
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        if (
            isinstance(item, dict)
            and item.get("field") == field
            and _same_url(item.get("url"), value)
        ):
            return value
    return None


def _stage(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    return next(
        (item for item in stages if isinstance(item, dict) and item.get("stage") == name),
        None,
    )


def _stage_trace(record: dict[str, Any], name: str) -> dict[str, Any]:
    trace = record.get("trace")
    stages = trace.get("stages") if isinstance(trace, dict) else None
    value = stages.get(name) if isinstance(stages, dict) else None
    return value if isinstance(value, dict) else {}


def _same_url(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if left.strip() == right.strip():
        return True
    normalized_left = safe_normalize_url(left)
    normalized_right = safe_normalize_url(right)
    return bool(normalized_left and normalized_left == normalized_right)


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(record.get("company_name")).casefold(),
        _text(record.get("linkedin_company_url")),
        _text(record.get("linkedin_job_url")),
    )


def _identity_audit_hash(record: dict[str, Any]) -> str:
    identity = json.dumps(
        [
            _text(record.get("company_name")).casefold(),
            _text(record.get("linkedin_company_url")),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _load_record_array(path: Path, label: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{label} must be a JSON array of objects")
    return payload


def _file_descriptor(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
