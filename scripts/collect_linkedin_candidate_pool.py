#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.linkedin_discovery import LinkedInJobsDiscoverer, linkedin_postings_to_company_inputs
from job_source_agent.models import dataclass_to_dict
from job_source_agent.web import Fetcher


COLLECTION_CONTRACT_SCHEMA = "1.0"
COLLECTION_CONTRACT_KEYS = frozenset(
    {
        "collection_kind",
        "fetch_timeout_seconds",
        "location",
        "minimum_job_count",
        "pages",
        "per_keyword_limit",
        "queries",
        "schema_version",
        "target_job_count",
        "target_required",
    }
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect LinkedIn public job cards without executing S2-S7.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--keyword", action="append")
    source.add_argument("--query-contract")
    parser.add_argument("--location")
    parser.add_argument("--per-keyword-limit", type=int)
    parser.add_argument("--pages", type=int)
    parser.add_argument("--target", type=int)
    parser.add_argument("--minimum-records", type=int)
    parser.add_argument(
        "--require-target",
        action="store_true",
        default=None,
        help="Fail unless the collector freezes exactly --target unique job cards.",
    )
    parser.add_argument("--fetch-timeout", type=float)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    contract_digest = None
    if args.query_contract:
        overridden = [
            name
            for name in (
                "location",
                "per_keyword_limit",
                "pages",
                "target",
                "minimum_records",
                "require_target",
                "fetch_timeout",
            )
            if getattr(args, name) is not None
        ]
        if overridden:
            parser.error(
                "--query-contract cannot be combined with collection overrides: "
                + ", ".join(overridden)
            )
        contract_path = Path(args.query_contract)
        try:
            contract, contract_digest = load_collection_contract(contract_path)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            parser.error(f"invalid query contract: {error}")
        query_specs = contract["queries"]
        location = contract["location"]
        per_keyword_limit = contract["per_keyword_limit"]
        pages = contract["pages"]
        target = contract["target_job_count"]
        minimum_records = contract["minimum_job_count"]
        require_target = contract["target_required"]
        fetch_timeout = contract["fetch_timeout_seconds"]
    else:
        query_specs = [
            {"keyword": keyword, "lane": "legacy_cli"}
            for keyword in args.keyword
        ]
        location = _default(args.location, "United States")
        per_keyword_limit = _default(args.per_keyword_limit, 30)
        pages = _default(args.pages, 2)
        target = _default(args.target, 120)
        minimum_records = _default(args.minimum_records, 50)
        require_target = bool(args.require_target)
        fetch_timeout = _default(args.fetch_timeout, 8)

    discoverer = LinkedInJobsDiscoverer(Fetcher(timeout=fetch_timeout))
    records_by_id = {}
    query_counts = []
    for query in query_specs:
        keyword = query["keyword"]
        lane = query["lane"]
        postings = discoverer.search(
            keywords=keyword,
            location=location,
            limit=per_keyword_limit,
            pages=pages,
        )
        query_counts.append(
            {"keyword": keyword, "lane": lane, "returned": len(postings)}
        )
        for posting in postings:
            record = dataclass_to_dict(linkedin_postings_to_company_inputs([posting])[0])
            existing = records_by_id.get(posting.job_id)
            if existing is None:
                record["source_trace"]["candidate_collection"] = {
                    "provenance": "development_candidate_pool",
                    "first_seen_keyword": keyword,
                    "first_seen_lane": lane,
                    "matched_keywords": [keyword],
                    "matched_lanes": [lane],
                    "query_contract_sha256": contract_digest,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_source": "public_search_card",
                }
                records_by_id[posting.job_id] = record
            else:
                collection = existing["source_trace"]["candidate_collection"]
                if keyword not in collection["matched_keywords"]:
                    collection["matched_keywords"].append(keyword)
                if lane not in collection["matched_lanes"]:
                    collection["matched_lanes"].append(lane)
                if (
                    existing["company_name"] != record["company_name"]
                    or existing["job_title"] != record["job_title"]
                ):
                    collection["identity_conflict"] = True
            if len(records_by_id) >= target:
                break
        if len(records_by_id) >= target:
            break
    records = list(records_by_id.values())
    validate_collection_count(
        len(records),
        target=target,
        minimum=minimum_records,
        require_target=require_target,
    )
    manifest = {
        "schema_version": "1.0",
        "collection_kind": "linkedin_public_search_cards_s1_only",
        "cohort_provenance": "development_candidate_pool",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "location": location,
        "queries": query_counts,
        "unique_job_count": len(records),
        "target_job_count": target,
        "minimum_job_count": minimum_records,
        "target_required": require_target,
        "query_contract_path": (
            str(Path(args.query_contract).resolve()) if args.query_contract else None
        ),
        "query_contract_sha256": contract_digest,
        "pipeline_stages_executed": ["linkedin_public_search_collection"],
        "s2_s7_executed": False,
    }
    _write_json_atomic(Path(args.output), records)
    _write_json_atomic(Path(args.manifest), manifest)
    print(json.dumps({"unique_job_count": len(records), "queries": query_counts}))


def load_collection_contract(path: Path) -> tuple[dict, str]:
    payload_bytes = path.read_bytes()
    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict):
        raise ValueError("contract must be an object")
    if set(payload) != COLLECTION_CONTRACT_KEYS:
        missing = sorted(COLLECTION_CONTRACT_KEYS - set(payload))
        extra = sorted(set(payload) - COLLECTION_CONTRACT_KEYS)
        raise ValueError(f"contract keys differ: missing={missing}, extra={extra}")
    if payload["schema_version"] != COLLECTION_CONTRACT_SCHEMA:
        raise ValueError("unsupported contract schema")
    if payload["collection_kind"] != "linkedin_public_search_cards_s1_only":
        raise ValueError("unsupported collection kind")
    if not isinstance(payload["location"], str) or not payload["location"].strip():
        raise ValueError("location must be nonempty")
    integer_bounds = (
        "per_keyword_limit",
        "pages",
        "target_job_count",
        "minimum_job_count",
    )
    if any(
        isinstance(payload[key], bool)
        or not isinstance(payload[key], int)
        or payload[key] <= 0
        for key in integer_bounds
    ):
        raise ValueError("collection bounds must be positive integers")
    timeout = payload["fetch_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("fetch timeout must be positive")
    if not isinstance(payload["target_required"], bool):
        raise ValueError("target_required must be boolean")
    validate_collection_count(
        payload["minimum_job_count"],
        target=payload["target_job_count"],
        minimum=payload["minimum_job_count"],
        require_target=False,
    )
    queries = payload["queries"]
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries must be a nonempty array")
    seen_keywords = set()
    normalized_queries = []
    for value in queries:
        if not isinstance(value, dict) or set(value) != {"keyword", "lane"}:
            raise ValueError("each query must contain exactly keyword and lane")
        if not isinstance(value["keyword"], str) or not isinstance(
            value["lane"], str
        ):
            raise ValueError("query keyword and lane must be strings")
        keyword = " ".join(value["keyword"].split())
        lane = value["lane"].strip()
        keyword_key = keyword.casefold()
        if not keyword or not lane or keyword_key in seen_keywords:
            raise ValueError("query keywords and lanes must be nonempty and unique")
        seen_keywords.add(keyword_key)
        normalized_queries.append({"keyword": keyword, "lane": lane})
    if payload["target_job_count"] > (
        payload["per_keyword_limit"] * len(normalized_queries)
    ):
        raise ValueError("target exceeds the per-query collection ceiling")
    normalized = dict(payload)
    normalized["location"] = payload["location"].strip()
    normalized["queries"] = normalized_queries
    return normalized, hashlib.sha256(payload_bytes).hexdigest()


def _default(value, fallback):
    return fallback if value is None else value


def validate_collection_count(
    actual: int,
    *,
    target: int,
    minimum: int,
    require_target: bool,
) -> None:
    if target <= 0 or minimum <= 0 or minimum > target:
        raise SystemExit("target and minimum must be positive, with minimum <= target")
    if require_target and actual != target:
        raise SystemExit(
            f"only {actual} unique public job cards collected; exactly {target} required"
        )
    if actual < minimum:
        raise SystemExit(
            f"only {actual} unique public job cards collected; at least {minimum} required"
        )


def _write_json_atomic(path: Path, value) -> None:
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
