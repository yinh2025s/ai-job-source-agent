#!/usr/bin/env python3.12
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.prepare_diagnostic_cohort import (
    _candidate_query_contract_sha256,
    _canonical_json_bytes,
    _cohort_identity_rows,
    _company_key,
    _linkedin_company_slug,
    _linkedin_job_id,
    _record_identity,
)


SCHEMA_VERSION = "1.0"
REQUIRED_CONFIG_KEYS = frozenset(
    {
        "candidate_discovery_engine",
        "career_search_timeout_seconds",
        "cohort_size",
        "company_time_budget_seconds",
        "evaluate_all_candidate_routes",
        "fetch_retries",
        "fetch_timeout_seconds",
        "full_outcome_replay_after_live",
        "max_ats_board_fetches",
        "max_career_candidates",
        "max_career_fetches",
        "max_career_search_queries",
        "max_career_transport_calls",
        "max_job_board_attempts",
        "max_job_pages",
        "render_js",
        "retry_base_delay_seconds",
        "schema_version",
        "search_backend",
        "skip_sitemap",
        "verify_limit",
        "website_time_budget_seconds",
        "workers",
    }
)


class DiagnosticMeasurementError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and prepare an isolated non-sealed diagnostic measurement "
            "without executing network work."
        )
    )
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--cohort-manifest", required=True)
    parser.add_argument("--run-config", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        prepared = prepare_measurement(
            cohort_path=Path(args.cohort),
            cohort_manifest_path=Path(args.cohort_manifest),
            run_config_path=Path(args.run_config),
            artifact_root=Path(args.artifact_root),
            repo_root=Path(args.repo_root),
        )
    except (
        DiagnosticMeasurementError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        raise SystemExit(f"diagnostic measurement preparation failed: {error}") from error
    print(
        json.dumps(
            {
                "artifact_root": prepared["artifact_root"],
                "cohort_sha256": prepared["cohort_sha256"],
                "record_count": prepared["record_count"],
                "runtime_code_commit": prepared["runtime_code_commit"],
                "status": prepared["status"],
            },
            sort_keys=True,
        )
    )


def prepare_measurement(
    *,
    cohort_path: Path,
    cohort_manifest_path: Path,
    run_config_path: Path,
    artifact_root: Path,
    repo_root: Path,
    runtime_version: tuple[int, int] | None = None,
    git_identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    runtime = runtime_version or sys.version_info[:2]
    if tuple(runtime) != (3, 12):
        raise DiagnosticMeasurementError("measurement runtime must be CPython 3.12")

    cohort_bytes = cohort_path.read_bytes()
    manifest_bytes = cohort_manifest_path.read_bytes()
    config_bytes = run_config_path.read_bytes()
    cohort = json.loads(cohort_bytes)
    manifest = json.loads(manifest_bytes)
    config = json.loads(config_bytes)
    if (
        not isinstance(cohort, list)
        or not isinstance(manifest, dict)
        or not isinstance(config, dict)
    ):
        raise DiagnosticMeasurementError(
            "cohort, manifest and run configuration types are invalid"
        )
    _validate_cohort_manifest(cohort, manifest)
    _validate_run_config(config, len(cohort))

    source_paths = {
        cohort_path.resolve(),
        cohort_manifest_path.resolve(),
        run_config_path.resolve(),
    }
    root = artifact_root.resolve()
    if artifact_root.exists() or artifact_root.is_symlink():
        raise DiagnosticMeasurementError("artifact root must not already exist")
    if any(root == path or root in path.parents for path in source_paths):
        raise DiagnosticMeasurementError("contract inputs cannot be inside artifact root")
    if _has_symlink_parent(artifact_root):
        raise DiagnosticMeasurementError("artifact root cannot traverse a symlink")

    head, tree = git_identity or _clean_git_identity(repo_root)
    layout = _layout(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / "contract").mkdir(exist_ok=False)
    for key in (
        "live",
        "checkpoints",
        "completions",
        "snapshots",
        "replay",
        "audit",
    ):
        layout[key].mkdir(parents=True, exist_ok=False)
    shutil.copyfile(cohort_path, layout["cohort"])
    shutil.copyfile(cohort_manifest_path, layout["cohort_manifest"])
    shutil.copyfile(run_config_path, layout["run_config"])

    command = _live_command(config, layout)
    execution_command = [
        sys.executable,
        "scripts/run_prepared_diagnostic_measurement.py",
        "--preflight",
        str(layout["preflight"]),
    ]
    prepared = {
        "schema_version": SCHEMA_VERSION,
        "measurement_kind": "development_diagnostic_nonsealed",
        "status": "prepared_not_executed",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "artifact_root": str(root),
        "record_count": len(cohort),
        "cohort_sha256": hashlib.sha256(
            _canonical_json_bytes(cohort)
        ).hexdigest(),
        "cohort_file_sha256": hashlib.sha256(cohort_bytes).hexdigest(),
        "cohort_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "run_configuration_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "runtime_python": ".".join(map(str, runtime)),
        "runtime_code_commit": head,
        "runtime_source_tree_sha256": tree,
        "runtime_repo_root": str(repo_root.resolve()),
        "adapter_version": _adapter_version(repo_root),
        "mutable_roots_preexisting": False,
        "mutable_roots_are_disjoint": True,
        "resume_allowed": False,
        "command": execution_command,
        "live_command": command,
        "paths": {key: str(path) for key, path in layout.items()},
    }
    _write_json_atomic(layout["preflight"], prepared)
    return prepared


def _validate_cohort_manifest(
    cohort: list[Any],
    manifest: dict[str, Any],
) -> None:
    if manifest.get("schema_version") != "1.0":
        raise DiagnosticMeasurementError("unsupported cohort manifest schema")
    if manifest.get("cohort_provenance") != "development_diagnostic_nonsealed":
        raise DiagnosticMeasurementError(
            "cohort is not development_diagnostic_nonsealed"
        )
    expected_sha = hashlib.sha256(_canonical_json_bytes(cohort)).hexdigest()
    if manifest.get("cohort_sha256") != expected_sha:
        raise DiagnosticMeasurementError("cohort digest does not match manifest")
    if manifest.get("record_count") != len(cohort) or not cohort:
        raise DiagnosticMeasurementError("cohort record count does not match manifest")

    company_keys: set[str] = set()
    job_ids: set[str] = set()
    company_slugs: set[str] = set()
    for record in cohort:
        if not isinstance(record, dict):
            raise DiagnosticMeasurementError("cohort record is not an object")
        try:
            company_key = _company_key(record["company_name"])
            job_id = _linkedin_job_id(record["linkedin_job_url"])
            company_slug = _linkedin_company_slug(
                record.get("linkedin_company_url"), required=False
            )
            _record_identity(record)
        except (KeyError, ValueError) as error:
            raise DiagnosticMeasurementError(
                "cohort contains an invalid record identity"
            ) from error
        if company_key in company_keys or job_id in job_ids:
            raise DiagnosticMeasurementError(
                "cohort companies and LinkedIn job IDs must be unique"
            )
        if company_slug and company_slug in company_slugs:
            raise DiagnosticMeasurementError(
                "cohort LinkedIn company slugs must be unique"
            )
        company_keys.add(company_key)
        job_ids.add(job_id)
        if company_slug:
            company_slugs.add(company_slug)

    identity_sha = hashlib.sha256(
        _canonical_json_bytes(_cohort_identity_rows(cohort))
    ).hexdigest()
    if manifest.get("cohort_identity_sha256") != identity_sha:
        raise DiagnosticMeasurementError(
            "cohort identity digest does not match manifest"
        )
    expected_counts = {
        "independent_company_count": len(company_keys),
        "unique_linkedin_job_id_count": len(job_ids),
        "unique_linkedin_company_slug_count": len(company_slugs),
    }
    if any(manifest.get(key) != value for key, value in expected_counts.items()):
        raise DiagnosticMeasurementError("cohort identity counts do not match manifest")
    selection = manifest.get("selection")
    if not isinstance(selection, dict) or any(
        selection.get(key) != 0
        for key in (
            "post_selection_company_overlap_count",
            "post_selection_linkedin_company_overlap_count",
            "post_selection_linkedin_job_overlap_count",
        )
    ):
        raise DiagnosticMeasurementError("cohort overlap gate is not clean")
    if manifest.get("s2_s7_executed_during_selection") is not False:
        raise DiagnosticMeasurementError("selection must not execute S2-S7")
    collection_contract = manifest.get("candidate_collection_contract")
    if (
        not isinstance(collection_contract, dict)
        or collection_contract.get("status") != "bound"
        or collection_contract.get("bound_record_count") != len(cohort)
        or not isinstance(collection_contract.get("sha256"), str)
    ):
        raise DiagnosticMeasurementError(
            "cohort is not bound to one frozen query collection contract"
        )
    contract_sha = collection_contract["sha256"]
    try:
        inconsistent_contract = any(
            _candidate_query_contract_sha256(record) != contract_sha
            for record in cohort
        )
    except ValueError as error:
        raise DiagnosticMeasurementError(
            "cohort query collection contract binding is invalid"
        ) from error
    if inconsistent_contract:
        raise DiagnosticMeasurementError(
            "cohort query collection contract binding is inconsistent"
        )


def _validate_run_config(config: dict[str, Any], record_count: int) -> None:
    if set(config) != REQUIRED_CONFIG_KEYS:
        missing = sorted(REQUIRED_CONFIG_KEYS - set(config))
        extra = sorted(set(config) - REQUIRED_CONFIG_KEYS)
        raise DiagnosticMeasurementError(
            f"run configuration keys differ: missing={missing}, extra={extra}"
        )
    if config.get("schema_version") != "1.0":
        raise DiagnosticMeasurementError("unsupported run configuration schema")
    if config.get("cohort_size") != record_count:
        raise DiagnosticMeasurementError(
            "run configuration cohort size differs from frozen cohort"
        )
    if isinstance(config.get("cohort_size"), bool) or not isinstance(
        config.get("cohort_size"), int
    ):
        raise DiagnosticMeasurementError("cohort size must be an integer")
    if config.get("candidate_discovery_engine") != "stage_v1":
        raise DiagnosticMeasurementError("diagnostic measurement must use stage_v1")
    if config.get("search_backend") != "legacy":
        raise DiagnosticMeasurementError("diagnostic measurement must use legacy search")
    if config.get("full_outcome_replay_after_live") is not True:
        raise DiagnosticMeasurementError("full outcome replay must be enabled")
    numeric_positive = (
        "career_search_timeout_seconds",
        "company_time_budget_seconds",
        "fetch_timeout_seconds",
        "max_ats_board_fetches",
        "max_career_candidates",
        "max_career_fetches",
        "max_career_search_queries",
        "max_career_transport_calls",
        "max_job_board_attempts",
        "max_job_pages",
        "retry_base_delay_seconds",
        "verify_limit",
        "website_time_budget_seconds",
        "workers",
    )
    if any(
        isinstance(config.get(key), bool)
        or not isinstance(config.get(key), (int, float))
        or config[key] <= 0
        for key in numeric_positive
    ):
        raise DiagnosticMeasurementError("run configuration bounds must be positive")
    retries = config.get("fetch_retries")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise DiagnosticMeasurementError("fetch retries must be a nonnegative integer")
    if (
        config["workers"] > 4
        or retries > 2
        or config["fetch_timeout_seconds"] > 30
        or config["company_time_budget_seconds"] > 300
        or config["website_time_budget_seconds"] > 120
        or config["max_career_transport_calls"] > 64
        or config["max_job_pages"] > 10
    ):
        raise DiagnosticMeasurementError("run configuration exceeds diagnostic bounds")
    for key in (
        "evaluate_all_candidate_routes",
        "render_js",
        "skip_sitemap",
    ):
        if not isinstance(config.get(key), bool):
            raise DiagnosticMeasurementError(f"{key} must be boolean")


def _layout(root: Path) -> dict[str, Path]:
    return {
        "cohort": root / "contract" / "cohort.json",
        "cohort_manifest": root / "contract" / "cohort-manifest.json",
        "run_config": root / "contract" / "run-config.json",
        "preflight": root / "contract" / "preflight.json",
        "live": root / "live",
        "checkpoints": root / "state" / "checkpoints",
        "completions": root / "state" / "completions",
        "evidence": root / "state" / "company-discovery-evidence.json",
        "snapshots": root / "capture" / "snapshots",
        "replay": root / "replay" / "full",
        "audit": root / "audit",
        "measurement_status": root / "measurement-status.json",
    }


def _live_command(config: dict[str, Any], paths: dict[str, Path]) -> list[str]:
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
        sys.executable,
        "scripts/live_batch_eval.py",
        "--input",
        str(paths["cohort"]),
        "--limit",
        str(config["cohort_size"]),
        "--require-full-cohort",
        "--no-resume",
        "--candidate-discovery-engine",
        config["candidate_discovery_engine"],
        "--search-backend",
        config["search_backend"],
    ]
    for key, flag in pairs:
        command.extend((flag, str(config[key])))
    if config["evaluate_all_candidate_routes"]:
        command.extend(
            (
                "--enable-parallel-candidate-discovery",
                "--evaluate-all-candidate-routes",
                "--route-evaluation-output",
                str(paths["live"] / "route-evaluation.json"),
            )
        )
    if config["render_js"]:
        command.append("--render-js")
    if config["skip_sitemap"]:
        command.append("--skip-sitemap")
    command.extend(
        (
            "--output",
            str(paths["live"] / "results.json"),
            "--trace-output",
            str(paths["live"] / "trace.json"),
            "--summary-output",
            str(paths["live"] / "summary.json"),
            "--checkpoint-dir",
            str(paths["checkpoints"]),
            "--batch-checkpoint-dir",
            str(paths["completions"]),
            "--company-discovery-evidence-store",
            str(paths["evidence"]),
            "--snapshot-dir",
            str(paths["snapshots"]),
            "--replay-bundle-dir",
            str(paths["replay"]),
            "--replay-bundle-limit",
            str(config["cohort_size"]),
        )
    )
    return command


def _clean_git_identity(repo_root: Path) -> tuple[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    if status:
        raise DiagnosticMeasurementError(
            "worktree must be completely clean before measurement preparation"
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    return head, tree


def _adapter_version(repo_root: Path) -> str:
    checkpoint_path = repo_root / "job_source_agent" / "checkpoint.py"
    value: Any = None
    for line in checkpoint_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADAPTER_VERSION = "):
            try:
                value = ast.literal_eval(line.partition("=")[2].strip())
            except (SyntaxError, ValueError):
                value = None
            break
    if not isinstance(value, str) or not value:
        raise DiagnosticMeasurementError("adapter version cannot be read")
    return value


def _has_symlink_parent(path: Path) -> bool:
    current = path.expanduser().absolute()
    for parent in (current, *current.parents):
        if parent.exists() and parent.is_symlink():
            return True
    return False


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
