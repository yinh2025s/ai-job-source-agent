#!/usr/bin/env python3.12
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.prepare_diagnostic_measurement import (
    DiagnosticMeasurementError,
    _adapter_version,
    _canonical_json_bytes,
    _clean_git_identity,
    _live_command,
)


class PreparedMeasurementRunError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate and execute one frozen diagnostic live command. "
            "A successful live remains unverified until finalization."
        )
    )
    parser.add_argument("--preflight", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        status = run_prepared_measurement(Path(args.preflight))
    except (
        DiagnosticMeasurementError,
        PreparedMeasurementRunError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit(f"prepared diagnostic run failed: {error}") from error
    print(json.dumps(status, sort_keys=True))


def run_prepared_measurement(
    preflight_path: Path,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    finalizer: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    preflight = load_and_validate_preflight(preflight_path)
    status_path = Path(preflight["paths"]["measurement_status"])
    if status_path.exists() or status_path.is_symlink():
        raise PreparedMeasurementRunError(
            "measurement status already exists; this run is not resumable"
        )

    running = _status_payload(preflight, "running")
    _write_json_atomic(status_path, running)
    try:
        completed = command_runner(
            preflight["live_command"],
            cwd=preflight["runtime_repo_root"],
            check=False,
        )
    except BaseException as error:
        failed = _status_payload(
            preflight,
            "live_failed",
            failure_type=type(error).__name__,
        )
        _write_json_atomic(status_path, failed)
        raise

    return_code = getattr(completed, "returncode", None)
    if return_code != 0:
        failed = _status_payload(
            preflight,
            "live_failed",
            live_return_code=return_code,
        )
        _write_json_atomic(status_path, failed)
        raise PreparedMeasurementRunError(
            f"frozen live command exited with status {return_code}"
        )

    status = _status_payload(
        preflight,
        "live_completed_unverified",
        live_return_code=0,
    )
    _write_json_atomic(status_path, status)
    if finalizer is None:
        from scripts.finalize_diagnostic_measurement import finalize_measurement

        finalizer = finalize_measurement
    try:
        final_report = finalizer(preflight_path)
    except BaseException as error:
        failed = _status_payload(
            preflight,
            "verification_failed",
            issues=[f"finalization_exception:{type(error).__name__}"],
        )
        _write_json_atomic(status_path, failed)
        raise
    if final_report.get("status") != "accepted":
        raise PreparedMeasurementRunError(
            "measurement finalization did not accept the run"
        )
    return final_report


def load_and_validate_preflight(
    preflight_path: Path,
    *,
    require_fresh_mutable_paths: bool = True,
) -> dict[str, Any]:
    payload_bytes = preflight_path.read_bytes()
    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict):
        raise PreparedMeasurementRunError("preflight must be an object")
    if payload.get("schema_version") != "1.0":
        raise PreparedMeasurementRunError("unsupported preflight schema")
    if payload.get("measurement_kind") != "development_diagnostic_nonsealed":
        raise PreparedMeasurementRunError("unsupported measurement kind")
    if payload.get("status") != "prepared_not_executed":
        raise PreparedMeasurementRunError("preflight is not prepared_not_executed")
    if payload.get("resume_allowed") is not False:
        raise PreparedMeasurementRunError("diagnostic measurement must not resume")
    if payload.get("runtime_python") != "3.12" or sys.version_info[:2] != (3, 12):
        raise PreparedMeasurementRunError("measurement runtime must be CPython 3.12")

    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise PreparedMeasurementRunError("preflight paths are missing")
    required_paths = {
        "audit",
        "checkpoints",
        "cohort",
        "cohort_manifest",
        "completions",
        "evidence",
        "live",
        "measurement_status",
        "preflight",
        "replay",
        "run_config",
        "snapshots",
    }
    if not required_paths.issubset(paths):
        raise PreparedMeasurementRunError("preflight paths are incomplete")
    resolved_preflight = preflight_path.resolve()
    if Path(paths["preflight"]).resolve() != resolved_preflight:
        raise PreparedMeasurementRunError("preflight path identity mismatch")
    root = Path(payload.get("artifact_root", "")).resolve()
    if not root.is_dir() or resolved_preflight.parent.parent != root:
        raise PreparedMeasurementRunError("artifact root identity mismatch")
    for key in required_paths:
        path = Path(paths[key]).resolve()
        if key != "measurement_status" and root not in path.parents:
            raise PreparedMeasurementRunError(f"{key} escapes artifact root")
        if key == "measurement_status" and path.parent != root:
            raise PreparedMeasurementRunError("measurement status escapes artifact root")

    _validate_contract_digests(payload, paths)
    repo_root = Path(payload.get("runtime_repo_root", "")).resolve()
    head, tree = _clean_git_identity(repo_root)
    if head != payload.get("runtime_code_commit"):
        raise PreparedMeasurementRunError("runtime Git commit changed after preflight")
    if tree != payload.get("runtime_source_tree_sha256"):
        raise PreparedMeasurementRunError("runtime source tree changed after preflight")
    if _adapter_version(repo_root) != payload.get("adapter_version"):
        raise PreparedMeasurementRunError("adapter version changed after preflight")

    command = payload.get("live_command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(value, str) and value for value in command)
    ):
        raise PreparedMeasurementRunError("live command is invalid")
    if command[0] != sys.executable:
        raise PreparedMeasurementRunError("live command runtime differs from preflight")
    if command[1:2] != ["scripts/live_batch_eval.py"]:
        raise PreparedMeasurementRunError("live command entrypoint is invalid")
    if "--no-resume" not in command or "--require-full-cohort" not in command:
        raise PreparedMeasurementRunError("live command lacks cold-run gates")
    if "--enable-parallel-candidate-discovery" not in command:
        raise PreparedMeasurementRunError("live command lacks three-route enablement")
    run_config = json.loads(Path(paths["run_config"]).read_text(encoding="utf-8"))
    expected_command = _live_command(
        run_config,
        {key: Path(value) for key, value in paths.items()},
    )
    if command != expected_command:
        raise PreparedMeasurementRunError(
            "live command differs from frozen contract"
        )

    if require_fresh_mutable_paths:
        _validate_fresh_mutable_paths(paths)
    return payload


def _validate_contract_digests(payload: dict[str, Any], paths: dict[str, str]) -> None:
    cohort_bytes = Path(paths["cohort"]).read_bytes()
    cohort = json.loads(cohort_bytes)
    checks = (
        ("cohort_file_sha256", cohort_bytes),
        ("cohort_manifest_sha256", Path(paths["cohort_manifest"]).read_bytes()),
        ("run_configuration_sha256", Path(paths["run_config"]).read_bytes()),
    )
    for field, value in checks:
        if hashlib.sha256(value).hexdigest() != payload.get(field):
            raise PreparedMeasurementRunError(f"{field} changed after preflight")
    if (
        not isinstance(cohort, list)
        or hashlib.sha256(_canonical_json_bytes(cohort)).hexdigest()
        != payload.get("cohort_sha256")
        or len(cohort) != payload.get("record_count")
    ):
        raise PreparedMeasurementRunError("cohort identity changed after preflight")


def _validate_fresh_mutable_paths(paths: dict[str, str]) -> None:
    directory_keys = (
        "audit",
        "checkpoints",
        "completions",
        "live",
        "replay",
        "snapshots",
    )
    for key in directory_keys:
        path = Path(paths[key])
        if not path.is_dir() or any(path.iterdir()):
            raise PreparedMeasurementRunError(
                f"{key} must be an existing empty directory"
            )
    evidence = Path(paths["evidence"])
    if evidence.exists() or evidence.is_symlink():
        raise PreparedMeasurementRunError(
            "company discovery evidence must not preexist"
        )


def _status_payload(
    preflight: dict[str, Any],
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "measurement_kind": preflight["measurement_kind"],
        "status": status,
        "record_count": preflight["record_count"],
        "runtime_code_commit": preflight["runtime_code_commit"],
        "runtime_source_tree_sha256": preflight["runtime_source_tree_sha256"],
        "adapter_version": preflight["adapter_version"],
        "cohort_sha256": preflight["cohort_sha256"],
        "run_configuration_sha256": preflight["run_configuration_sha256"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


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
