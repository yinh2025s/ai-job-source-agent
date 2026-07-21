from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA_VERSION = "1.0"
PUBLIC_FIELDS = (
    "company_name",
    "linkedin_company_url",
    "linkedin_job_url",
    "job_title",
    "job_location",
)


def prepare(source_path: Path, selection_path: Path) -> dict:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if source.get("schema_version") != "1.0" or not isinstance(source.get("postings"), list):
        raise ValueError("source cohort schema is incompatible")
    if selection.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("selection schema is incompatible")
    ids = selection.get("record_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("selection record_ids must be unique non-negative integers")
    postings = source["postings"]
    records = []
    for record_id in ids:
        if record_id >= len(postings) or not isinstance(postings[record_id], dict):
            raise ValueError(f"selected record {record_id:03d} is missing")
        posting = postings[record_id]
        record = {"record_id": f"{record_id:03d}"}
        for field in PUBLIC_FIELDS:
            value = posting.get(field)
            if field == "company_name" and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"selected record {record_id:03d} has no company name")
            if value is not None and not isinstance(value, str):
                raise ValueError(f"selected record {record_id:03d} has invalid {field}")
            record[field] = value
        records.append(record)
    record_digest = _digest(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "fixed eligible-G development input; contains no evaluator answer URLs",
        "source_cohort": source_path.name,
        "source_cohort_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "selection": selection_path.name,
        "record_count": len(records),
        "records_sha256": record_digest,
        "records": records,
    }


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = prepare(args.source, args.selection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
