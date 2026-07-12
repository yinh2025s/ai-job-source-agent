from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.checkpoint import ADAPTER_VERSION, CHECKPOINT_SCHEMA_VERSION
from job_source_agent.composition import FetcherConfig, build_application
from job_source_agent.evaluation import summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import PIPELINE_STAGES, RESULT_SCHEMA_VERSION, dataclass_to_dict
from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots
from scripts.export_replay_input import export_replay_records


BUNDLE_SCHEMA_VERSION = 1


class FailureReplayError(ValueError):
    """Raised when a failure replay bundle cannot be built safely."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select failed results, validate snapshots, and replay the pipeline offline."
    )
    parser.add_argument("--results", required=True, help="Prior results.json or trace.json.")
    parser.add_argument("--snapshot-dir", required=True, help="Snapshot directory with snapshots.jsonl.")
    parser.add_argument("--output-dir", required=True, help="Directory for the self-contained replay bundle.")
    parser.add_argument("--pipeline-status", action="append")
    parser.add_argument("--stage", choices=PIPELINE_STAGES)
    parser.add_argument("--stage-status", action="append")
    parser.add_argument("--reason-code", action="append")
    parser.add_argument("--provider", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-missing-website", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        manifest = replay_failure_bundle(args)
    except (FailureReplayError, SnapshotReplayError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failure replay failed: {exc}") from exc
    print(json.dumps(manifest["summary"], sort_keys=True), flush=True)
    print(f"bundle: {Path(args.output_dir).resolve()}", flush=True)


def replay_failure_bundle(args: argparse.Namespace, *, allow_empty: bool = False) -> dict:
    results_path = Path(args.results).resolve()
    output_root = Path(args.output_dir).resolve()
    records = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise FailureReplayError("Results input must be a JSON array")

    export_args = SimpleNamespace(
        input=results_path.name,
        pipeline_status=args.pipeline_status,
        stage=args.stage,
        stage_status=args.stage_status,
        reason_code=args.reason_code,
        provider=args.provider,
        limit=args.limit,
        include_missing_website=args.include_missing_website,
    )
    replay_records = export_replay_records(records, export_args)
    if not replay_records:
        if allow_empty:
            manifest = _empty_bundle_manifest(args)
            _write_json_atomic(output_root / "bundle-manifest.json", manifest)
            return manifest
        raise FailureReplayError("No replayable records matched the requested filters")

    fixture_result = replay_snapshots(args.snapshot_dir, output_root / "offline")
    input_path = output_root / "replay-input.json"
    _write_json_atomic(input_path, replay_records)
    companies = load_company_inputs(input_path)
    application = build_application(
        FetcherConfig(fixtures_dir=output_root / "offline" / "sites", offline=True),
        checkpoint_dir=output_root / "checkpoints",
    )
    discoveries = [application.pipeline.discover(company) for company in companies]
    result_records = [result.result_record() for result in discoveries]
    trace_records = [dataclass_to_dict(result.trace_record()) for result in discoveries]
    summary = summarize_results(trace_records)

    _write_json_atomic(output_root / "replay-results.json", result_records)
    _write_json_atomic(output_root / "replay-trace.json", trace_records)
    _write_json_atomic(output_root / "replay-summary.json", summary)
    manifest = {
        "status": "success",
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "paths": {
            "input": "replay-input.json",
            "fixtures": "offline/sites",
            "snapshot_manifest": "offline/replay-manifest.json",
            "results": "replay-results.json",
            "trace": "replay-trace.json",
            "summary": "replay-summary.json",
            "checkpoints": "checkpoints",
        },
        "filters": {
            "pipeline_status": args.pipeline_status or [],
            "stage": args.stage,
            "stage_status": args.stage_status or [],
            "reason_code": args.reason_code or [],
            "provider": args.provider or [],
            "limit": args.limit,
        },
        "snapshot_summary": fixture_result.summary,
        "summary": summary,
    }
    _write_json_atomic(output_root / "bundle-manifest.json", manifest)
    return manifest


def _empty_bundle_manifest(args: argparse.Namespace) -> dict:
    return {
        "status": "skipped",
        "reason": "no_replayable_failure_records",
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "paths": {},
        "filters": {
            "pipeline_status": args.pipeline_status or [],
            "stage": args.stage,
            "stage_status": args.stage_status or [],
            "reason_code": args.reason_code or [],
            "provider": args.provider or [],
            "limit": args.limit,
        },
        "snapshot_summary": None,
        "summary": {"total": 0},
    }


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
