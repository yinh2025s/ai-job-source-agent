from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.checkpoint import checkpoint_metadata
from job_source_agent.evaluation import result_provider
from job_source_agent.models import PIPELINE_STAGES


REPLAY_FIELDS = (
    "company_name",
    "company_website_url",
    "hiring_entity_name",
    "career_root_url",
    "linkedin_job_url",
    "external_apply_url",
    "linkedin_company_url",
    "job_title",
    "job_location",
    "source",
    "source_trace",
    "checkpoint",
)

_SOURCE_POSTING_FIELDS = ("status", "availability")
_LINKEDIN_POSTING_FIELDS = (
    "availability",
    "apply_mode",
    "evidence_source",
    "job_url",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export prior results into clean replay input records.")
    parser.add_argument("--input", required=True, help="Prior results.json or trace.json.")
    parser.add_argument("--output", required=True, help="Path for replay input JSON.")
    parser.add_argument("--pipeline-status", action="append", help="Filter by pipeline_status, e.g. failed.")
    parser.add_argument("--stage", help="Filter by a pipeline stage name.")
    parser.add_argument("--stage-status", action="append", help="Filter by the selected stage status.")
    parser.add_argument("--reason-code", action="append", help="Filter by any stage reason_code.")
    parser.add_argument("--provider", action="append", help="Filter by detected provider/host.")
    parser.add_argument("--limit", type=int, help="Maximum records to export.")
    parser.add_argument(
        "--include-missing-website",
        action="store_true",
        help="Include records that only have a LinkedIn company URL and need S2 replay.",
    )
    parser.add_argument("--summary-output", help="Optional JSON export summary path.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    records = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit("Input must be a JSON array of result or trace records.")

    replay_records = export_replay_records(records, args)
    Path(args.output).write_text(json.dumps(replay_records, indent=2), encoding="utf-8")
    summary = {
        "input": args.input,
        "output": args.output,
        "read": len(records),
        "exported": len(replay_records),
        "filters": {
            "pipeline_status": args.pipeline_status or [],
            "stage": args.stage,
            "stage_status": args.stage_status or [],
            "reason_code": args.reason_code or [],
            "provider": args.provider or [],
            "include_missing_website": args.include_missing_website,
        },
    }
    if args.summary_output:
        Path(args.summary_output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"exported {len(replay_records)} replay records to {args.output}", flush=True)


def export_replay_records(records: list[dict], args: argparse.Namespace) -> list[dict]:
    exported = []
    for record in records:
        if not _matches_filters(record, args):
            continue
        replay_record = _to_replay_record(record, source_path=args.input)
        if not _has_required_source(replay_record, include_missing_website=args.include_missing_website):
            continue
        exported.append(replay_record)
        if args.limit and len(exported) >= args.limit:
            break
    return exported


def _matches_filters(record: dict, args: argparse.Namespace) -> bool:
    if args.pipeline_status and str(record.get("pipeline_status") or record.get("status")) not in args.pipeline_status:
        return False
    if args.stage:
        stage = _stage_by_name(record).get(args.stage)
        if not stage:
            return False
        if args.stage_status and str(stage.get("status")) not in args.stage_status:
            return False
    if args.reason_code:
        reason_codes = {str(stage.get("reason_code")) for stage in _stage_by_name(record).values() if stage.get("reason_code")}
        if not reason_codes.intersection(args.reason_code):
            return False
    if args.provider and _result_provider(record) not in args.provider:
        return False
    return True


def _to_replay_record(record: dict, source_path: str) -> dict:
    source_trace = _stable_source_trace(record)
    source_trace["replay"] = {
        "source_result_file": source_path,
        "pipeline_status": record.get("pipeline_status") or record.get("status"),
        "provider": _result_provider(record),
        "first_non_success_stage": _first_non_success_stage(record),
    }
    replay_record = {
        "company_name": record.get("company_name") or "",
        "company_website_url": record.get("company_website_url") or "",
        "hiring_entity_name": record.get("hiring_entity_name"),
        "career_root_url": record.get("career_root_url") or record.get("career_page_url"),
        "linkedin_job_url": record.get("linkedin_job_url") or "",
        "external_apply_url": record.get("external_apply_url"),
        "linkedin_company_url": record.get("linkedin_company_url"),
        "job_title": record.get("job_title") or record.get("linkedin_job_title"),
        "job_location": record.get("job_location") or record.get("linkedin_job_location"),
        "source": "replay_input",
        "source_trace": source_trace,
    }
    replay_record["checkpoint"] = checkpoint_metadata(replay_record)
    return {key: value for key, value in replay_record.items() if key in REPLAY_FIELDS and value is not None}


def _stable_source_trace(record: dict) -> dict:
    source_trace = record.get("source_trace")
    if not isinstance(source_trace, dict):
        trace = record.get("trace")
        source_trace = trace.get("source_trace") if isinstance(trace, dict) else None
    if not isinstance(source_trace, dict):
        return {}

    stable: dict = {}
    posting_status = _nonempty_string(source_trace.get("posting_status"))
    if posting_status:
        stable["posting_status"] = posting_status

    source_posting = _stable_string_fields(
        source_trace.get("source_posting"),
        _SOURCE_POSTING_FIELDS,
    )
    if source_posting:
        stable["source_posting"] = source_posting

    linkedin_posting = _stable_string_fields(
        source_trace.get("linkedin_posting"),
        _LINKEDIN_POSTING_FIELDS,
    )
    if linkedin_posting:
        stable["linkedin_posting"] = linkedin_posting
    return stable


def _stable_string_fields(value: object, fields: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        field: normalized
        for field in fields
        if (normalized := _nonempty_string(value.get(field))) is not None
    }


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _has_required_source(record: dict, include_missing_website: bool) -> bool:
    if not record.get("company_name"):
        return False
    if record.get("company_website_url"):
        return True
    if record.get("external_apply_url"):
        return True
    return include_missing_website and bool(record.get("linkedin_company_url"))


def _stage_by_name(record: dict) -> dict[str, dict]:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return {}
    return {
        str(stage.get("stage")): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage")
    }


def _first_non_success_stage(record: dict) -> dict | None:
    stage_by_name = _stage_by_name(record)
    for stage_name in PIPELINE_STAGES:
        stage = stage_by_name.get(stage_name)
        if stage and stage.get("status") not in {"success", "not_applicable"}:
            return {
                "stage": stage_name,
                "status": stage.get("status"),
                "reason_code": stage.get("reason_code"),
            }
    return None


def _result_provider(record: dict) -> str:
    return result_provider(record)


if __name__ == "__main__":
    main()
