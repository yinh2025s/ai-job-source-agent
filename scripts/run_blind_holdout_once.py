#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BlindExecutionError(ValueError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume one frozen blind holdout exactly once.")
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--run-config", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        execute_once(
            cohort_path=Path(args.cohort),
            holdout_manifest_path=Path(args.holdout_manifest),
            run_config_path=Path(args.run_config),
            artifact_dir=Path(args.artifact_dir),
            ledger_path=Path(args.ledger),
            repo_root=repo_root,
        )
    except (BlindExecutionError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"blind holdout execution refused: {error}") from error


def execute_once(
    *, cohort_path: Path, holdout_manifest_path: Path, run_config_path: Path,
    artifact_dir: Path, ledger_path: Path, repo_root: Path,
) -> dict[str, Any]:
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    manifest = json.loads(holdout_manifest_path.read_text(encoding="utf-8"))
    config_bytes = run_config_path.read_bytes()
    config = json.loads(config_bytes)
    if not isinstance(cohort, list) or not isinstance(manifest, dict) or not isinstance(config, dict):
        raise BlindExecutionError("cohort, manifest, or run configuration has invalid type")
    if manifest.get("cohort_provenance") != "blind_unseen":
        raise BlindExecutionError("holdout manifest is not blind_unseen")
    cohort_sha = hashlib.sha256(_canonical_json_bytes(cohort)).hexdigest()
    if manifest.get("cohort_sha256") != cohort_sha:
        raise BlindExecutionError("cohort digest does not match frozen manifest")
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    if manifest.get("run_configuration_sha256") != config_sha:
        raise BlindExecutionError("run configuration digest does not match frozen manifest")
    head, tree = _clean_git_identity(repo_root)
    if manifest.get("code_commit") != head or manifest.get("source_tree_sha256") != tree:
        raise BlindExecutionError("runtime code identity differs from frozen manifest")
    if len(cohort) != config.get("cohort_size") or not 30 <= len(cohort) <= 50:
        raise BlindExecutionError("cohort size differs from frozen run configuration")

    paths = {
        "results": artifact_dir / "results.json",
        "trace": artifact_dir / "trace.json",
        "summary": artifact_dir / "summary.json",
        "snapshots": artifact_dir / "snapshots",
        "checkpoints": artifact_dir / "checkpoints",
        "batch": artifact_dir / "batch",
    }
    command = _live_command(config, cohort_path, paths)
    if artifact_dir.exists():
        raise BlindExecutionError("artifact directory already exists; refusing blind execution")
    if ledger_path.exists():
        raise BlindExecutionError("one-shot ledger already exists; rerun is forbidden")

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    ledger = {
        "schema_version": "1.0", "run_id": run_id, "status": "consumed",
        "started_at": started_at, "cohort_sha256": cohort_sha,
        "code_commit": head, "source_tree_sha256": tree,
        "run_configuration_sha256": config_sha,
    }
    _create_ledger_once(ledger_path, ledger)
    artifact_dir.mkdir(parents=True, exist_ok=False)
    completed = subprocess.run(command, cwd=repo_root, check=False)
    completed_at = datetime.now(timezone.utc).isoformat()
    execution = {
        "schema_version": "1.0",
        "execution_kind": "blind_holdout_once",
        "run_id": run_id,
        "status": "complete" if completed.returncode == 0 else "failed",
        "return_code": completed.returncode,
        "started_at": started_at,
        "completed_at": completed_at,
        "cohort_provenance_before_execution": "blind_unseen",
        "cohort_provenance_after_execution": "blind_observed",
        "cohort_sha256": cohort_sha,
        "holdout_manifest_sha256": hashlib.sha256(holdout_manifest_path.read_bytes()).hexdigest(),
        "run_configuration_sha256": config_sha,
        "code_commit": head,
        "source_tree_sha256": tree,
        "command": command,
        "artifact_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
            if name in {"results", "trace", "summary"} and path.is_file()
        },
        "live_execution_count": 1,
    }
    _write_json_atomic(artifact_dir / "execution-manifest.json", execution)
    if completed.returncode != 0:
        raise BlindExecutionError(
            f"one allowed live execution exited {completed.returncode}; cohort is now observed"
        )
    if set(execution["artifact_sha256"]) != {"results", "trace", "summary"}:
        raise BlindExecutionError("live execution did not publish all required artifacts")
    return execution


def _live_command(config: dict[str, Any], cohort: Path, paths: dict[str, Path]) -> list[str]:
    pairs = (
        ("fetch_timeout_seconds", "--fetch-timeout"),
        ("fetch_retries", "--fetch-retries"),
        ("retry_base_delay_seconds", "--retry-base-delay"),
        ("career_search_timeout_seconds", "--career-search-timeout"),
        ("max_career_search_queries", "--max-career-search-queries"),
        ("verify_limit", "--verify-limit"),
        ("max_career_candidates", "--max-career-candidates"),
        ("max_career_fetches", "--max-career-fetches"),
        ("max_career_transport_calls", "--max-career-transport-calls"),
        ("max_ats_board_fetches", "--max-ats-board-fetches"),
        ("max_job_pages", "--max-job-pages"),
        ("max_job_board_attempts", "--max-job-board-attempts"),
        ("company_time_budget_seconds", "--company-time-budget"),
        ("website_time_budget_seconds", "--website-time-budget"),
        ("workers", "--workers"),
    )
    command = [
        sys.executable, "scripts/live_batch_eval.py", "--input", str(cohort),
        "--limit", str(config["cohort_size"]),
    ]
    for key, flag in pairs:
        command.extend((flag, str(config[key])))
    if config.get("render_js"):
        command.append("--render-js")
    if config.get("skip_sitemap"):
        command.append("--skip-sitemap")
    command.extend((
        "--output", str(paths["results"]),
        "--trace-output", str(paths["trace"]),
        "--summary-output", str(paths["summary"]),
        "--snapshot-dir", str(paths["snapshots"]),
        "--checkpoint-dir", str(paths["checkpoints"]),
        "--batch-checkpoint-dir", str(paths["batch"]),
        "--no-resume",
    ))
    return command


def _clean_git_identity(repo_root: Path) -> tuple[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_root,
        check=True, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if status:
        raise BlindExecutionError("tracked worktree changed after cohort freeze")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root, check=True,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    return head, tree


def _create_ledger_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise BlindExecutionError("one-shot ledger already exists; rerun is forbidden") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True, allow_nan=False)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write_json_atomic(path: Path, value: Any) -> None:
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
