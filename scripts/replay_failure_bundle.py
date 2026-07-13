from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace

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
from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.run_configuration import DeterministicRunConfig
from job_source_agent.web import normalize_url
from scripts.export_replay_input import export_replay_records


BUNDLE_SCHEMA_VERSION = 3


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
    replay_records, source_records = _export_replay_records_with_sources(
        records,
        export_args,
    )
    if not replay_records:
        if allow_empty:
            manifest = _empty_bundle_manifest(args)
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
        source_records=source_records,
    )

    _write_json_atomic(output_root / "replay-results.json", result_records)
    _write_json_atomic(output_root / "replay-trace.json", trace_records)
    _write_json_atomic(output_root / "replay-summary.json", summary)
    manifest = {
        "status": "success",
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


def _export_replay_records_with_sources(
    records: list[dict],
    export_args: SimpleNamespace,
) -> tuple[list[dict], list[dict]]:
    replay_records: list[dict] = []
    source_records: list[dict] = []
    per_record_args = SimpleNamespace(**vars(export_args))
    per_record_args.limit = None
    for record in records:
        exported = export_replay_records([record], per_record_args)
        if not exported:
            continue
        replay_records.append(exported[0])
        source_records.append(record)
        if export_args.limit and len(replay_records) >= export_args.limit:
            break
    return replay_records, source_records


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
        resume_stage = _first_non_success_stage_name(replay_record)
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
    source_records: list[dict] | None = None,
) -> dict:
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
    return {
        "company_website_url": _canonical_public_url(record.get("company_website_url")),
        "hiring_entity_name": _normalized_identity_text(record.get("hiring_entity_name")),
        "career_page_url": _canonical_public_url(record.get("career_page_url")),
        "job_list_page_url": _canonical_public_url(record.get("job_list_page_url")),
        "open_position_url": _canonical_public_url(record.get("open_position_url")),
        "provider": _optional_string(result_provider(record)),
    }


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
