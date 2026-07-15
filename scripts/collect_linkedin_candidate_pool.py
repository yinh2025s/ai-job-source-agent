#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect LinkedIn public job cards without executing S2-S7.")
    parser.add_argument("--keyword", action="append", required=True)
    parser.add_argument("--location", default="United States")
    parser.add_argument("--per-keyword-limit", type=int, default=30)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--target", type=int, default=120)
    parser.add_argument("--fetch-timeout", type=float, default=8)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    discoverer = LinkedInJobsDiscoverer(Fetcher(timeout=args.fetch_timeout))
    records_by_id = {}
    query_counts = []
    for keyword in args.keyword:
        postings = discoverer.search(
            keywords=keyword,
            location=args.location,
            limit=args.per_keyword_limit,
            pages=args.pages,
        )
        query_counts.append({"keyword": keyword, "returned": len(postings)})
        for posting in postings:
            record = dataclass_to_dict(linkedin_postings_to_company_inputs([posting])[0])
            existing = records_by_id.get(posting.job_id)
            if existing is None:
                record["source_trace"]["blind_candidate_collection"] = {
                    "first_seen_keyword": keyword,
                    "matched_keywords": [keyword],
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_source": "public_search_card",
                }
                records_by_id[posting.job_id] = record
            else:
                collection = existing["source_trace"]["blind_candidate_collection"]
                if keyword not in collection["matched_keywords"]:
                    collection["matched_keywords"].append(keyword)
                if (
                    existing["company_name"] != record["company_name"]
                    or existing["job_title"] != record["job_title"]
                ):
                    collection["identity_conflict"] = True
            if len(records_by_id) >= args.target:
                break
        if len(records_by_id) >= args.target:
            break
    records = list(records_by_id.values())
    if len(records) < 50:
        raise SystemExit(f"only {len(records)} unique public job cards collected; at least 50 required")
    manifest = {
        "schema_version": "1.0",
        "collection_kind": "linkedin_public_search_cards_s1_only",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "location": args.location,
        "queries": query_counts,
        "unique_job_count": len(records),
        "pipeline_stages_executed": ["linkedin_public_search_collection"],
        "s2_s7_executed": False,
    }
    _write_json_atomic(Path(args.output), records)
    _write_json_atomic(Path(args.manifest), manifest)
    print(json.dumps({"unique_job_count": len(records), "queries": query_counts}))


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
