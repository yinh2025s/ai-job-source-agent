#!/usr/bin/env python3.12
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.audit_exact_identities import (
    ExactIdentityAuditError,
    audit_exact_identities,
)
from scripts.audit_strict_replay import (
    StrictReplayAuditError,
    _load_replay_artifacts,
    audit_strict_replay,
)
from scripts.run_prepared_diagnostic_measurement import (
    PreparedMeasurementRunError,
    _status_payload,
    _write_json_atomic,
    load_and_validate_preflight,
)
from scripts.scan_artifact_privacy import (
    ArtifactPrivacyError,
    scan_artifact_root,
)


class DiagnosticMeasurementFinalizationError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize one prepared diagnostic measurement only after full live, "
            "route, Exact, replay and privacy gates pass."
        )
    )
    parser.add_argument("--preflight", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        report = finalize_measurement(Path(args.preflight))
    except (
        ArtifactPrivacyError,
        DiagnosticMeasurementFinalizationError,
        ExactIdentityAuditError,
        OSError,
        PreparedMeasurementRunError,
        StrictReplayAuditError,
        json.JSONDecodeError,
    ) as error:
        raise SystemExit(
            f"diagnostic measurement finalization failed: {error}"
        ) from error
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "accepted":
        raise SystemExit(1)


def finalize_measurement(preflight_path: Path) -> dict[str, Any]:
    preflight = load_and_validate_preflight(
        preflight_path,
        require_fresh_mutable_paths=False,
    )
    paths = preflight["paths"]
    status_path = Path(paths["measurement_status"])
    status = _load_json(status_path)
    _validate_live_status(preflight, status)

    audit_dir = Path(paths["audit"])
    audit_dir.mkdir(parents=True, exist_ok=True)
    exact_report_path = audit_dir / "exact-identities.json"
    replay_report_path = audit_dir / "strict-replay.json"
    privacy_report_path = audit_dir / "artifact-privacy.json"
    final_report_path = audit_dir / "measurement-finalization.json"

    issues: list[str] = []
    results_path = Path(paths["live"]) / "results.json"
    trace_path = Path(paths["live"]) / "trace.json"
    summary_path = Path(paths["live"]) / "summary.json"
    routes_path = Path(paths["live"]) / "route-evaluation.json"
    replay_manifest_path = Path(paths["replay"]) / "bundle-manifest.json"
    expected = preflight["record_count"]

    results = _load_json(results_path)
    trace = _load_json(trace_path)
    summary = _load_json(summary_path)
    routes = _load_json(routes_path)
    cohort = _load_json(Path(paths["cohort"]))
    run_config = _load_json(Path(paths["run_config"]))
    replay_manifest = _load_json(replay_manifest_path)

    if not isinstance(results, list) or len(results) != expected:
        issues.append("live_results_count_mismatch")
    if not isinstance(trace, list) or len(trace) != expected:
        issues.append("live_trace_count_mismatch")
    if not isinstance(summary, dict) or summary.get("total") != expected:
        issues.append("live_summary_count_mismatch")
    if not isinstance(routes, dict) or routes.get("record_count") != expected:
        issues.append("route_evaluation_count_mismatch")
    if isinstance(summary, dict) and isinstance(routes, dict):
        manifest = summary.get("evaluation_manifest")
        companies_sha = (
            manifest.get("companies_sha256")
            if isinstance(manifest, dict)
            else None
        )
        if not companies_sha or routes.get("cohort_companies_sha256") != companies_sha:
            issues.append("route_evaluation_cohort_mismatch")
        if (
            routes.get("run_configuration_digest")
            != summary.get("run_configuration_digest")
        ):
            issues.append("route_evaluation_run_config_mismatch")

    exact_report = audit_exact_identities(
        trace,
        cohort_records=cohort,
        result_records=results,
    )
    _write_json_atomic(exact_report_path, exact_report)
    if (
        exact_report.get("status") != "passed"
        or exact_report.get("measurement_bound") is not True
        or exact_report.get("trace_record_count") != expected
    ):
        issues.append("exact_identity_audit_failed")

    replay_input, replay_results, replay_trace = _load_replay_artifacts(
        replay_manifest_path,
        replay_manifest,
    )
    replay_report = audit_strict_replay(
        replay_manifest,
        expected_records=expected,
        preflight=preflight,
        frozen_cohort=cohort,
        live_results=results,
        live_trace=trace,
        run_config=run_config,
        replay_input=replay_input,
        replay_results=replay_results,
        replay_trace=replay_trace,
        artifact_digests={
            "preflight": _sha256(preflight_path),
            "cohort_file": _sha256(Path(paths["cohort"])),
            "live_results": _sha256(results_path),
            "live_trace": _sha256(trace_path),
            "run_config": _sha256(Path(paths["run_config"])),
            "replay_manifest": _sha256(replay_manifest_path),
        },
    )
    _write_json_atomic(replay_report_path, replay_report)
    if (
        replay_report.get("status") != "passed"
        or replay_report.get("mode") != "measurement"
    ):
        issues.append("strict_replay_audit_failed")

    privacy_report = scan_artifact_root(Path(preflight["artifact_root"]))
    _write_json_atomic(privacy_report_path, privacy_report)
    if privacy_report.get("total_matches") != 0:
        issues.append("artifact_privacy_matches")

    issues = sorted(set(issues))
    report = {
        "schema_version": "1.0",
        "measurement_kind": preflight["measurement_kind"],
        "status": "accepted" if not issues else "verification_failed",
        "issues": issues,
        "record_count": expected,
        "exact_count": exact_report.get("exact_count"),
        "runtime_code_commit": preflight["runtime_code_commit"],
        "runtime_source_tree_sha256": preflight["runtime_source_tree_sha256"],
        "adapter_version": preflight["adapter_version"],
        "cohort_sha256": preflight["cohort_sha256"],
        "run_configuration_sha256": preflight["run_configuration_sha256"],
        "artifacts": {
            "live_results_sha256": _sha256(results_path),
            "live_trace_sha256": _sha256(trace_path),
            "live_summary_sha256": _sha256(summary_path),
            "route_evaluation_sha256": _sha256(routes_path),
            "replay_manifest_sha256": _sha256(replay_manifest_path),
            "exact_audit_sha256": _sha256(exact_report_path),
            "replay_audit_sha256": _sha256(replay_report_path),
            "privacy_audit_sha256": _sha256(privacy_report_path),
        },
        "privacy": {
            "files_scanned": privacy_report.get("files_scanned"),
            "bytes_scanned": privacy_report.get("bytes_scanned"),
            "total_matches": privacy_report.get("total_matches"),
        },
    }
    _write_json_atomic(final_report_path, report)
    terminal_status = _status_payload(
        preflight,
        report["status"],
        issues=issues,
        final_report_sha256=_sha256(final_report_path),
    )
    _write_json_atomic(status_path, terminal_status)
    return report


def _validate_live_status(
    preflight: dict[str, Any],
    status: Any,
) -> None:
    if not isinstance(status, dict):
        raise DiagnosticMeasurementFinalizationError(
            "measurement status must be an object"
        )
    if status.get("status") != "live_completed_unverified":
        raise DiagnosticMeasurementFinalizationError(
            "live status is not live_completed_unverified"
        )
    for field in (
        "adapter_version",
        "cohort_sha256",
        "record_count",
        "run_configuration_sha256",
        "runtime_code_commit",
        "runtime_source_tree_sha256",
    ):
        if status.get(field) != preflight.get(field):
            raise DiagnosticMeasurementFinalizationError(
                f"measurement status {field} differs from preflight"
            )
    if status.get("live_return_code") != 0:
        raise DiagnosticMeasurementFinalizationError(
            "live command did not exit successfully"
        )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
