from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.checkpoint import (
    ADAPTER_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    execution_fingerprint,
)
from job_source_agent.composition import AgentConfig, FetcherConfig, build_application
from job_source_agent.contracts import StageExecution
from job_source_agent.evaluation import result_provider, summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import (
    PIPELINE_STAGES,
    RESULT_SCHEMA_VERSION,
    StageResult,
    dataclass_to_dict,
)
from job_source_agent.providers.base import (
    PageAwareProviderAdapter,
    PageProbeProviderAdapter,
)
from job_source_agent.providers.registry import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.run_configuration import DeterministicRunConfig
from job_source_agent.web import normalize_url
from scripts.export_replay_input import _matches_filters, export_replay_records


BUNDLE_SCHEMA_VERSION = 5


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
    parser.add_argument(
        "--legacy-run-config",
        choices=("composition-defaults",),
        help="Explicitly replay legacy records that predate deterministic run metadata.",
    )
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
        integrity = manifest.get("record_integrity", {})
        if integrity.get("status") == "failed":
            reason_codes = ", ".join(
                reason["code"] for reason in integrity.get("reasons", [])
            )
            raise SystemExit(
                "failure replay gate failed: record integrity failed"
                + (f" ({reason_codes})" if reason_codes else "")
            )
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
    replay_records, source_records, selection_counts = (
        _export_replay_records_with_sources(records, export_args)
    )
    preflight_integrity = _build_record_integrity(
        args,
        selection_counts,
        result_count=0,
        trace_count=0,
        comparison_count=0,
    )
    if _selection_integrity_failed(preflight_integrity):
        manifest = _empty_bundle_manifest(
            args,
            status="failed",
            reason="record_integrity_failed",
            record_integrity=preflight_integrity,
        )
        _write_json_atomic(output_root / "bundle-manifest.json", manifest)
        return manifest
    if not replay_records:
        record_integrity = preflight_integrity
        if record_integrity["status"] == "failed":
            manifest = _empty_bundle_manifest(
                args,
                status="failed",
                reason="record_integrity_failed",
                record_integrity=record_integrity,
            )
            _write_json_atomic(output_root / "bundle-manifest.json", manifest)
            return manifest
        if allow_empty:
            manifest = _empty_bundle_manifest(
                args,
                record_integrity=record_integrity,
            )
            _write_json_atomic(output_root / "bundle-manifest.json", manifest)
            return manifest
        raise FailureReplayError("No replayable records matched the requested filters")

    run_configuration, run_configuration_provenance = _resolve_run_configuration(
        source_records,
        getattr(args, "legacy_run_config", None),
    )

    _reset_checkpoint_output(output_root / "checkpoints")
    fixture_result = replay_snapshots(args.snapshot_dir, output_root / "offline")
    input_path = output_root / "replay-input.json"
    _write_json_atomic(input_path, replay_records)
    companies = load_company_inputs(input_path)
    resume_stages = _seed_authoritative_handoffs(
        companies,
        replay_records,
        source_records,
        output_root / "checkpoints",
        run_configuration,
    )
    application = build_application(
        FetcherConfig(fixtures_dir=output_root / "offline" / "sites", offline=True),
        run_configuration.to_agent_config(),
        checkpoint_dir=output_root / "checkpoints",
    )
    discoveries = [
        application.pipeline.discover(company, start_at=resume_stage)
        for company, resume_stage in zip(companies, resume_stages, strict=True)
    ]
    result_records = [result.result_record() for result in discoveries]
    trace_records = [dataclass_to_dict(result.trace_record()) for result in discoveries]
    summary = summarize_results(trace_records)
    summary["run_configuration"] = run_configuration.to_payload()
    summary["run_configuration_digest"] = run_configuration.digest
    outcome_gate = _build_outcome_gate(
        replay_records,
        result_records,
        trace_records=trace_records,
        source_records=source_records,
    )
    record_integrity = _build_record_integrity(
        args,
        selection_counts,
        result_count=len(result_records),
        trace_count=len(trace_records),
        comparison_count=len(outcome_gate["records"]),
    )
    if record_integrity["status"] == "failed":
        outcome_gate["status"] = "failed"

    _write_json_atomic(output_root / "replay-results.json", result_records)
    _write_json_atomic(output_root / "replay-trace.json", trace_records)
    _write_json_atomic(output_root / "replay-summary.json", summary)
    manifest = {
        "status": "failed" if record_integrity["status"] == "failed" else "success",
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "run_configuration": run_configuration.to_payload(),
        "run_configuration_digest": run_configuration.digest,
        "run_configuration_provenance": run_configuration_provenance,
        "paths": {
            "input": "replay-input.json",
            "fixtures": "offline/sites",
            "snapshot_manifest": "offline/replay-manifest.json",
            "fetch_failures": "offline/fetch-failures.json",
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
        "record_integrity": record_integrity,
        "outcome_gate": outcome_gate,
    }
    _write_json_atomic(output_root / "bundle-manifest.json", manifest)
    return manifest


def _empty_bundle_manifest(
    args: argparse.Namespace,
    *,
    status: str = "skipped",
    reason: str = "no_replayable_failure_records",
    record_integrity: dict | None = None,
) -> dict:
    return {
        "status": status,
        "reason": reason,
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
        "record_integrity": record_integrity,
        "outcome_gate": {
            "status": "failed" if status == "failed" else "skipped",
            "classification_counts": {
                "reproduced": 0,
                "expected_transition": 0,
                "budget_recovery": 0,
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


def _export_replay_records_with_sources(
    records: list[dict],
    export_args: SimpleNamespace,
) -> tuple[list[dict], list[dict], dict[str, int]]:
    replay_records: list[dict] = []
    source_records: list[dict] = []
    per_record_args = SimpleNamespace(**vars(export_args))
    per_record_args.limit = None
    selected_records = [
        record for record in records if _matches_filters(record, per_record_args)
    ]
    export_attempted_count = 0
    replayability_dropped_count = 0
    for record in selected_records:
        if (
            export_args.limit
            and replay_records
            and len(replay_records) >= export_args.limit
        ):
            break
        export_attempted_count += 1
        exported = export_replay_records([record], per_record_args)
        if not exported:
            replayability_dropped_count += 1
            continue
        replay_records.append(exported[0])
        source_records.append(record)
    counts = {
        "source_result_count": len(records),
        "filter_matched_count": len(selected_records),
        "selected_count": len(source_records),
        "export_attempted_count": export_attempted_count,
        "exported_count": len(replay_records),
        "replayability_dropped_count": replayability_dropped_count,
        "limit_omitted_count": len(selected_records) - export_attempted_count,
    }
    return replay_records, source_records, counts


def _build_record_integrity(
    args: argparse.Namespace,
    selection_counts: dict[str, int],
    *,
    result_count: int,
    trace_count: int,
    comparison_count: int,
) -> dict:
    source_count = selection_counts["source_result_count"]
    explicit_filters = any(
        (
            args.pipeline_status,
            args.stage,
            args.stage_status,
            args.reason_code,
            args.provider,
        )
    )
    limit_covers_source = args.limit is None or args.limit >= source_count
    full_coverage_required = not explicit_filters and limit_covers_source
    counts = {
        **selection_counts,
        "result_count": result_count,
        "trace_count": trace_count,
        "comparison_count": comparison_count,
    }
    reasons: list[dict[str, object]] = []
    if full_coverage_required:
        checks = (
            (
                "filter_match_count_mismatch",
                source_count,
                counts["filter_matched_count"],
            ),
            ("selection_count_mismatch", source_count, counts["selected_count"]),
            ("export_count_mismatch", source_count, counts["exported_count"]),
            ("result_count_mismatch", source_count, result_count),
            ("trace_count_mismatch", source_count, trace_count),
            ("comparison_count_mismatch", source_count, comparison_count),
        )
        reasons.extend(
            {"code": code, "expected": expected, "actual": actual}
            for code, expected, actual in checks
            if expected != actual
        )
        if counts["replayability_dropped_count"]:
            reasons.append(
                {
                    "code": "replayability_records_dropped",
                    "count": counts["replayability_dropped_count"],
                }
            )
    elif explicit_filters:
        reasons.append({"code": "explicit_failure_filters"})
    else:
        reasons.append(
            {
                "code": "limit_below_source_count",
                "limit": args.limit,
                "source_result_count": source_count,
            }
        )
    return {
        "status": "failed" if full_coverage_required and reasons else "passed",
        "full_coverage_required": full_coverage_required,
        "counts": counts,
        "reasons": reasons,
    }


def _selection_integrity_failed(record_integrity: dict) -> bool:
    selection_reason_codes = {
        "filter_match_count_mismatch",
        "selection_count_mismatch",
        "export_count_mismatch",
        "replayability_records_dropped",
    }
    return record_integrity.get("status") == "failed" and any(
        reason.get("code") in selection_reason_codes
        for reason in record_integrity.get("reasons", [])
    )


def _seed_authoritative_handoffs(
    companies: list,
    replay_records: list[dict],
    source_records: list[dict],
    checkpoint_root: Path,
    run_configuration: DeterministicRunConfig,
) -> list[str | None]:
    store = FilesystemCheckpointStore(checkpoint_root)
    resume_stages: list[str | None] = []
    for company, replay_record, source_record in zip(
        companies,
        replay_records,
        source_records,
        strict=True,
    ):
        resume_stage = _replay_resume_stage(
            source_record,
            _first_non_success_stage_name(replay_record),
        )
        executions = _authoritative_upstream_executions(source_record, resume_stage)
        if executions is None:
            resume_stages.append(None)
            continue
        fingerprint = execution_fingerprint(asdict(company), run_configuration.digest)
        for execution in executions:
            store.save(fingerprint, execution)
        resume_stages.append(resume_stage)
    return resume_stages


def _resolve_run_configuration(
    source_records: list[dict],
    legacy_mode: str | None,
) -> tuple[DeterministicRunConfig, str]:
    payloads = [record.get("run_configuration") for record in source_records]
    present = [payload for payload in payloads if payload is not None]
    if not present:
        if legacy_mode != "composition-defaults":
            raise FailureReplayError(
                "Selected records predate run configuration metadata; pass "
                "--legacy-run-config composition-defaults to replay them explicitly"
            )
        return DeterministicRunConfig.from_agent_config(AgentConfig()), "legacy_defaulted"
    if len(present) != len(payloads):
        raise FailureReplayError("Selected records mix missing and versioned run configurations")
    try:
        configurations = [DeterministicRunConfig.from_payload(payload) for payload in present]
    except ValueError as error:
        raise FailureReplayError(f"Invalid source run configuration: {error}") from error
    first = configurations[0]
    if any(configuration.digest != first.digest for configuration in configurations[1:]):
        raise FailureReplayError("Selected records contain incompatible run configurations")
    for source_record in source_records:
        recorded_digest = source_record.get("run_configuration_digest")
        if recorded_digest is not None and recorded_digest != first.digest:
            raise FailureReplayError("Source run configuration digest does not match its payload")
    return first, "source_record"


def _first_non_success_stage_name(replay_record: dict) -> str | None:
    source_trace = replay_record.get("source_trace")
    replay = source_trace.get("replay") if isinstance(source_trace, dict) else None
    stage = replay.get("first_non_success_stage") if isinstance(replay, dict) else None
    stage_name = stage.get("stage") if isinstance(stage, dict) else None
    return stage_name if stage_name in PIPELINE_STAGES else None


def _replay_resume_stage(source_record: dict, failure_stage: str | None) -> str | None:
    if failure_stage != "opening_match":
        return failure_stage
    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    job_board_trace = (
        stage_traces.get("job_board_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    provider_detection = (
        job_board_trace.get("provider_detection")
        if isinstance(job_board_trace, dict)
        else None
    )
    method = (
        provider_detection.get("method")
        if isinstance(provider_detection, dict)
        else None
    )
    if method in {"page_evidence", "page_probe"}:
        return "job_board_discovery"
    if method is None and _results_require_page_derived_board(source_record):
        return "job_board_discovery"
    return failure_stage


def _results_require_page_derived_board(source_record: dict) -> bool:
    stages = source_record.get("stages")
    job_list_url = source_record.get("job_list_page_url")
    if not isinstance(stages, list) or not isinstance(job_list_url, str) or not job_list_url:
        return False
    try:
        parsed_url = urlparse(job_list_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            return False
    except (TypeError, ValueError):
        return False
    job_board_result = next(
        (
            stage
            for stage in stages
            if isinstance(stage, dict) and stage.get("stage") == "job_board_discovery"
        ),
        None,
    )
    provider = job_board_result.get("provider") if isinstance(job_board_result, dict) else None
    if not isinstance(provider, str) or not provider:
        return False
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
    if adapter is None or not isinstance(
        adapter,
        (PageAwareProviderAdapter, PageProbeProviderAdapter),
    ):
        return False
    try:
        return adapter.identify_board(job_list_url) is None
    except (TypeError, ValueError):
        return False


def _authoritative_upstream_executions(
    source_record: dict,
    resume_stage: str | None,
) -> list[StageExecution] | None:
    if resume_stage is None:
        return None
    resume_index = PIPELINE_STAGES.index(resume_stage)
    stages = source_record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    upstream = PIPELINE_STAGES[:resume_index]
    if any(
        stage_name not in stage_by_name
        or stage_by_name[stage_name].get("status") not in {"success", "not_applicable"}
        for stage_name in upstream
    ):
        return None

    result_fields = {field.name for field in fields(StageResult)}
    executions = [
        StageExecution(
            result=StageResult(
                **{
                    key: value
                    for key, value in stage_by_name[stage_name].items()
                    if key in result_fields
                }
            ),
            updates=_authoritative_stage_updates(stage_name, source_record),
            trace={},
        )
        for stage_name in upstream
    ]
    required_update_by_stage = {
        "website_resolution": "company_website_url",
        "career_discovery": "career_page_url",
        "job_board_discovery": "job_list_page_url",
        "opening_match": "open_position_url",
    }
    if any(
        execution.result.status == "success"
        and (required := required_update_by_stage.get(execution.result.stage)) is not None
        and required not in execution.updates
        for execution in executions
    ):
        return None
    return executions


def _authoritative_stage_updates(stage: str, source_record: dict) -> dict:
    fields_by_stage = {
        "website_resolution": ("company_website_url",),
        "hiring_identity_resolution": (
            "company_website_url",
            "hiring_entity_name",
            "career_root_url",
        ),
        "career_discovery": ("career_page_url",),
        "job_board_discovery": ("job_list_page_url",),
        "opening_match": ("job_list_page_url", "open_position_url"),
    }
    updates = {
        field: source_record[field]
        for field in fields_by_stage.get(stage, ())
        if source_record.get(field) not in (None, "")
    }
    if stage == "job_board_discovery":
        result = next(
            (
                item
                for item in source_record.get("stages", [])
                if isinstance(item, dict) and item.get("stage") == stage
            ),
            {},
        )
        if result.get("provider"):
            updates["provider"] = result["provider"]
    return updates


def _build_outcome_gate(
    replay_records: list[dict],
    result_records: list[dict],
    *,
    trace_records: list[dict] | None = None,
    source_records: list[dict] | None = None,
) -> dict:
    comparisons = []
    counts = {
        "reproduced": 0,
        "expected_transition": 0,
        "budget_recovery": 0,
        "fixture_gap": 0,
        "mismatch": 0,
    }
    record_count = max(len(replay_records), len(result_records))
    for index in range(record_count):
        replay_input = replay_records[index] if index < len(replay_records) else None
        replay_result = result_records[index] if index < len(result_records) else None
        replay_trace = (
            trace_records[index]
            if trace_records is not None and index < len(trace_records)
            else None
        )
        source_record = (
            source_records[index]
            if source_records is not None and index < len(source_records)
            else None
        )
        compare_identity = bool(
            source_record is not None
            and (
                _optional_string(source_record.get("pipeline_status")) == "success"
                or _optional_string(source_record.get("status")) == "success"
            )
        )
        original = (
            _source_outcome(source_record, include_identity=compare_identity)
            if source_record is not None
            else _original_outcome(replay_input)
        )
        expected_transition = _expected_transition(replay_input)
        replayed_original = _result_outcome(
            replay_result,
            failure_stage=_outcome_stage_name(original),
            include_identity=compare_identity,
        )
        replayed_expected = (
            _result_outcome(
                replay_result,
                failure_stage=_outcome_stage_name(expected_transition),
                include_identity=False,
            )
            if expected_transition is not None
            else replayed_original
        )
        budget_replay_outcome = _result_outcome(
            replay_result,
            failure_stage=None,
            include_identity=True,
        )
        source_identity_prefix = _successful_identity_prefix(source_record)
        successful_outcome_reproduced = bool(
            source_record is not None
            and original is not None
            and original.get("pipeline_status") == "success"
            and original == replayed_original
        )
        if successful_outcome_reproduced:
            classification = "reproduced"
            reason = "outcome_equal"
        elif _contains_reason_code(
            (replay_result, replay_trace),
            "OFFLINE_FIXTURE_MISSING",
        ):
            classification = "fixture_gap"
            reason = "offline_fixture_missing"
        elif original == replayed_original and original is not None:
            classification = "reproduced"
            reason = "outcome_equal"
        elif (
            expected_transition is not None
            and expected_transition == replayed_expected
            and _identity_prefix_matches(source_record, replay_result)
        ):
            classification = "expected_transition"
            reason = "declared_transition_equal"
        elif (
            expected_transition is None
            and _is_budget_recovery(source_record, replay_result)
        ):
            classification = "budget_recovery"
            reason = "company_budget_replay_advanced"
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
                    else budget_replay_outcome
                    if classification == "budget_recovery"
                    else replayed_original
                ),
                "source_identity_prefix": source_identity_prefix,
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


def _source_outcome(
    record: dict | None,
    *,
    include_identity: bool,
) -> dict | None:
    if not isinstance(record, dict):
        return None
    outcome = {
        "pipeline_status": _optional_string(record.get("pipeline_status") or record.get("status")),
        "failure_stage": _stage_outcome(_first_non_success_result_stage(record)),
    }
    if include_identity:
        outcome["result_identity"] = _result_identity(record)
    return outcome


def _result_outcome(
    record: dict | None,
    *,
    failure_stage: str | None,
    include_identity: bool = False,
) -> dict | None:
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
    outcome = {
        "pipeline_status": _optional_string(
            record.get("pipeline_status") or record.get("status")
        ),
        "failure_stage": _stage_outcome(replay_failure),
    }
    if include_identity:
        outcome["result_identity"] = _result_identity(record)
    return outcome


def _first_non_success_result_stage(record: dict) -> dict | None:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    return next(
        (
            stage_by_name[stage_name]
            for stage_name in PIPELINE_STAGES
            if stage_name in stage_by_name
            and stage_by_name[stage_name].get("status") not in {"success", "not_applicable"}
        ),
        None,
    )


def _result_identity(record: dict) -> dict:
    provider = _optional_string(result_provider(record))
    return {
        "company_website_url": _canonical_public_url(record.get("company_website_url")),
        "hiring_entity_name": _normalized_identity_text(record.get("hiring_entity_name")),
        "career_page_url": _canonical_provider_board_identity(
            record.get("career_page_url"),
            provider,
        ),
        "job_list_page_url": _canonical_provider_board_identity(
            record.get("job_list_page_url"),
            provider,
        ),
        "open_position_url": _canonical_public_url(record.get("open_position_url")),
        "provider": provider,
    }


def _canonical_provider_board_identity(
    value: object,
    provider: str | None,
) -> str | None:
    canonical_url = _canonical_public_url(value)
    if canonical_url is None or provider is None:
        return canonical_url
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
    if adapter is None or not adapter.supports_listing:
        return canonical_url
    board = adapter.identify_board(canonical_url)
    if board is None or board.provider != provider:
        return canonical_url
    return _canonical_public_url(board.url)


def _successful_identity_prefix(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    identity = _result_identity(record)
    fields_by_stage = {
        "website_resolution": ("company_website_url",),
        "hiring_identity_resolution": (
            "company_website_url",
            "hiring_entity_name",
        ),
        "career_discovery": ("career_page_url",),
        "job_board_discovery": ("job_list_page_url", "provider"),
        "opening_match": ("open_position_url",),
    }
    prefix: dict[str, str | None] = {}
    for stage_name in PIPELINE_STAGES:
        stage = stage_by_name.get(stage_name)
        if stage is None or stage.get("status") not in {"success", "not_applicable"}:
            break
        if stage.get("status") == "success":
            for field in fields_by_stage.get(stage_name, ()):
                prefix[field] = identity[field]
            if stage_name == "hiring_identity_resolution":
                career_root = _canonical_public_url(record.get("career_root_url"))
                if career_root is not None:
                    prefix["career_root_url"] = career_root
    return prefix


def _identity_prefix_matches(source: dict | None, replay: dict | None) -> bool:
    prefix = _successful_identity_prefix(source)
    if prefix is None:
        return source is None
    if not isinstance(replay, dict):
        return False
    replay_identity = _result_identity(replay)
    if "career_root_url" in prefix:
        replay_identity["career_root_url"] = _canonical_public_url(
            replay.get("career_root_url")
        )
    return all(replay_identity.get(field) == value for field, value in prefix.items())


def _is_budget_recovery(source: dict | None, replay: dict | None) -> bool:
    if not isinstance(source, dict) or not isinstance(replay, dict):
        return False
    source_failure = _first_non_success_result_stage(source)
    if not isinstance(source_failure, dict):
        return False
    source_stage = _optional_string(source_failure.get("stage"))
    if (
        source_failure.get("reason_code") != "COMPANY_TIME_BUDGET_EXHAUSTED"
        or source_stage not in PIPELINE_STAGES
        or _authoritative_upstream_executions(source, source_stage) is None
        or not _identity_prefix_matches(source, replay)
        or _has_reason_code(replay, "COMPANY_TIME_BUDGET_EXHAUSTED")
    ):
        return False

    replay_stages = replay.get("stages")
    if not isinstance(replay_stages, list):
        return False
    replay_by_name = {
        stage.get("stage"): stage
        for stage in replay_stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    completed = replay_by_name.get(source_stage)
    if not isinstance(completed, dict) or completed.get("status") not in {
        "success",
        "not_applicable",
    }:
        return False
    replay_failure = _first_non_success_result_stage(replay)
    if replay_failure is None:
        return True
    replay_stage = _optional_string(replay_failure.get("stage"))
    return bool(
        replay_stage in PIPELINE_STAGES
        and PIPELINE_STAGES.index(replay_stage) > PIPELINE_STAGES.index(source_stage)
    )


def _canonical_public_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = normalize_url(value)
    except (TypeError, ValueError):
        return None
    return normalized[:-1] if normalized.endswith("/") and normalized.count("/") > 2 else normalized


def _normalized_identity_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


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


def _contains_reason_code(value: object, reason_code: str) -> bool:
    if isinstance(value, dict):
        return value.get("reason_code") == reason_code or any(
            _contains_reason_code(nested, reason_code)
            for nested in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_reason_code(item, reason_code) for item in value)
    return False


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
