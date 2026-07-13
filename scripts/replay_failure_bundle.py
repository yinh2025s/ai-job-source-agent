from __future__ import annotations

import argparse
import json
import os
import shutil
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
    outcome_gate = manifest.get("outcome_gate")
    if isinstance(outcome_gate, dict) and outcome_gate.get("status") in {"failed", "incomplete"}:
        counts = outcome_gate.get("classification_counts", {})
        mismatch_count = counts.get("mismatch", 0)
        fixture_gap_count = counts.get("fixture_gap", 0)
        raise SystemExit(
            "failure replay gate failed: "
            f"{mismatch_count} outcome mismatch(es), {fixture_gap_count} fixture gap(s)"
        )


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

    _reset_checkpoint_output(output_root / "checkpoints")
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
    outcome_gate = _build_outcome_gate(replay_records, result_records)

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
        "outcome_gate": outcome_gate,
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
        "outcome_gate": {
            "status": "skipped",
            "classification_counts": {
                "reproduced": 0,
                "expected_transition": 0,
                "fixture_gap": 0,
                "mismatch": 0,
            },
            "records": [],
        },
    }


def _reset_checkpoint_output(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise FailureReplayError(f"Unsafe replay checkpoint output: {path}")
    if path.exists():
        shutil.rmtree(path)


def _build_outcome_gate(replay_records: list[dict], result_records: list[dict]) -> dict:
    comparisons = []
    counts = {
        "reproduced": 0,
        "expected_transition": 0,
        "fixture_gap": 0,
        "mismatch": 0,
    }
    record_count = max(len(replay_records), len(result_records))
    for index in range(record_count):
        replay_input = replay_records[index] if index < len(replay_records) else None
        replay_result = result_records[index] if index < len(result_records) else None
        original = _original_outcome(replay_input)
        expected_transition = _expected_transition(replay_input)
        replayed_original = _result_outcome(
            replay_result,
            failure_stage=_outcome_stage_name(original),
        )
        replayed_expected = (
            _result_outcome(
                replay_result,
                failure_stage=_outcome_stage_name(expected_transition),
            )
            if expected_transition is not None
            else replayed_original
        )
        if original == replayed_original and original is not None:
            classification = "reproduced"
            reason = "outcome_equal"
        elif _has_reason_code(replay_result, "OFFLINE_FIXTURE_MISSING"):
            classification = "fixture_gap"
            reason = "offline_fixture_missing"
        elif expected_transition is not None and expected_transition == replayed_expected:
            classification = "expected_transition"
            reason = "declared_transition_equal"
        else:
            classification = "mismatch"
            reason = (
                "record_count_changed"
                if replay_input is None or replay_result is None
                else "declared_transition_not_met"
                if expected_transition is not None
                else "outcome_changed"
            )
        counts[classification] += 1
        comparisons.append(
            {
                "index": index,
                "company_name": _record_field(replay_input, replay_result, "company_name"),
                "job_title": _record_field(
                    replay_input,
                    replay_result,
                    "job_title",
                    fallback="linkedin_job_title",
                ),
                "classification": classification,
                "reason": reason,
                "original_outcome": original,
                "expected_transition": expected_transition,
                "replay_outcome": (
                    replayed_expected
                    if expected_transition is not None
                    else replayed_original
                ),
            }
        )

    if counts["mismatch"]:
        status = "failed"
    elif counts["fixture_gap"]:
        status = "incomplete"
    else:
        status = "passed"
    return {
        "status": status,
        "classification_counts": counts,
        "records": comparisons,
    }


def _original_outcome(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    source_trace = record.get("source_trace")
    replay_metadata = source_trace.get("replay") if isinstance(source_trace, dict) else None
    if not isinstance(replay_metadata, dict):
        return None
    return {
        "pipeline_status": _optional_string(replay_metadata.get("pipeline_status")),
        "failure_stage": _stage_outcome(
            replay_metadata.get("first_non_success_stage")
        ),
    }


def _expected_transition(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    source_trace = record.get("source_trace")
    replay_metadata = source_trace.get("replay") if isinstance(source_trace, dict) else None
    transition = (
        replay_metadata.get("expected_transition")
        if isinstance(replay_metadata, dict)
        else None
    )
    if not isinstance(transition, dict):
        return None
    return {
        "pipeline_status": _optional_string(transition.get("pipeline_status")),
        "failure_stage": _stage_outcome(transition.get("failure_stage")),
    }


def _outcome_stage_name(outcome: dict | None) -> str | None:
    failure_stage = outcome.get("failure_stage") if isinstance(outcome, dict) else None
    if not isinstance(failure_stage, dict):
        return None
    return _optional_string(failure_stage.get("stage"))


def _result_outcome(record: dict | None, *, failure_stage: str | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    stages = record.get("stages")
    stage_by_name = {
        str(stage.get("stage")): stage
        for stage in stages if isinstance(stage, dict) and stage.get("stage")
    } if isinstance(stages, list) else {}
    replay_failure = stage_by_name.get(failure_stage) if failure_stage else None
    if not failure_stage:
        replay_failure = next(
            (
                stage_by_name[stage_name]
                for stage_name in PIPELINE_STAGES
                if stage_name in stage_by_name
                and stage_by_name[stage_name].get("status") not in {"success", "not_applicable"}
            ),
            None,
        )
    return {
        "pipeline_status": _optional_string(
            record.get("pipeline_status") or record.get("status")
        ),
        "failure_stage": _stage_outcome(replay_failure),
    }


def _stage_outcome(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        "stage": _optional_string(value.get("stage")),
        "status": _optional_string(value.get("status")),
        "reason_code": _optional_string(value.get("reason_code")),
    }


def _has_reason_code(record: dict | None, reason_code: str) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("stages"), list):
        return False
    return any(
        isinstance(stage, dict) and stage.get("reason_code") == reason_code
        for stage in record["stages"]
    )


def _record_field(
    primary: dict | None,
    secondary: dict | None,
    field: str,
    *,
    fallback: str | None = None,
) -> str | None:
    for record in (primary, secondary):
        if isinstance(record, dict):
            value = record.get(field) or (record.get(fallback) if fallback else None)
            if normalized := _optional_string(value):
                return normalized
    return None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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
