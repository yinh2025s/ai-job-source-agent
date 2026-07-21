from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.candidate_reasoning_clients import (
    StructuredCompanyCandidateRanker,
    StructuredCompanyQueryPlanner,
)
from job_source_agent.candidate_reasoning_coordinator import CandidateReasoningCoordinator
from job_source_agent.candidate_reasoning_experiment import (
    EXPERIMENT_SCHEMA_VERSION,
    RecordingCandidateReasoningService,
    json_digest,
    load_public_cohort,
    sha256_file,
    write_json_atomic,
)
from job_source_agent.candidate_reasoning_search import ResolverCandidateSearchBackend
from job_source_agent.candidate_reasoning_service import (
    CandidateReasoningInvocationService,
    CandidateReasoningRuntime,
)
from job_source_agent.composition import AgentConfig, FetcherConfig, build_application
from job_source_agent.deepseek_reasoning_client import (
    DEEPSEEK_ADAPTER_VERSION,
    DEFAULT_DEEPSEEK_MODEL,
    DeepSeekReasoningClient,
)
from job_source_agent.llm_decision_bundle import (
    LLM_DECISION_MANIFEST_FILENAME,
    LLM_DECISIONS_FILENAME,
    AuditedLLMDecisionStore,
)
from job_source_agent.llm_experiment_budget import (
    BudgetedLLMReasoningClient,
    LLMExperimentBudgetConfig,
)
from job_source_agent.models import CompanyInput
from job_source_agent.run_configuration import DeterministicRunConfig
from scripts.replay_failure_bundle import replay_failure_bundle


BRANCH = "codex/llm-candidate-reasoning-foundation"
PROVIDER = "deepseek"
PROMPT_VERSION = "deepseek-company-candidates-v1"
MAX_CALLS = 36
HARD_COST_CAP_USD = Decimal("0.50")
INPUT_PRICE_USD_PER_MILLION = Decimal("0.14")
OUTPUT_PRICE_USD_PER_MILLION = Decimal("0.28")
MODEL_OUTPUT_LIMITS = {"query_plan": 1_000, "candidate_rank": 1_600}


def run_experiment(
    *,
    root: Path,
    cohort_path: Path,
    model: str = DEFAULT_DEEPSEEK_MODEL,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    commit = _require_frozen_branch(repo_root)
    cohort = load_public_cohort(cohort_path)
    _require_new_root(root)
    root.mkdir(parents=True, mode=0o700)
    shutil.copyfile(cohort_path, root / "cohort.json")

    baseline_config = _agent_config(llm=False)
    treatment_config = _agent_config(llm=True, model=model)
    common_config_digest = _common_config_digest(baseline_config)
    if common_config_digest != _common_config_digest(treatment_config):
        raise RuntimeError("baseline and treatment non-LLM configuration differ")
    baseline_run = DeterministicRunConfig.from_agent_config(baseline_config)
    treatment_run = DeterministicRunConfig.from_agent_config(treatment_config)
    execution_identity = json_digest(
        {
            "branch": BRANCH,
            "commit": commit,
            "cohort_sha256": sha256_file(root / "cohort.json"),
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "treatment_run_configuration_digest": treatment_run.digest,
        }
    )
    write_json_atomic(
        root / "capture-start.json",
        {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "status": "running",
            "branch": BRANCH,
            "git_commit": commit,
            "record_count": len(cohort),
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "execution_identity": execution_identity,
            "labels_loaded": False,
        },
    )

    baseline_results, baseline_traces = _run_arm(
        root=root / "baseline",
        cohort=cohort,
        agent_config=baseline_config,
        service_factory=None,
    )

    raw_client = DeepSeekReasoningClient(model=model, timeout_seconds=3.0)
    budget_client = BudgetedLLMReasoningClient(
        raw_client,
        LLMExperimentBudgetConfig(
            max_calls=MAX_CALLS,
            hard_cost_cap_usd=HARD_COST_CAP_USD,
            input_cache_miss_usd_per_million=INPUT_PRICE_USD_PER_MILLION,
            output_usd_per_million=OUTPUT_PRICE_USD_PER_MILLION,
            prompt_overhead_token_reserve=4_000,
            max_output_tokens_by_decision_kind=MODEL_OUTPUT_LIMITS,
        ),
    )
    decision_root = root / "treatment" / "decisions"
    decision_store = AuditedLLMDecisionStore(
        decision_root,
        execution_identity=execution_identity,
        run_configuration_digest=treatment_run.digest,
        llm_provider=PROVIDER,
        model_id=model,
        prompt_version=PROMPT_VERSION,
        adapter_version=DEEPSEEK_ADAPTER_VERSION,
    )
    recording_holder: dict[str, RecordingCandidateReasoningService] = {}

    def service_factory(resolver):
        coordinator = CandidateReasoningCoordinator(
            planner=StructuredCompanyQueryPlanner(budget_client),
            ranker=StructuredCompanyCandidateRanker(budget_client),
            search_backend=ResolverCandidateSearchBackend(resolver),
            decision_store=decision_store,
            clock=time.monotonic,
            max_candidates=treatment_run.llm_max_candidates,
            max_calls_per_company=treatment_run.llm_max_calls_per_company,
        )
        service = CandidateReasoningInvocationService(
            coordinator,
            CandidateReasoningRuntime(
                feature_enabled=True,
                llm_provider=PROVIDER,
                model_id=model,
                prompt_version=PROMPT_VERSION,
                timeout_seconds=treatment_run.llm_timeout,
                adapter_version=DEEPSEEK_ADAPTER_VERSION,
                execution_fingerprint=execution_identity,
            ),
            monotonic_clock=time.monotonic,
            wall_clock=time.time,
        )
        recording = RecordingCandidateReasoningService(service)
        recording_holder["service"] = recording
        return recording

    treatment_results, treatment_traces = _run_arm(
        root=root / "treatment",
        cohort=cohort,
        agent_config=treatment_config,
        service_factory=service_factory,
    )
    recording = recording_holder.get("service")
    if recording is None:
        raise RuntimeError("treatment candidate reasoning service was not constructed")
    write_json_atomic(
        root / "treatment" / "candidate-records.json",
        list(recording.records()),
    )
    budget = budget_client.snapshot()
    write_json_atomic(
        root / "treatment" / "budget.json",
        {
            **asdict(budget),
            "estimated_actual_cost_usd": str(budget.estimated_actual_cost_usd),
            "remaining_cost_cap_usd": str(budget.remaining_cost_cap_usd),
            "hard_cost_cap_usd": str(HARD_COST_CAP_USD),
        },
    )
    if budget.call_count > MAX_CALLS or budget.estimated_actual_cost_usd > HARD_COST_CAP_USD:
        raise RuntimeError("treatment exceeded the frozen call or cost budget")
    if not (decision_root / LLM_DECISIONS_FILENAME).is_file():
        raise RuntimeError("treatment produced no auditable LLM decisions")

    replay = replay_failure_bundle(
        SimpleNamespace(
            results=str(root / "treatment" / "trace.json"),
            snapshot_dir=str(root / "treatment" / "snapshots"),
            output_dir=str(root / "replay"),
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=None,
            include_missing_website=True,
            company_discovery_evidence_store=None,
            llm_decision_dir=str(decision_root),
            legacy_run_config=None,
        )
    )
    if replay.get("outcome_gate", {}).get("status") != "passed":
        raise RuntimeError("same-version replay did not reproduce every treatment outcome")
    replay_counts = replay["outcome_gate"]["classification_counts"]
    if replay_counts.get("reproduced") != len(cohort):
        raise RuntimeError("same-version replay did not reproduce all 18 records")

    _require_frozen_branch(repo_root, expected_commit=commit)
    files = _sealed_files(root)
    manifest = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "status": "sealed",
        "branch": BRANCH,
        "git_commit": commit,
        "record_count": len(cohort),
        "cohort_records_sha256": json_digest(list(cohort)),
        "model": model,
        "provider": PROVIDER,
        "prompt_version": PROMPT_VERSION,
        "adapter_version": DEEPSEEK_ADAPTER_VERSION,
        "execution_identity": execution_identity,
        "baseline_run_configuration": baseline_run.to_payload(),
        "baseline_run_configuration_digest": baseline_run.digest,
        "treatment_run_configuration": treatment_run.to_payload(),
        "treatment_run_configuration_digest": treatment_run.digest,
        "common_non_llm_configuration_digest": common_config_digest,
        "call_limit": MAX_CALLS,
        "hard_cost_cap_usd": str(HARD_COST_CAP_USD),
        "actual_call_count": budget.call_count,
        "actual_prompt_tokens": budget.prompt_tokens,
        "actual_completion_tokens": budget.completion_tokens,
        "actual_cost_usd": str(budget.estimated_actual_cost_usd),
        "replay_classification_counts": replay_counts,
        "labels_loaded_during_capture": False,
        "files": files,
    }
    write_json_atomic(root / "capture-manifest.json", manifest)
    return manifest


def _run_arm(
    *,
    root: Path,
    cohort: tuple[dict[str, Any], ...],
    agent_config: AgentConfig,
    service_factory,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root.mkdir(parents=True, mode=0o700)
    application = build_application(
        FetcherConfig(
            timeout=5.0,
            retries=1,
            retry_base_delay=0.25,
            snapshot_dir=root / "snapshots",
        ),
        agent_config,
        checkpoint_dir=root / "checkpoints",
        company_discovery_evidence_path=root / "company-evidence.json",
        candidate_reasoning_service_factory=service_factory,
    )
    results: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    for index, record in enumerate(cohort, start=1):
        company = CompanyInput(
            company_name=record["company_name"],
            linkedin_job_url=record.get("linkedin_job_url") or "",
            linkedin_company_url=record.get("linkedin_company_url"),
            job_title=record.get("job_title"),
            job_location=record.get("job_location"),
            source="fixed_input",
            source_trace={"experiment_record_id": record["record_id"]},
        )
        started = time.monotonic()
        result = application.pipeline.discover(company)
        result_record = result.result_record()
        trace_record = result.trace_record()
        result_record["experiment_record_id"] = record["record_id"]
        trace_record["experiment_record_id"] = record["record_id"]
        trace_record["experiment_elapsed_seconds"] = round(
            time.monotonic() - started, 3
        )
        results.append(result_record)
        traces.append(trace_record)
        write_json_atomic(root / "results.partial.json", results)
        write_json_atomic(root / "trace.partial.json", traces)
        print(
            f"[{index:02d}/{len(cohort):02d}] {record['record_id']} "
            f"{result.pipeline_status} website={bool(result.company_website_url)} "
            f"opening={bool(result.open_position_url)}",
            flush=True,
        )
    write_json_atomic(root / "results.json", results)
    write_json_atomic(root / "trace.json", traces)
    return results, traces


def _agent_config(*, llm: bool, model: str = "") -> AgentConfig:
    return AgentConfig(
        max_candidates=12,
        max_job_pages=8,
        max_job_board_attempts=3,
        max_career_candidate_fetches=12,
        max_career_discovery_transport_calls=32,
        max_career_search_queries=5,
        max_ats_board_fetches=5,
        enable_sitemap_discovery=True,
        enable_career_search=True,
        career_search_timeout=7.0,
        enable_parallel_candidate_discovery=True,
        evaluate_all_candidate_routes=False,
        enable_llm_candidate_reasoning=llm,
        llm_provider=PROVIDER if llm else "",
        llm_model=model if llm else "",
        llm_prompt_version=PROMPT_VERSION if llm else "",
        llm_timeout=15.0,
        llm_max_candidates=10,
        llm_max_calls_per_company=2,
    )


def _common_config_digest(config: AgentConfig) -> str:
    payload = asdict(config)
    for field in (
        "enable_llm_candidate_reasoning",
        "llm_provider",
        "llm_model",
        "llm_prompt_version",
        "llm_timeout",
        "llm_max_candidates",
        "llm_max_calls_per_company",
    ):
        payload.pop(field)
    return json_digest(payload)


def _require_new_root(root: Path) -> None:
    if root.exists():
        raise RuntimeError("experiment root already exists; use a fresh isolated directory")
    if root.is_symlink():
        raise RuntimeError("experiment root cannot be a symlink")


def _require_frozen_branch(repo_root: Path, expected_commit: str | None = None) -> str:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if branch != BRANCH:
        raise RuntimeError(f"experiment must run on {BRANCH}")
    if status:
        raise RuntimeError("experiment requires a clean frozen worktree")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError("experiment code changed during capture")
    return commit


def _sealed_files(root: Path) -> dict[str, str]:
    excluded = {"capture-manifest.json"}
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"experiment artifact cannot be a symlink: {path}")
        if not path.is_file() or path.name.startswith(".") or path.name in excluded:
            continue
        relative = path.relative_to(root).as_posix()
        if "/.locks/" in f"/{relative}/" or relative.endswith(".lock"):
            continue
        files[relative] = sha256_file(path)
    required = {
        "cohort.json",
        "baseline/results.json",
        "baseline/trace.json",
        "treatment/results.json",
        "treatment/trace.json",
        "treatment/candidate-records.json",
        f"treatment/decisions/{LLM_DECISIONS_FILENAME}",
        f"treatment/decisions/{LLM_DECISION_MANIFEST_FILENAME}",
        "replay/bundle-manifest.json",
    }
    if not required.issubset(files):
        raise RuntimeError("experiment artifact set is incomplete")
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--cohort",
        type=Path,
        default=Path("samples/evaluation/llm_candidate_reasoning_g_dev_v1.json"),
    )
    parser.add_argument("--model", default=DEFAULT_DEEPSEEK_MODEL)
    args = parser.parse_args()
    manifest = run_experiment(root=args.root, cohort_path=args.cohort, model=args.model)
    print(json.dumps({key: manifest[key] for key in ("status", "model", "actual_call_count", "actual_cost_usd")}, sort_keys=True))


if __name__ == "__main__":
    main()
