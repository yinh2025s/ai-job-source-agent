#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from job_source_agent.replay_record_plan import build_replay_record_plans
from job_source_agent.run_configuration import DeterministicRunConfig
from scripts.export_replay_input import export_replay_records


class StrictReplayAuditError(ValueError):
    pass


_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINKEDIN_JOB_ID = re.compile(r"(?:-|/)([0-9]{5,})(?:[/?#]|$)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Require full replay integrity and bind a measurement replay to its "
            "preflight, frozen cohort, live output, and run configuration."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight")
    parser.add_argument("--cohort")
    parser.add_argument("--live-results")
    parser.add_argument("--live-trace")
    parser.add_argument("--run-config")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Explicitly audit an historical manifest without measurement binding.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        manifest_path = Path(args.manifest)
        manifest, manifest_digest = _read_json(manifest_path)
        if args.legacy:
            report = audit_strict_replay(
                manifest,
                expected_records=args.expected_records,
                legacy=True,
            )
        else:
            required = {
                "--preflight": args.preflight,
                "--cohort": args.cohort,
                "--live-results": args.live_results,
                "--live-trace": args.live_trace,
                "--run-config": args.run_config,
            }
            missing = [flag for flag, value in required.items() if not value]
            if missing:
                raise StrictReplayAuditError(
                    "measurement mode requires " + ", ".join(missing)
                )
            preflight, preflight_digest = _read_json(Path(args.preflight))
            cohort, cohort_file_digest = _read_json(Path(args.cohort))
            live_results, results_digest = _read_json(Path(args.live_results))
            live_trace, trace_digest = _read_json(Path(args.live_trace))
            run_config, run_config_digest = _read_json(Path(args.run_config))
            replay_input, replay_results, replay_trace = _load_replay_artifacts(
                manifest_path, manifest
            )
            report = audit_strict_replay(
                manifest,
                expected_records=args.expected_records,
                preflight=preflight,
                frozen_cohort=cohort,
                live_results=live_results,
                live_trace=live_trace,
                run_config=run_config,
                replay_input=replay_input,
                replay_results=replay_results,
                replay_trace=replay_trace,
                artifact_digests={
                    "preflight": preflight_digest,
                    "cohort_file": cohort_file_digest,
                    "live_results": results_digest,
                    "live_trace": trace_digest,
                    "run_config": run_config_digest,
                    "replay_manifest": manifest_digest,
                },
            )
    except (StrictReplayAuditError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"strict replay audit failed: {error}") from error
    _write_json_atomic(Path(args.output), report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


def audit_strict_replay(
    manifest: Any,
    *,
    expected_records: int,
    preflight: Any = None,
    frozen_cohort: Any = None,
    live_results: Any = None,
    live_trace: Any = None,
    run_config: Any = None,
    replay_input: Any = None,
    replay_results: Any = None,
    replay_trace: Any = None,
    artifact_digests: Any = None,
    legacy: bool = False,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise StrictReplayAuditError("replay manifest must be an object")
    if expected_records <= 0:
        raise StrictReplayAuditError("expected record count must be positive")

    issues, counts, classifications = _audit_replay_gate(
        manifest, expected_records
    )
    bindings: dict[str, Any] = {}
    if legacy:
        mode = "legacy"
    else:
        mode = "measurement"
        measurement_issues, bindings = _audit_measurement_binding(
            manifest=manifest,
            expected_records=expected_records,
            preflight=preflight,
            frozen_cohort=frozen_cohort,
            live_results=live_results,
            live_trace=live_trace,
            run_config=run_config,
            replay_input=replay_input,
            replay_results=replay_results,
            replay_trace=replay_trace,
            artifact_digests=artifact_digests,
        )
        issues.extend(measurement_issues)

    issues = sorted(set(issues))
    return {
        "schema_version": "2.0",
        "mode": mode,
        "status": "passed" if not issues else "failed",
        "expected_records": expected_records,
        "integrity_counts": counts,
        "classification_counts": classifications,
        "bindings": bindings,
        "issues": issues,
    }


def _audit_replay_gate(
    manifest: dict[str, Any], expected_records: int
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    issues: list[str] = []
    if manifest.get("status") != "success":
        issues.append("bundle_status_not_success")

    integrity = manifest.get("record_integrity")
    if not isinstance(integrity, dict):
        issues.append("record_integrity_not_passed")
        counts: dict[str, Any] = {}
    else:
        counts = (
            integrity.get("counts")
            if isinstance(integrity.get("counts"), dict)
            else {}
        )
        if integrity.get("status") != "passed":
            issues.append("record_integrity_not_passed")
    for key in (
        "comparison_count",
        "export_attempted_count",
        "exported_count",
        "filter_matched_count",
        "result_count",
        "selected_count",
        "source_result_count",
        "trace_count",
    ):
        if counts.get(key) != expected_records:
            issues.append(f"{key}_mismatch")
    for key in ("limit_omitted_count", "replayability_dropped_count"):
        if counts.get(key) != 0:
            issues.append(f"{key}_nonzero")

    outcome = manifest.get("outcome_gate")
    if not isinstance(outcome, dict):
        issues.append("outcome_gate_not_passed")
        classifications: dict[str, Any] = {}
    else:
        classifications = (
            outcome.get("classification_counts")
            if isinstance(outcome.get("classification_counts"), dict)
            else {}
        )
        if outcome.get("status") != "passed":
            issues.append("outcome_gate_not_passed")
    if classifications.get("reproduced") != expected_records:
        issues.append("reproduced_count_mismatch")
    for key in (
        "budget_recovery",
        "expected_transition",
        "fixture_gap",
        "mismatch",
    ):
        if classifications.get(key) != 0:
            issues.append(f"{key}_nonzero")
    return issues, counts, classifications


def _audit_measurement_binding(
    *,
    manifest: dict[str, Any],
    expected_records: int,
    preflight: Any,
    frozen_cohort: Any,
    live_results: Any,
    live_trace: Any,
    run_config: Any,
    replay_input: Any,
    replay_results: Any,
    replay_trace: Any,
    artifact_digests: Any,
) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    named_objects = {
        "preflight": (preflight, dict),
        "frozen_cohort": (frozen_cohort, list),
        "live_results": (live_results, list),
        "live_trace": (live_trace, list),
        "run_config": (run_config, dict),
        "replay_input": (replay_input, list),
        "replay_results": (replay_results, list),
        "replay_trace": (replay_trace, list),
        "artifact_digests": (artifact_digests, dict),
    }
    for name, (value, kind) in named_objects.items():
        if not isinstance(value, kind):
            issues.append(f"{name}_missing_or_invalid")
    if issues:
        return issues, {}

    assert isinstance(preflight, dict)
    assert isinstance(frozen_cohort, list)
    assert isinstance(live_results, list)
    assert isinstance(live_trace, list)
    assert isinstance(run_config, dict)
    assert isinstance(replay_input, list)
    assert isinstance(replay_results, list)
    assert isinstance(replay_trace, list)
    assert isinstance(artifact_digests, dict)

    for name in (
        "preflight",
        "cohort_file",
        "live_results",
        "live_trace",
        "run_config",
        "replay_manifest",
    ):
        if _valid_sha(artifact_digests.get(name)) is None:
            issues.append(f"{name}_digest_missing_or_invalid")

    cohort_sha = _canonical_digest(frozen_cohort)
    if preflight.get("measurement_kind") != "development_diagnostic_nonsealed":
        issues.append("preflight_measurement_kind_mismatch")
    if preflight.get("record_count") != expected_records:
        issues.append("preflight_record_count_mismatch")
    if preflight.get("cohort_sha256") != cohort_sha:
        issues.append("cohort_canonical_digest_mismatch")
    if preflight.get("cohort_file_sha256") != artifact_digests.get("cohort_file"):
        issues.append("cohort_file_digest_mismatch")
    if preflight.get("run_configuration_sha256") != artifact_digests.get(
        "run_config"
    ):
        issues.append("run_config_file_digest_mismatch")
    if run_config.get("cohort_size") != expected_records:
        issues.append("run_config_record_count_mismatch")

    runtime_commit = preflight.get("runtime_code_commit")
    adapter_version = preflight.get("adapter_version")
    if not isinstance(runtime_commit, str) or not runtime_commit:
        issues.append("preflight_commit_missing")
    if not isinstance(adapter_version, str) or not adapter_version:
        issues.append("preflight_adapter_missing")
    if manifest.get("adapter_version") != adapter_version:
        issues.append("replay_adapter_mismatch")

    sequences: dict[str, list[str] | None] = {}
    for name, records in (
        ("cohort", frozen_cohort),
        ("live_results", live_results),
        ("live_trace", live_trace),
        ("replay_input", replay_input),
        ("replay_results", replay_results),
        ("replay_trace", replay_trace),
    ):
        if len(records) != expected_records:
            issues.append(f"{name}_record_count_mismatch")
        sequence = _linkedin_job_ids(records)
        if sequence is None:
            issues.append(f"{name}_linkedin_job_ids_invalid")
        elif len(set(sequence)) != len(sequence):
            issues.append(f"{name}_linkedin_job_ids_not_unique")
        sequences[name] = sequence
    cohort_ids = sequences["cohort"]
    if cohort_ids is not None:
        for name, sequence in sequences.items():
            if name != "cohort" and sequence != cohort_ids:
                issues.append(f"{name}_linkedin_job_id_order_mismatch")

    live_run_digest = _validate_live_run_configuration(
        live_results, live_trace, issues
    )
    manifest_run_digest = manifest.get("run_configuration_digest")
    if live_run_digest is None:
        issues.append("live_run_configuration_digest_invalid")
    elif manifest_run_digest != live_run_digest:
        issues.append("replay_run_configuration_digest_mismatch")
    manifest_run_payload = manifest.get("run_configuration")
    try:
        replay_derived_digest = DeterministicRunConfig.from_payload(
            manifest_run_payload
        ).digest
    except (TypeError, ValueError):
        issues.append("replay_run_configuration_invalid")
    else:
        if replay_derived_digest != manifest_run_digest:
            issues.append("replay_run_configuration_digest_invalid")

    expected_input: list[dict[str, Any]] = []
    expected_plans = ()
    try:
        expected_input = export_replay_records(
            live_results,
            SimpleNamespace(
                input="results.json",
                pipeline_status=None,
                stage=None,
                stage_status=None,
                reason_code=None,
                provider=None,
                limit=expected_records,
                include_missing_website=False,
            ),
        )
        expected_plans = build_replay_record_plans(live_results, expected_input)
        for record, plan in zip(expected_input, expected_plans, strict=True):
            record.setdefault("source_trace", {}).setdefault("replay", {})[
                "record_id"
            ] = plan.record_id
            record.pop("hiring_entity_name", None)
    except (TypeError, ValueError):
        issues.append("live_results_replay_projection_invalid")
    if len(expected_input) != expected_records:
        issues.append("live_results_replay_projection_count_mismatch")
    elif _canonical_digest(expected_input) != _canonical_digest(replay_input):
        issues.append("replay_input_digest_mismatch")

    plans = manifest.get("record_plans")
    outcome = manifest.get("outcome_gate")
    comparisons = outcome.get("records") if isinstance(outcome, dict) else None
    expected_plan_payload = [
        {
            "source_ordinal": plan.source_ordinal,
            "record_id": plan.record_id,
            "evidence_mode": plan.evidence_mode,
        }
        for plan in expected_plans
    ]
    if plans != expected_plan_payload:
        issues.append("replay_record_plans_mismatch")
    expected_record_ids = [item["record_id"] for item in expected_plan_payload]
    comparison_ids = (
        [item.get("record_id") for item in comparisons]
        if isinstance(comparisons, list)
        and all(isinstance(item, dict) for item in comparisons)
        else None
    )
    if comparison_ids != expected_record_ids:
        issues.append("replay_outcome_record_ids_mismatch")
    replay_trace_ids = [_replay_record_id(item) for item in replay_trace]
    if replay_trace_ids != expected_record_ids:
        issues.append("replay_trace_record_ids_mismatch")

    bindings = {
        "adapter_version": adapter_version,
        "runtime_code_commit": runtime_commit,
        "record_count": expected_records,
        "cohort_sha256": cohort_sha,
        "cohort_file_sha256": artifact_digests.get("cohort_file"),
        "run_configuration_sha256": artifact_digests.get("run_config"),
        "live_results_sha256": artifact_digests.get("live_results"),
        "live_trace_sha256": artifact_digests.get("live_trace"),
        "replay_manifest_sha256": artifact_digests.get("replay_manifest"),
        "live_run_configuration_digest": live_run_digest,
        "linkedin_job_ids_sha256": (
            _canonical_digest(cohort_ids) if cohort_ids is not None else None
        ),
        "replay_input_sha256": _canonical_digest(replay_input),
    }
    return issues, bindings


def _validate_live_run_configuration(
    live_results: list[Any],
    live_trace: list[Any],
    issues: list[str],
) -> str | None:
    derived: list[str] = []
    recorded: list[str] = []
    for name, records in (("live_results", live_results), ("live_trace", live_trace)):
        for record in records:
            if not isinstance(record, dict):
                issues.append(f"{name}_record_invalid")
                continue
            try:
                digest = DeterministicRunConfig.from_payload(
                    record.get("run_configuration")
                ).digest
            except (TypeError, ValueError):
                issues.append(f"{name}_run_configuration_invalid")
                continue
            derived.append(digest)
            recorded_digest = record.get("run_configuration_digest")
            if recorded_digest != digest:
                issues.append(f"{name}_run_configuration_digest_invalid")
            recorded.append(recorded_digest)
    unique = set(derived + recorded)
    return next(iter(unique)) if len(unique) == 1 else None


def _linkedin_job_ids(records: list[Any]) -> list[str] | None:
    values: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            return None
        value = record.get("linkedin_job_url")
        if not isinstance(value, str):
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not (
            parsed.hostname == "linkedin.com"
            or (parsed.hostname or "").endswith(".linkedin.com")
        ):
            return None
        match = _LINKEDIN_JOB_ID.search(parsed.path + "/")
        if match is None:
            return None
        values.append(match.group(1))
    return values


def _replay_record_id(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    trace = record.get("trace")
    payload = trace if isinstance(trace, dict) else record
    source_trace = payload.get("source_trace")
    replay = source_trace.get("replay") if isinstance(source_trace, dict) else None
    record_id = replay.get("record_id") if isinstance(replay, dict) else None
    return _valid_sha(record_id)


def _load_replay_artifacts(
    manifest_path: Path, manifest: Any
) -> tuple[Any, Any, Any]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("paths"), dict):
        raise StrictReplayAuditError("replay manifest paths are missing")
    root = manifest_path.resolve().parent
    loaded = []
    for key in ("input", "results", "trace"):
        relative = manifest["paths"].get(key)
        if not isinstance(relative, str) or not relative:
            raise StrictReplayAuditError(f"replay manifest path {key} is missing")
        path = (root / relative).resolve()
        if root != path and root not in path.parents:
            raise StrictReplayAuditError(f"replay manifest path {key} escapes bundle")
        payload, _ = _read_json(path)
        loaded.append(payload)
    return loaded[0], loaded[1], loaded[2]


def _read_json(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _valid_sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


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
