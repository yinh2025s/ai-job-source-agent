from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.company_discovery_evidence import VerifiedWebsiteEvidence
from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
    stored_website_deterministically_rejected,
)
from job_source_agent.candidate_route_evaluation import (
    summarize_candidate_route_metrics,
)
from job_source_agent.batch_checkpoint import FilesystemBatchCompletionStore
from job_source_agent.batch_discovery import LinkedInDiscoveryManifestStore
from job_source_agent.checkpoint import execution_fingerprint
from job_source_agent.checkpoint_prefix import (
    CheckpointPrefixInspection,
    inspect_checkpoint_prefix,
    inspect_complete_checkpoint_prefix,
)
from job_source_agent.completion_resume import (
    CompletionResumeDecision,
    classify_completion_resume,
    completion_resume_marker,
)
from job_source_agent.composition import AgentConfig, FetcherConfig, build_application, build_fetcher
from job_source_agent.contracts import FetchClient, PipelineContext
from job_source_agent.evaluation import compare_summaries, evaluate_expectations, summarize_results
from job_source_agent.evaluation_history import cohort_identities_compatible, derive_cohort_identity
from job_source_agent.evidence_scope import StageEvidenceLineage, new_capture_attempt_id
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.linkedin_discovery import (
    LinkedInJobsDiscoverer,
    LinkedInSearchQuery,
    enrich_public_external_apply_urls,
    linkedin_postings_to_company_inputs,
)
from job_source_agent.pipeline_application import discovery_result_from_context
from job_source_agent.models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_WEBSITE_RESOLUTION,
    PIPELINE_STAGES,
    CompanyInput,
    DiscoveryResult,
    dataclass_to_dict,
)
from job_source_agent.process_budget import ProcessBudgetExceeded, RemoteProcessError, run_with_process_budget
from job_source_agent.reasons import canonical_reason_code, make_stage_result
from job_source_agent.run_configuration import (
    BATCH_EXECUTION_SCHEMA_VERSION,
    BatchExecutionConfig,
    DeterministicRunConfig,
    combined_configuration_digest,
)
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.web import domain_of, normalize_url
from job_source_agent.website_resolver import CompanyWebsiteResolver
from scripts.replay_failure_bundle import replay_failure_bundle


_NON_RETRYABLE_WORKER_EXCEPTION_NAMES = frozenset(
    {
        "AssertionError",
        "AttributeError",
        "IndexError",
        "KeyError",
        "NotImplementedError",
        "TypeError",
        "ValueError",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a checkpointed live batch evaluation.")
    parser.add_argument("--input", help="Optional fixed company input JSON. If omitted, LinkedIn search is used.")
    parser.add_argument("--expectations", help="Optional expectations JSON keyed by company name.")
    parser.add_argument("--linkedin-keywords")
    parser.add_argument(
        "--linkedin-keyword",
        action="append",
        dest="linkedin_keyword_list",
        help="Repeat for a stratified multi-keyword LinkedIn benchmark cohort.",
    )
    parser.add_argument("--linkedin-location", default="United States")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--linkedin-pages", type=int, default=3)
    parser.add_argument(
        "--linkedin-per-query-limit",
        type=int,
        default=20,
        help="Maximum public search cards collected from each repeated keyword.",
    )
    parser.add_argument(
        "--preserve-job-postings",
        action="store_true",
        help="Retain distinct LinkedIn jobs from the same company in benchmark cohorts.",
    )
    parser.add_argument(
        "--enrich-public-external-apply",
        action="store_true",
        help="Fetch bounded public LinkedIn job details for visible External Apply evidence.",
    )
    parser.add_argument(
        "--require-full-cohort",
        action="store_true",
        help="Fail before downstream live work unless discovery freezes exactly --limit records.",
    )
    parser.add_argument("--fetch-timeout", type=float, default=3)
    parser.add_argument("--fixtures-dir", help="Optional fixture directory for deterministic batch checks.")
    parser.add_argument("--offline", action="store_true", help="Disable live network access.")
    parser.add_argument("--fetch-retries", type=int, default=0, help="Retries for retryable fetch failures.")
    parser.add_argument("--retry-base-delay", type=float, default=0.25, help="Initial delay between fetch retries.")
    parser.add_argument("--career-search-timeout", type=float, default=6)
    parser.add_argument("--max-career-search-queries", type=int, default=5)
    parser.add_argument("--verify-limit", type=int, default=3)
    parser.add_argument("--max-career-candidates", type=int, default=6)
    parser.add_argument("--max-career-fetches", type=int, default=5)
    parser.add_argument(
        "--max-career-transport-calls",
        type=int,
        default=32,
        help="Maximum underlying fetch dispatches during S4 career discovery.",
    )
    parser.add_argument("--max-ats-board-fetches", type=int, default=5)
    parser.add_argument("--max-job-pages", type=int, default=3)
    parser.add_argument("--max-job-board-attempts", type=int, default=3)
    candidate_discovery_group = parser.add_mutually_exclusive_group()
    candidate_discovery_group.add_argument(
        "--enable-parallel-candidate-discovery",
        dest="enable_parallel_candidate_discovery",
        action="store_true",
        default=True,
        help="Enable the merged S5 candidate portfolio (default).",
    )
    candidate_discovery_group.add_argument(
        "--disable-parallel-candidate-discovery",
        dest="enable_parallel_candidate_discovery",
        action="store_false",
        help="Use the legacy website-first S5 path for rollback or comparison.",
    )
    parser.add_argument(
        "--evaluate-all-candidate-routes",
        action="store_true",
        help=(
            "Run External Apply, provider search, and Website/Career candidate "
            "routes exhaustively and retain independent attribution trace."
        ),
    )
    parser.add_argument(
        "--route-evaluation-output",
        help="Optional JSON report for exhaustive candidate-route metrics.",
    )
    parser.add_argument(
        "--company-time-budget",
        type=float,
        default=45,
        help="Maximum wall-clock seconds spent on each company before checkpointing a structured timeout.",
    )
    parser.add_argument(
        "--website-time-budget",
        type=float,
        default=20,
        help="Maximum seconds allocated to S2/S3 before preserving the rest for career and ATS discovery.",
    )
    parser.add_argument("--skip-sitemap", action="store_true")
    parser.add_argument("--render-js", action="store_true", help="Use smart browser fallback for company pages.")
    parser.add_argument("--render-budget", type=int, default=2, help="Browser-rendered pages allowed per company.")
    parser.add_argument(
        "--render-screenshot",
        action="store_true",
        help="Capture screenshot artifacts for Playwright-rendered pages when snapshots are enabled.",
    )
    parser.add_argument("--output", default="/tmp/live-batch-results.json")
    parser.add_argument("--trace-output", default="/tmp/live-batch-trace.json")
    parser.add_argument("--summary-output", default="/tmp/live-batch-summary.json")
    parser.add_argument("--snapshot-dir", help="Optional directory for sanitized page snapshots.")
    parser.add_argument(
        "--failure-bundle-dir",
        help="Build an offline replay bundle for partial/failed/unsupported results after the batch.",
    )
    parser.add_argument(
        "--failure-bundle-limit",
        type=int,
        default=20,
        help="Maximum failure records included in the automatic replay bundle.",
    )
    parser.add_argument(
        "--replay-bundle-dir",
        help="Build an offline replay bundle for every pipeline outcome after the batch.",
    )
    parser.add_argument(
        "--replay-bundle-limit",
        type=int,
        default=50,
        help="Maximum records included in the full outcome replay bundle.",
    )
    parser.add_argument("--baseline-summary", help="Optional prior summary JSON used for regression deltas.")
    parser.add_argument("--workers", type=int, default=1, help="Number of companies to process concurrently.")
    stage_group = parser.add_mutually_exclusive_group()
    stage_group.add_argument(
        "--resume-from-stage",
        choices=PIPELINE_STAGES[1:-1],
        help="Reuse compatible stage checkpoints or replay evidence and continue from this stage.",
    )
    stage_group.add_argument(
        "--rerun-stage",
        choices=PIPELINE_STAGES[1:-1],
        help="Invalidate this stage and downstream checkpoints, then recompute them.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        help="Stage checkpoint directory; defaults beside the result output.",
    )
    parser.add_argument(
        "--company-discovery-evidence-store",
        help=(
            "Optional persistent store of previously verified company discovery "
            "candidates; every value is revalidated before use."
        ),
    )
    parser.add_argument(
        "--batch-checkpoint-dir",
        help="Atomic company-completion directory; defaults beside the result output.",
    )
    parser.add_argument(
        "--linkedin-manifest",
        help="Versioned dynamic LinkedIn cohort manifest; defaults inside the batch checkpoint directory.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Run every input again; dynamic LinkedIn mode also refreshes the frozen cohort.",
    )
    parser.add_argument(
        "--require-all-expectations",
        action="store_true",
        help="Fail expectation checks for companies that are not present in a partial live run.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    validate_artifact_args(args)
    if not args.checkpoint_dir:
        args.checkpoint_dir = str(Path(args.output).with_suffix(".checkpoints"))
    linkedin_fetcher = build_fetcher(FetcherConfig(timeout=max(args.fetch_timeout, 6)))
    companies = load_batch_companies(args, linkedin_fetcher)
    if bool(getattr(args, "require_full_cohort", False)) and len(companies) != args.limit:
        raise SystemExit(
            f"Cohort discovery produced {len(companies)} records; expected exactly {args.limit}."
        )
    args.cohort_companies_sha256 = _json_digest(
        [dataclass_to_dict(company) for company in companies]
    )
    _freeze_company_discovery_evidence_revisions(companies, args)

    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    summary_path = Path(args.summary_output)
    replay_evidence_snapshot_root, replay_evidence_store = (
        _prepare_automatic_replay_evidence_snapshot(args)
    )
    run_configuration = _run_configuration(args)
    batch_execution = _batch_execution_configuration(args)
    completion_store = FilesystemBatchCompletionStore(
        _batch_checkpoint_dir(args),
        run_configuration,
        combined_configuration_digest(run_configuration.digest, batch_execution.digest),
    )
    completed = _load_completed_companies(companies, completion_store, args)
    started = time.time()

    pending = [
        (index, company)
        for index, company in enumerate(companies, start=1)
        if index not in completed
    ]
    if completed:
        _write_completed_artifacts(
            completed,
            output_path,
            trace_path,
            summary_path,
            args,
            started,
        )
    print(
        f"cohort records: {len(companies)} "
        f"restored: {len(completed)} "
        f"retryable_resubmitted: "
        f"{getattr(args, 'batch_completion_resume_stats', {}).get('retryable_resubmit', 0)} "
        f"pending: {len(pending)}",
        flush=True,
    )
    if args.workers <= 1:
        for index, company in pending:
            index, result, elapsed = run_company_timed(index, company, args)
            _record_company_completion(
                index,
                len(companies),
                company,
                result,
                elapsed,
                completed,
                completion_store,
                output_path,
                trace_path,
                summary_path,
                args,
                started,
            )
    else:
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="live-company") as executor:
            futures = {
                executor.submit(run_company_timed, index, company, args): (index, company)
                for index, company in pending
            }
            for future in as_completed(futures):
                expected_index, company = futures[future]
                try:
                    index, result, elapsed = future.result()
                except Exception as error:
                    index = expected_index
                    result = _remote_worker_failure_result(
                        company,
                        error,
                        run_configuration=_run_configuration(args),
                    )
                    elapsed = 0.0
                _record_company_completion(
                    index,
                    len(companies),
                    company,
                    result,
                    elapsed,
                    completed,
                    completion_store,
                    output_path,
                    trace_path,
                    summary_path,
                    args,
                    started,
                )

    boundary_recapture = _recapture_retryable_missing_boundaries(
        companies,
        completed,
        completion_store,
        args,
    )
    if boundary_recapture["replaced"]:
        _write_completed_artifacts(
            completed,
            output_path,
            trace_path,
            summary_path,
            args,
            started,
        )

    results, traces = _ordered_records(completed)
    summary = build_summary(
        results,
        args,
        elapsed_sec=round(time.time() - started, 1),
        traces=traces,
    )
    if boundary_recapture["attempted"]:
        summary["automatic_boundary_recapture"] = boundary_recapture
    route_report_path = getattr(args, "route_evaluation_output", None)
    if route_report_path:
        route_report = summarize_candidate_route_metrics(traces)
        route_report["cohort_companies_sha256"] = getattr(
            args, "cohort_companies_sha256", None
        )
        route_report["run_configuration_digest"] = summary.get(
            "run_configuration_digest"
        )
        route_report["batch_execution_configuration_digest"] = summary.get(
            "batch_execution_configuration_digest"
        )
        _atomic_write_json(Path(route_report_path), route_report)
        summary["candidate_route_evaluation"] = {
            key: value
            for key, value in route_report.items()
            if key != "records"
        }
        summary["candidate_route_evaluation"]["report_path"] = str(
            Path(route_report_path)
        )
    if args.baseline_summary:
        baseline_summary = json.loads(Path(args.baseline_summary).read_text(encoding="utf-8"))
        if cohort_identities_compatible(
            derive_cohort_identity(summary),
            derive_cohort_identity(baseline_summary),
        ):
            summary["regression"] = compare_summaries(summary, baseline_summary)
        else:
            summary["regression"] = {"comparison_status": "no_compatible_baseline"}
    try:
        bundle_manifest = build_automatic_failure_bundle(
            args,
            trace_path,
            company_discovery_evidence_store=replay_evidence_store,
            company_discovery_evidence_source=getattr(
                args, "company_discovery_evidence_store", None
            ),
        )
        if bundle_manifest is not None:
            summary["failure_bundle"] = _replay_bundle_summary(
                bundle_manifest,
                Path(args.failure_bundle_dir) / "bundle-manifest.json",
            )
        replay_manifest = build_automatic_replay_bundle(
            args,
            trace_path,
            company_discovery_evidence_store=replay_evidence_store,
            company_discovery_evidence_source=getattr(
                args, "company_discovery_evidence_store", None
            ),
        )
        if replay_manifest is not None:
            summary["replay_bundle"] = _replay_bundle_summary(
                replay_manifest,
                Path(args.replay_bundle_dir) / "bundle-manifest.json",
            )
    finally:
        if replay_evidence_snapshot_root is not None:
            replay_evidence_snapshot_root.cleanup()
    _atomic_write_json(summary_path, summary)
    print_summary(summary)
    print(f"results: {output_path}", flush=True)
    print(f"trace: {trace_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    if route_report_path:
        print(f"candidate route evaluation: {route_report_path}", flush=True)
    expectation_checks = summary.get("expectation_checks", {})
    if expectation_checks.get("failed"):
        raise SystemExit("Live expectations failed; see the summary JSON for details.")
    enforce_bundle_gates(summary)


def enforce_bundle_gates(summary: dict) -> None:
    for bundle_name in ("failure_bundle", "replay_bundle"):
        gate_status = summary.get(bundle_name, {}).get("outcome_gate")
        if gate_status in {"failed", "incomplete"}:
            raise SystemExit(
                f"Live {bundle_name.replace('_', ' ')} gate {gate_status}; "
                "see the bundle manifest for details."
            )


def validate_artifact_args(args: argparse.Namespace) -> None:
    if bool(getattr(args, "evaluate_all_candidate_routes", False)) and not bool(
        getattr(args, "enable_parallel_candidate_discovery", False)
    ):
        raise SystemExit(
            "--evaluate-all-candidate-routes requires parallel candidate discovery."
        )
    if getattr(args, "route_evaluation_output", None) and not bool(
        getattr(args, "evaluate_all_candidate_routes", False)
    ):
        raise SystemExit(
            "--route-evaluation-output requires --evaluate-all-candidate-routes."
        )
    if (
        getattr(args, "failure_bundle_dir", None)
        or getattr(args, "replay_bundle_dir", None)
    ) and not getattr(args, "snapshot_dir", None):
        raise SystemExit("Replay bundle output requires --snapshot-dir.")
    if int(getattr(args, "failure_bundle_limit", 20)) <= 0:
        raise SystemExit("--failure-bundle-limit must be greater than zero.")
    if int(getattr(args, "replay_bundle_limit", 50)) <= 0:
        raise SystemExit("--replay-bundle-limit must be greater than zero.")


def build_automatic_failure_bundle(
    args: argparse.Namespace,
    trace_path: Path,
    *,
    company_discovery_evidence_store: str | None = None,
    company_discovery_evidence_source: str | None = None,
) -> dict | None:
    output_dir = getattr(args, "failure_bundle_dir", None)
    if not output_dir:
        return None
    replay_args = argparse.Namespace(
        results=str(trace_path),
        snapshot_dir=str(args.snapshot_dir),
        output_dir=str(output_dir),
        pipeline_status=["partial", "failed", "unsupported"],
        stage=None,
        stage_status=None,
        reason_code=None,
        provider=None,
        limit=int(args.failure_bundle_limit),
        include_missing_website=True,
        legacy_run_config=None,
        company_discovery_evidence_store=(
            company_discovery_evidence_store
            if company_discovery_evidence_store is not None
            else getattr(args, "company_discovery_evidence_store", None)
        ),
    )
    manifest = replay_failure_bundle(replay_args, allow_empty=True)
    return _restore_automatic_replay_evidence_provenance(
        manifest,
        Path(output_dir),
        company_discovery_evidence_source,
    )


def build_automatic_replay_bundle(
    args: argparse.Namespace,
    trace_path: Path,
    *,
    company_discovery_evidence_store: str | None = None,
    company_discovery_evidence_source: str | None = None,
) -> dict | None:
    output_dir = getattr(args, "replay_bundle_dir", None)
    if not output_dir:
        return None
    replay_args = argparse.Namespace(
        results=str(trace_path),
        snapshot_dir=str(args.snapshot_dir),
        output_dir=str(output_dir),
        pipeline_status=None,
        stage=None,
        stage_status=None,
        reason_code=None,
        provider=None,
        limit=int(args.replay_bundle_limit),
        include_missing_website=True,
        legacy_run_config=None,
        company_discovery_evidence_store=(
            company_discovery_evidence_store
            if company_discovery_evidence_store is not None
            else getattr(args, "company_discovery_evidence_store", None)
        ),
    )
    manifest = replay_failure_bundle(replay_args, allow_empty=True)
    return _restore_automatic_replay_evidence_provenance(
        manifest,
        Path(output_dir),
        company_discovery_evidence_source,
    )


def _restore_automatic_replay_evidence_provenance(
    manifest: dict,
    output_dir: Path,
    source_path: str | None,
) -> dict:
    """Keep bundle provenance tied to the configured store, not its private snapshot."""

    evidence = manifest.get("company_discovery_evidence")
    if not source_path or not isinstance(evidence, dict):
        return manifest
    evidence["source_path_sha256"] = hashlib.sha256(
        str(Path(source_path).expanduser().resolve()).encode("utf-8")
    ).hexdigest()
    _atomic_write_json(output_dir / "bundle-manifest.json", manifest)
    return manifest


def _replay_bundle_summary(manifest: dict, manifest_path: Path) -> dict:
    replayed = int(manifest.get("summary", {}).get("total", 0))
    integrity_counts = manifest.get("record_integrity", {}).get("counts", {})
    selected = int(integrity_counts.get("selected_count", replayed))
    exported = int(integrity_counts.get("exported_count", selected))
    return {
        "status": manifest["status"],
        "reason": manifest.get("reason"),
        "filter_matched": int(
            integrity_counts.get("filter_matched_count", selected)
        ),
        "selected": selected,
        "exported": exported,
        "replayed": int(integrity_counts.get("result_count", replayed)),
        "manifest": str(manifest_path),
        "outcome_gate": manifest.get("outcome_gate", {}).get("status"),
    }


def load_batch_companies(args: argparse.Namespace, linkedin_fetcher: FetchClient) -> list[CompanyInput]:
    if args.input:
        return load_company_inputs(args.input)[: args.limit]
    keyword_values = tuple(
        value.strip()
        for value in (
            getattr(args, "linkedin_keyword_list", None)
            or ([args.linkedin_keywords] if args.linkedin_keywords else [])
        )
        if isinstance(value, str) and value.strip()
    )
    if not keyword_values:
        raise SystemExit(
            "Provide either --input, --linkedin-keywords, or repeated --linkedin-keyword."
        )
    request = {
        "keywords": list(keyword_values),
        "location": args.linkedin_location,
        "limit": args.limit,
        "pages": args.linkedin_pages,
        "per_query_limit": int(
            getattr(args, "linkedin_per_query_limit", args.limit)
        ),
        "preserve_job_postings": bool(
            getattr(args, "preserve_job_postings", False)
        ),
        "enrich_public_external_apply": bool(
            getattr(args, "enrich_public_external_apply", False)
        ),
    }
    manifest_path = _linkedin_manifest_path(args)
    store = LinkedInDiscoveryManifestStore(manifest_path)

    def discover() -> list[dict]:
        discoverer = LinkedInJobsDiscoverer(linkedin_fetcher)
        if bool(getattr(args, "preserve_job_postings", False)):
            postings = discoverer.collect_benchmark_cohort(
                (
                    LinkedInSearchQuery(
                        keywords=keywords,
                        location=args.linkedin_location,
                    )
                    for keywords in keyword_values
                ),
                cohort_size=args.limit,
                per_query_limit=int(
                    getattr(args, "linkedin_per_query_limit", args.limit)
                ),
                pages=args.linkedin_pages,
            )
        else:
            postings = discoverer.search(
                keywords=keyword_values[0],
                location=args.linkedin_location,
                limit=args.limit,
                pages=args.linkedin_pages,
            )
        if bool(getattr(args, "enrich_public_external_apply", False)):
            postings = enrich_public_external_apply_urls(
                postings,
                linkedin_fetcher,
                max_detail_fetches=args.limit,
            )
        companies = linkedin_postings_to_company_inputs(
            postings,
            preserve_job_postings=bool(
                getattr(args, "preserve_job_postings", False)
            ),
        )[: args.limit]
        return [dataclass_to_dict(company) for company in companies]

    records, action = store.resolve(
        request,
        discover,
        refresh=bool(getattr(args, "no_resume", False)),
    )
    args.linkedin_manifest_action = action
    args.linkedin_manifest_resolved_path = str(manifest_path)
    return [CompanyInput(**record) for record in records]


def build_summary(
    results: list[dict],
    args: argparse.Namespace,
    elapsed_sec: float,
    traces: list[dict] | None = None,
) -> dict:
    summary_records = traces if traces is not None else results
    summary = summarize_results(summary_records, elapsed_sec=elapsed_sec)
    run_configuration = _run_configuration(args)
    batch_execution = _batch_execution_configuration(args)
    summary["run_configuration"] = run_configuration.to_payload()
    summary["run_configuration_digest"] = run_configuration.digest
    summary["batch_execution_configuration"] = batch_execution.to_payload()
    summary["batch_execution_configuration_digest"] = batch_execution.digest
    evaluation_manifest = {}
    evaluation_manifest["run_configuration_digest"] = run_configuration.digest
    evaluation_manifest["batch_execution_configuration_digest"] = batch_execution.digest
    companies_sha256 = getattr(args, "cohort_companies_sha256", None)
    if companies_sha256:
        evaluation_manifest["companies_sha256"] = companies_sha256
    manifest_action = getattr(args, "linkedin_manifest_action", None)
    if manifest_action:
        summary["linkedin_discovery_manifest"] = {
            "action": manifest_action,
            "path": getattr(args, "linkedin_manifest_resolved_path", None),
        }
    completion_resume_stats = getattr(args, "batch_completion_resume_stats", None)
    if completion_resume_stats is not None:
        summary["batch_completion_resume"] = dict(completion_resume_stats)
    if args.expectations:
        expectations = json.loads(Path(args.expectations).read_text(encoding="utf-8"))
        if not getattr(args, "require_all_expectations", False):
            present_companies = {str(result.get("company_name")) for result in results}
            expectations = {
                company_name: expectation
                for company_name, expectation in expectations.items()
                if company_name in present_companies
            }
        summary["expectation_checks"] = evaluate_expectations(results, expectations)
        evaluation_manifest["expectations_sha256"] = _json_digest(expectations)
    if evaluation_manifest:
        summary["evaluation_manifest"] = evaluation_manifest
    return summary


def _json_digest(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_company_timed(
    index: int,
    company: CompanyInput,
    args: argparse.Namespace,
) -> tuple[int, DiscoveryResult, float]:
    item_started = time.time()
    result = run_company(company, args)
    elapsed = round(time.time() - item_started, 1)
    return index, result, elapsed


def _load_completed_companies(
    companies: list[CompanyInput],
    store: FilesystemBatchCompletionStore,
    args: argparse.Namespace,
) -> dict[int, tuple[dict, dict, float]]:
    stats = {
        "compatible_completions": 0,
        "completion_restore": 0,
        "non_retryable_restore": 0,
        "unclassified_restore": 0,
        "retryable_resubmit": 0,
    }
    args.batch_completion_resume_stats = stats
    args.batch_completion_resume_decisions = {}
    args.batch_completion_retry_stages = {}
    if getattr(args, "no_resume", False) or getattr(args, "rerun_stage", None):
        return {}
    input_records = [dataclass_to_dict(company) for company in companies]
    saved = store.scan(input_records)
    stats["compatible_completions"] = len(saved)
    completed: dict[int, tuple[dict, dict, float]] = {}
    stage_store = FilesystemCheckpointStore(_checkpoint_dir(args))
    run_configuration = _run_configuration(args)
    for index, input_record in enumerate(input_records, start=1):
        completion = saved.get(store.fingerprint(input_record))
        if completion is None:
            continue
        decision = classify_completion_resume(completion.result, completion.trace)
        if (
            decision.action == "retryable_resubmit"
            and _has_identity_verified_final_output(completion.result, completion.trace)
        ):
            decision = CompletionResumeDecision(
                "completion_restore",
                "identity_verified_final_output",
            )
        marker = completion_resume_marker(decision)
        args.batch_completion_resume_decisions[index] = marker
        stats[decision.action] += 1
        if decision.action == "retryable_resubmit":
            fingerprint = execution_fingerprint(input_record, run_configuration.digest)
            retry_stage = decision.retry_stage or PIPELINE_STAGES[0]
            stage_store.invalidate_from(fingerprint, retry_stage)
            args.batch_completion_retry_stages[fingerprint] = retry_stage
            continue
        restored_trace = copy.deepcopy(completion.trace)
        trace_payload = restored_trace.get("trace")
        if not isinstance(trace_payload, dict):
            trace_payload = {}
            restored_trace["trace"] = trace_payload
        trace_payload["batch_completion_resume"] = marker
        completed[index] = (completion.result, restored_trace, completion.elapsed)
    return completed


def _has_identity_verified_final_output(result: dict, trace: dict) -> bool:
    """Return whether persisted final evidence makes an upstream retry unnecessary."""

    identity_assertion = result.get("identity_assertion")
    if (
        not isinstance(identity_assertion, dict)
        or identity_assertion.get("verdict") != "verified"
    ):
        return False
    if not any(
        isinstance(result.get(field), str) and bool(result[field].strip())
        for field in ("job_list_page_url", "open_position_url")
    ):
        return False
    stages = trace.get("stages")
    if not isinstance(stages, list):
        stages = result.get("stages")
    if not isinstance(stages, list) or len(stages) != len(PIPELINE_STAGES):
        return False
    final_stage = stages[-1]
    return (
        isinstance(final_stage, dict)
        and final_stage.get("stage") == STAGE_RESULT_VALIDATION
        and final_stage.get("status") == "success"
    )


def _recapture_retryable_missing_boundaries(
    companies: list[CompanyInput],
    completed: dict[int, tuple[dict, dict, float]],
    store: FilesystemBatchCompletionStore,
    args: argparse.Namespace,
) -> dict[str, int]:
    """Run at most one real targeted recapture for each replay-blocked retryable result."""

    stats = {
        "eligible": 0,
        "attempted": 0,
        "replaced": 0,
        "boundary_still_missing": 0,
        "execution_failed": 0,
    }
    if not (
        getattr(args, "failure_bundle_dir", None)
        or getattr(args, "replay_bundle_dir", None)
    ):
        return stats

    stage_store = FilesystemCheckpointStore(_checkpoint_dir(args))
    run_configuration = _run_configuration(args)
    original_retry_stages = getattr(args, "batch_completion_retry_stages", None)
    try:
        for index, company in enumerate(companies, start=1):
            completion = completed.get(index)
            if completion is None:
                continue
            result_record, trace_record, elapsed = completion
            decision = classify_completion_resume(result_record, trace_record)
            retry_stage = decision.retry_stage
            if (
                decision.action != "retryable_resubmit"
                or retry_stage not in PIPELINE_STAGES
                or _has_finalized_capture_boundary(trace_record, retry_stage)
            ):
                continue

            stats["eligible"] += 1
            stats["attempted"] += 1
            recapture_company = copy.deepcopy(company)
            fingerprint = execution_fingerprint(
                dataclass_to_dict(recapture_company),
                run_configuration.digest,
            )
            stage_store.invalidate_from(fingerprint, retry_stage)
            args.batch_completion_retry_stages = {fingerprint: retry_stage}
            recapture_started = time.monotonic()
            try:
                recaptured = run_company(recapture_company, args)
            except Exception:
                stats["execution_failed"] += 1
                continue
            recapture_elapsed = round(time.monotonic() - recapture_started, 1)
            recaptured_result = recaptured.result_record()
            recaptured_trace = dataclass_to_dict(recaptured.trace_record())
            if not _has_finalized_capture_boundary(recaptured_trace, retry_stage):
                stats["boundary_still_missing"] += 1
                continue

            total_elapsed = round(elapsed + recapture_elapsed, 1)
            store.save(
                dataclass_to_dict(company),
                recaptured_result,
                recaptured_trace,
                total_elapsed,
            )
            completed[index] = (
                recaptured_result,
                recaptured_trace,
                total_elapsed,
            )
            stats["replaced"] += 1
    finally:
        args.batch_completion_retry_stages = original_retry_stages
    return stats


def _has_finalized_capture_boundary(trace_record: dict, stage: str) -> bool:
    lineage = trace_record.get("stage_evidence_lineage")
    if lineage is None:
        trace = trace_record.get("trace")
        lineage = trace.get("stage_evidence_lineage") if isinstance(trace, dict) else None
    if not isinstance(lineage, list):
        return False
    for payload in lineage:
        try:
            restored = StageEvidenceLineage.from_payload(payload)
        except (TypeError, ValueError):
            continue
        if restored.stage == stage:
            return restored.snapshot_scope is not None
    return False


def _record_company_completion(
    index: int,
    total: int,
    company: CompanyInput,
    result: DiscoveryResult,
    elapsed: float,
    completed: dict[int, tuple[dict, dict, float]],
    store: FilesystemBatchCompletionStore,
    output_path: Path,
    trace_path: Path,
    summary_path: Path,
    args: argparse.Namespace,
    started: float,
) -> None:
    result_record = result.result_record()
    trace_record = dataclass_to_dict(result.trace_record())
    resume_marker = getattr(args, "batch_completion_resume_decisions", {}).get(index)
    if resume_marker is not None:
        trace_payload = trace_record.get("trace")
        if not isinstance(trace_payload, dict):
            trace_payload = {}
            trace_record["trace"] = trace_payload
        trace_payload["batch_completion_resume"] = resume_marker
    store.save(dataclass_to_dict(company), result_record, trace_record, elapsed)
    completed[index] = (result_record, trace_record, elapsed)
    _write_completed_artifacts(
        completed,
        output_path,
        trace_path,
        summary_path,
        args,
        started,
    )
    print(
        f"[{index:02d}/{total:02d}] "
        f"{result.status.upper()} {result.company_name} "
        f"career={bool(result.career_page_url)} "
        f"job_list={bool(result.job_list_page_url)} "
        f"opening={bool(result.open_position_url)} "
        f"error={result.error} "
        f"elapsed={elapsed}s",
        flush=True,
    )


def _ordered_records(
    completed: dict[int, tuple[dict, dict, float]],
) -> tuple[list[dict], list[dict]]:
    ordered = [completed[index] for index in sorted(completed)]
    return [item[0] for item in ordered], [item[1] for item in ordered]


def _write_completed_artifacts(
    completed: dict[int, tuple[dict, dict, float]],
    output_path: Path,
    trace_path: Path,
    summary_path: Path,
    args: argparse.Namespace,
    started: float,
) -> None:
    results, traces = _ordered_records(completed)
    _atomic_write_json(output_path, results)
    _atomic_write_json(trace_path, traces)
    summary = build_summary(
        results,
        args,
        elapsed_sec=round(time.time() - started, 1),
        traces=traces,
    )
    _atomic_write_json(summary_path, summary)


def record_checkpoint(
    index: int,
    total: int,
    result: DiscoveryResult,
    elapsed: float,
    results: list[dict],
    traces: list[dict],
    output_path: Path,
    trace_path: Path,
    summary_path: Path,
    args: argparse.Namespace,
    started: float,
) -> None:
    results.append(result.result_record())
    traces.append(dataclass_to_dict(result.trace_record()))
    _atomic_write_json(output_path, results)
    _atomic_write_json(trace_path, traces)
    summary = build_summary(
        results,
        args,
        elapsed_sec=round(time.time() - started, 1),
        traces=traces,
    )
    _atomic_write_json(summary_path, summary)
    print(
        f"[{index:02d}/{total:02d}] "
        f"{result.status.upper()} {result.company_name} "
        f"career={bool(result.career_page_url)} "
        f"job_list={bool(result.job_list_page_url)} "
        f"opening={bool(result.open_position_url)} "
        f"error={result.error} "
        f"elapsed={elapsed}s",
        flush=True,
    )


def _atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale_path in path.parent.glob(f".{path.name}.*.tmp"):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _prepare_automatic_replay_evidence_snapshot(
    args: argparse.Namespace,
) -> tuple[tempfile.TemporaryDirectory[str] | None, str | None]:
    """Freeze the configured public evidence store before live workers can update it."""

    source_value = getattr(args, "company_discovery_evidence_store", None)
    if not source_value or not (
        getattr(args, "failure_bundle_dir", None)
        or getattr(args, "replay_bundle_dir", None)
    ):
        return None, None

    output_parent = Path(getattr(args, "output", "/tmp/live-batch-results.json")).parent
    output_parent.mkdir(parents=True, exist_ok=True)
    snapshot_root = tempfile.TemporaryDirectory(
        prefix=".live-batch-replay-evidence-",
        dir=output_parent,
    )
    snapshot_path = Path(snapshot_root.name) / "company-discovery-evidence.json"
    source_path = Path(source_value)
    try:
        if source_path.is_symlink() or (
            source_path.exists() and not source_path.is_file()
        ):
            # A directory is deliberately unreadable to the replay freezer too.
            snapshot_path.mkdir()
        elif source_path.exists():
            _atomic_copy_file(source_path, snapshot_path)
    except OSError:
        # Preserve fail-closed behavior for a configured but unreadable store.
        snapshot_path.mkdir(exist_ok=True)
    return snapshot_root, str(snapshot_path)


def _atomic_copy_file(source_path: Path, destination_path: Path) -> None:
    payload = source_path.read_bytes()
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def run_company(company: CompanyInput, args: argparse.Namespace):
    rerun_failure = _explicit_rerun_preflight(company, args)
    if rerun_failure is not None:
        return rerun_failure

    started = time.monotonic()
    upstream_result: DiscoveryResult | None = None
    capture_attempt_id = new_capture_attempt_id()
    automatic_retry_start = _automatic_retry_start_stage(company, args)
    explicit_resume_start = _explicit_resume_start_stage(company, args)
    explicit_rerun_start = getattr(args, "rerun_stage", None)
    selected_start = automatic_retry_start or explicit_resume_start or explicit_rerun_start
    if selected_start not in {
        STAGE_CAREER_DISCOVERY,
        STAGE_JOB_BOARD_DISCOVERY,
        STAGE_OPENING_MATCH,
        STAGE_RESULT_VALIDATION,
    }:
        upstream_budget = min(args.website_time_budget, args.company_time_budget)
        upstream_deadline = time.monotonic() + _inner_deadline_budget(upstream_budget)
        upstream_start = (
            selected_start
            if selected_start in {
                STAGE_LINKEDIN_DISCOVERY,
                STAGE_WEBSITE_RESOLUTION,
                STAGE_HIRING_IDENTITY_RESOLUTION,
            }
            else None
        )
        try:
            upstream_result = run_with_process_budget(
                run_pipeline_phase,
                (
                    company,
                    args,
                    upstream_start,
                    STAGE_HIRING_IDENTITY_RESOLUTION,
                    None if automatic_retry_start else _upstream_rerun_stage(args),
                    upstream_deadline,
                    capture_attempt_id,
                ),
                timeout=upstream_budget,
            )
        except ProcessBudgetExceeded:
            recovered = _recover_checkpoint_prefix(company, args)
            if recovered is not None and len(recovered.stage_results) == len(PIPELINE_STAGES):
                return recovered
            return failure_result(
                company,
                error="company_time_budget_exhausted",
                detail=f"Website/identity resolution exceeded its {min(args.website_time_budget, args.company_time_budget):g}-second stage budget.",
                completed_result=recovered,
                run_configuration=_run_configuration(args),
            )
        except RemoteProcessError as exc:
            recovered = _recover_checkpoint_prefix(company, args)
            return _remote_worker_failure_result(
                company,
                exc,
                completed_result=recovered,
                run_configuration=_run_configuration(args),
            )
        if (
            not upstream_result.company_website_url
            and not company.external_apply_url
            and not bool(getattr(args, "enable_parallel_candidate_discovery", False))
        ):
            return upstream_result

    if selected_start in {
        STAGE_CAREER_DISCOVERY,
        STAGE_JOB_BOARD_DISCOVERY,
        STAGE_OPENING_MATCH,
        STAGE_RESULT_VALIDATION,
    }:
        downstream_start = selected_start
    else:
        downstream_start, _ = _downstream_start_stage(company, args)

    remaining = args.company_time_budget - (time.monotonic() - started)
    if remaining <= 0:
        return failure_result(
            company,
            error="company_time_budget_exhausted",
            detail=f"Exceeded the {args.company_time_budget:g}-second company budget after website resolution.",
            completed_result=upstream_result,
            run_configuration=_run_configuration(args),
        )
    if _split_opening_phase(downstream_start, remaining):
        reserved_opening_budget = _opening_phase_reserve(remaining)
        company.source_trace.setdefault("phase_budget", {}).update(
            {
                "remaining_before_job_board_sec": round(remaining, 3),
                "reserved_opening_sec": round(reserved_opening_budget, 3),
            }
        )
        discovery_budget = max(0.001, remaining - reserved_opening_budget)
        try:
            discovery_deadline = time.monotonic() + _phase_deadline_budget(
                discovery_budget
            )
            discovery_result = run_with_process_budget(
                run_pipeline_phase,
                (
                    company,
                    args,
                    downstream_start,
                    STAGE_JOB_BOARD_DISCOVERY,
                    None if automatic_retry_start else _downstream_rerun_stage(args),
                    discovery_deadline,
                    capture_attempt_id,
                    True,
                ),
                timeout=discovery_budget,
            )
        except ProcessBudgetExceeded:
            discovery_result = _recover_checkpoint_prefix(company, args)
            if (
                discovery_result is None
                or discovery_result.stage_status(STAGE_JOB_BOARD_DISCOVERY) != "success"
                or not discovery_result.job_list_page_url
            ):
                return failure_result(
                    company,
                    error="company_time_budget_exhausted",
                    detail=(
                        "Career and Job List discovery exhausted its bounded phase "
                        "budget before a verified board was published."
                    ),
                    completed_result=discovery_result or upstream_result,
                    run_configuration=_run_configuration(args),
                )
        except RemoteProcessError as exc:
            discovery_result = _recover_checkpoint_prefix(company, args)
            if (
                discovery_result is None
                or discovery_result.stage_status(STAGE_JOB_BOARD_DISCOVERY) != "success"
                or not discovery_result.job_list_page_url
            ):
                return _remote_worker_failure_result(
                    company,
                    exc,
                    completed_result=discovery_result or upstream_result,
                    run_configuration=_run_configuration(args),
                )

        if (
            discovery_result.stage_status(STAGE_JOB_BOARD_DISCOVERY) != "success"
            or not discovery_result.job_list_page_url
        ):
            return discovery_result
        upstream_result = discovery_result
        downstream_start = STAGE_OPENING_MATCH
        remaining = args.company_time_budget - (time.monotonic() - started)
        minimum_opening_budget = _minimum_opening_execution_budget(
            reserved_opening_budget
        )
        if remaining < minimum_opening_budget:
            return failure_result(
                company,
                error="company_time_budget_exhausted",
                detail=(
                    "Verified Job List discovery completed, but process cleanup eroded "
                    f"the reserved opening-search window to {max(0.0, remaining):.1f}s "
                    f"(< {minimum_opening_budget:.1f}s). Resume from opening_match."
                ),
                completed_result=discovery_result,
                run_configuration=_run_configuration(args),
            )

    try:
        downstream_deadline = time.monotonic() + _inner_deadline_budget(remaining)
        if downstream_start == STAGE_OPENING_MATCH:
            downstream_deadline = time.monotonic() + _phase_deadline_budget(remaining)
        return run_with_process_budget(
            run_pipeline_phase,
            (
                company,
                args,
                downstream_start,
                None,
                None if automatic_retry_start else _downstream_rerun_stage(args),
                downstream_deadline,
                capture_attempt_id,
                True,
            ),
            timeout=remaining,
        )
    except ProcessBudgetExceeded:
        recovered = _recover_checkpoint_prefix(company, args)
        if recovered is not None and len(recovered.stage_results) == len(PIPELINE_STAGES):
            return recovered
        return failure_result(
            company,
            error="company_time_budget_exhausted",
            detail=f"Downstream pipeline exceeded the remaining {remaining:.1f}-second company budget.",
            completed_result=recovered or upstream_result,
            run_configuration=_run_configuration(args),
        )
    except RemoteProcessError as exc:
        recovered = _recover_checkpoint_prefix(company, args)
        return _remote_worker_failure_result(
            company,
            exc,
            completed_result=recovered or upstream_result,
            run_configuration=_run_configuration(args),
        )


def _remote_worker_failure_result(
    company: CompanyInput,
    error: BaseException,
    *,
    completed_result: DiscoveryResult | None = None,
    run_configuration: DeterministicRunConfig | None = None,
) -> DiscoveryResult:
    detail = str(error)
    exception_name = _terminal_worker_exception_name(error)
    if exception_name in _NON_RETRYABLE_WORKER_EXCEPTION_NAMES:
        return failure_result(
            company,
            error="batch_worker_contract_failed",
            reason_code="INVALID_STRUCTURED_DATA",
            detail=detail,
            completed_result=completed_result,
            run_configuration=run_configuration,
        )
    return failure_result(
        company,
        error="batch_worker_failed",
        detail=detail,
        completed_result=completed_result,
        run_configuration=run_configuration,
    )


def _terminal_worker_exception_name(error: BaseException) -> str | None:
    if not isinstance(error, RemoteProcessError):
        return type(error).__name__
    for line in reversed(str(error).splitlines()):
        match = re.fullmatch(
            r"(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)(?::.*)?",
            line.strip(),
        )
        if match:
            return match.group(1)
    return None


def _automatic_retry_start_stage(
    company: CompanyInput,
    args: argparse.Namespace,
) -> str | None:
    retry_stages = getattr(args, "batch_completion_retry_stages", None)
    if not isinstance(retry_stages, dict) or not retry_stages:
        return None
    fingerprint = execution_fingerprint(
        dataclass_to_dict(company),
        _run_configuration(args).digest,
    )
    requested = retry_stages.get(fingerprint)
    if requested not in PIPELINE_STAGES:
        return None

    inspection = _inspect_company_checkpoint_prefix(company, args, requested)
    effective = inspection.effective_start
    company.source_trace["batch_completion_retry"] = {
        "requested_start_stage": requested,
        "effective_start_stage": effective,
        "checkpoint_chain": "complete" if not inspection.defects else "incomplete",
        "missing_checkpoints": [defect.stage for defect in inspection.defects],
        **inspection.trace_record(mode="automatic_retry"),
    }
    return effective


def _explicit_resume_start_stage(
    company: CompanyInput,
    args: argparse.Namespace,
) -> str | None:
    requested = getattr(args, "resume_from_stage", None)
    if requested not in PIPELINE_STAGES:
        return None
    inspection = _inspect_company_checkpoint_prefix(company, args, requested)
    _record_resume_inspection(company, inspection)
    return inspection.effective_start


def _explicit_rerun_preflight(
    company: CompanyInput,
    args: argparse.Namespace,
) -> DiscoveryResult | None:
    requested = getattr(args, "rerun_stage", None)
    if requested not in PIPELINE_STAGES:
        return None
    inspection = _inspect_company_checkpoint_prefix(company, args, requested)
    if not inspection.defects:
        return None
    company.source_trace["checkpoint_prefix"] = inspection.trace_record(mode="rerun")
    defect_summary = ", ".join(
        f"{defect.stage}:{defect.defect_class}" for defect in inspection.defects
    )
    return failure_result(
        company,
        error="checkpoint_prefix_invalid",
        detail=(
            f"Cannot rerun from {requested}: checkpoint prefix is not reusable "
            f"({defect_summary}); rebuild from {inspection.effective_start}."
        ),
        run_configuration=_run_configuration(args),
    )


def _upstream_rerun_stage(args: argparse.Namespace) -> str | None:
    rerun_stage = getattr(args, "rerun_stage", None)
    if rerun_stage in {
        STAGE_LINKEDIN_DISCOVERY,
        STAGE_WEBSITE_RESOLUTION,
        STAGE_HIRING_IDENTITY_RESOLUTION,
    }:
        return rerun_stage
    return None


def _downstream_start_stage(
    company: CompanyInput,
    args: argparse.Namespace,
) -> tuple[str, str | None]:
    requested = getattr(args, "resume_from_stage", None)
    if requested not in {STAGE_JOB_BOARD_DISCOVERY, STAGE_OPENING_MATCH}:
        return STAGE_CAREER_DISCOVERY, None

    inspection = _inspect_company_checkpoint_prefix(company, args, requested)
    _record_resume_inspection(company, inspection)
    if not inspection.defects:
        return requested, None

    company.source_trace["resume"]["fallback"] = "rebuild_from_earliest_checkpoint_gap"
    return inspection.effective_start, "rebuild_from_checkpoint_gap"


def _inspect_company_checkpoint_prefix(
    company: CompanyInput,
    args: argparse.Namespace,
    requested: str,
) -> CheckpointPrefixInspection:
    run_configuration = _run_configuration(args)
    fingerprint = execution_fingerprint(
        dataclass_to_dict(company),
        run_configuration.digest,
    )
    store = FilesystemCheckpointStore(_checkpoint_dir(args))
    return inspect_checkpoint_prefix(
        store,
        fingerprint,
        PipelineContext.from_company(company),
        requested,
    )


def _record_resume_inspection(
    company: CompanyInput,
    inspection: CheckpointPrefixInspection,
) -> None:
    company.source_trace.setdefault("resume", {}).update(
        {
            "resume_from_stage": inspection.requested_start,
            "requested_start_stage": inspection.requested_start,
            "effective_start_stage": inspection.effective_start,
            "checkpoint_chain": "complete" if not inspection.defects else "incomplete",
            "missing_checkpoints": [defect.stage for defect in inspection.defects],
            "used_replay_upstream": False,
            "skipped_stages": [],
            **inspection.trace_record(mode="resume"),
        }
    )


def _downstream_rerun_stage(args: argparse.Namespace) -> str | None:
    rerun_stage = getattr(args, "rerun_stage", None)
    if rerun_stage in {
        STAGE_CAREER_DISCOVERY,
        STAGE_JOB_BOARD_DISCOVERY,
        STAGE_OPENING_MATCH,
        STAGE_RESULT_VALIDATION,
    }:
        return rerun_stage
    return None


def prepare_company(company: CompanyInput, args: argparse.Namespace) -> CompanyInput:
    fetcher = build_company_fetcher(args)
    identity_resolver = CompanyIdentityResolver()
    website_resolver = CompanyWebsiteResolver(fetcher, verify_limit=args.verify_limit)
    company_evidence_store = _company_discovery_evidence_store(args)
    stored_website = None
    if (
        company_evidence_store is not None
        and company.linkedin_company_url
        and not company.company_website_url
    ):
        try:
            stored_record = company_evidence_store.load(
                company.company_name,
                company.linkedin_company_url,
            )
        except (OSError, TypeError, ValueError):
            stored_record = None
        if stored_record is not None and stored_record.website is not None:
            stored_website = stored_record.website.url

    identity_started = time.perf_counter()
    identity, identity_trace = identity_resolver.resolve(
        company.company_name,
        company.company_website_url or None,
        company.linkedin_company_url,
    )
    company.source_trace["identity_resolution"] = identity_trace
    company.source_trace.setdefault("stage_metrics", {})[
        "hiring_identity_resolution_duration_ms"
    ] = round((time.perf_counter() - identity_started) * 1000)
    website_started = time.perf_counter()
    website_was_verified = False
    if identity:
        company.hiring_entity_name = identity.hiring_entity_name
        company.career_root_url = identity.career_root_url
        if identity.official_website_url:
            company.company_website_url = identity.official_website_url
            website_was_verified = True
        company.source_trace["website_resolution"] = {
            "selected": {
                "url": company.company_website_url,
                "reason": "provided by company identity resolver",
            }
        }
    elif company.company_website_url:
        company.company_website_url = normalize_url(company.company_website_url)
        company.source_trace["website_resolution"] = {
            "selected": {
                "url": company.company_website_url,
                "reason": "provided by input record",
            }
        }
    else:
        website_url, website_trace = website_resolver.resolve(
            company.company_name,
            company.linkedin_company_url,
            stored_candidate_url=stored_website,
        )
        company.company_website_url = website_url or ""
        website_was_verified = bool(website_url)
        company.source_trace["website_resolution"] = website_trace
        if stored_website and not website_url:
            _invalidate_rejected_stored_website(
                company_evidence_store,
                company,
                stored_website,
                website_trace,
            )
    if (
        company_evidence_store is not None
        and company.linkedin_company_url
        and company.company_website_url
        and website_was_verified
    ):
        try:
            company_evidence_store.save(
                company.company_name,
                company.linkedin_company_url,
                website=VerifiedWebsiteEvidence(
                    url=company.company_website_url,
                    source="verified_resolver",
                    evidence_url=company.company_website_url,
                    observed_at=time.time(),
                ),
            )
        except (OSError, TypeError, ValueError):
            pass
    company.source_trace["stage_metrics"]["website_resolution_duration_ms"] = round(
        (time.perf_counter() - website_started) * 1000
    )
    retry_events = getattr(fetcher, "retry_events", None)
    if retry_events:
        company.source_trace["retry_events"] = retry_events
    return company


def discover_prepared_company(company: CompanyInput, args: argparse.Namespace) -> DiscoveryResult:
    return run_pipeline_phase(company, args, STAGE_CAREER_DISCOVERY, None, None)


def run_pipeline_phase(
    company: CompanyInput,
    args: argparse.Namespace,
    start_at: str | None,
    stop_after: str | None,
    rerun_from: str | None,
    retry_deadline: float | None = None,
    capture_attempt_id: str | None = None,
    same_attempt_continuation: bool = False,
) -> DiscoveryResult:
    application = build_application(
        _company_fetcher_config(args, retry_deadline=retry_deadline),
        _agent_config(args),
        checkpoint_dir=_checkpoint_dir(args),
        company_discovery_evidence_path=getattr(
            args,
            "company_discovery_evidence_store",
            None,
        ),
    )
    result = application.pipeline.discover(
        company,
        start_at=start_at,
        stop_after=stop_after,
        rerun_from=rerun_from,
        capture_attempt_id=capture_attempt_id,
        same_attempt_continuation=same_attempt_continuation,
    )
    fetcher = application.fetcher
    retry_events = getattr(fetcher, "retry_events", None)
    if retry_events:
        result.trace["retry_events"] = retry_events
    render_events = getattr(fetcher, "render_events", None)
    if render_events:
        result.trace["render_events"] = render_events
    return result


def _agent_config(args: argparse.Namespace) -> AgentConfig:
    career_search_timeout = getattr(args, "career_search_timeout", 6)
    return AgentConfig(
        max_candidates=int(getattr(args, "max_career_candidates", 6)),
        max_job_pages=int(getattr(args, "max_job_pages", 3)),
        max_job_board_attempts=int(getattr(args, "max_job_board_attempts", 3)),
        max_career_candidate_fetches=int(getattr(args, "max_career_fetches", 5)),
        max_career_discovery_transport_calls=(
            None
            if getattr(args, "max_career_transport_calls", None) is None
            else int(getattr(args, "max_career_transport_calls"))
        ),
        max_career_search_queries=int(getattr(args, "max_career_search_queries", 5)),
        max_ats_board_fetches=int(getattr(args, "max_ats_board_fetches", 5)),
        enable_sitemap_discovery=not bool(getattr(args, "skip_sitemap", False)),
        enable_career_search=True,
        enable_parallel_candidate_discovery=bool(
            getattr(args, "enable_parallel_candidate_discovery", False)
        ),
        evaluate_all_candidate_routes=bool(
            getattr(args, "evaluate_all_candidate_routes", False)
        ),
        career_search_timeout=(
            None if career_search_timeout is None else float(career_search_timeout)
        ),
    )


def _run_configuration(args: argparse.Namespace) -> DeterministicRunConfig:
    return DeterministicRunConfig.from_agent_config(_agent_config(args))


def _batch_execution_configuration(args: argparse.Namespace) -> BatchExecutionConfig:
    company_time_budget = float(getattr(args, "company_time_budget", 45))
    website_time_budget = min(
        float(getattr(args, "website_time_budget", 20)),
        company_time_budget,
    )
    return BatchExecutionConfig.from_payload(
        {
            "schema_version": BATCH_EXECUTION_SCHEMA_VERSION,
            "batch": {
                "company_time_budget": company_time_budget,
                "website_time_budget": website_time_budget,
                "fetch_timeout": float(getattr(args, "fetch_timeout", 3)),
                "fetch_retries": int(getattr(args, "fetch_retries", 0)),
                "retry_base_delay": float(getattr(args, "retry_base_delay", 0.25)),
                "render_mode": "smart" if bool(getattr(args, "render_js", False)) else "none",
                "render_budget": int(getattr(args, "render_budget", 2)),
                "verify_limit": int(getattr(args, "verify_limit", 3)),
                "offline": bool(getattr(args, "offline", False)),
                "opening_phase_policy": "reserved_after_verified_job_list_v1",
            },
        }
    )


def build_company_fetcher(args: argparse.Namespace):
    return build_fetcher(_company_fetcher_config(args))


def _inner_deadline_budget(outer_budget: float) -> float:
    """Leave time to finalize stage results and publish checkpoints before hard kill."""

    # Browser teardown, snapshot finalization, and fsync can take several seconds
    # after the last bounded fetch returns. Keep a real publication window so a
    # cooperative timeout is persisted instead of being replaced by the parent
    # process hard deadline.
    reserve = min(30.0, max(0.05, outer_budget * 0.50))
    return max(0.001, outer_budget - reserve)


def _opening_phase_reserve(remaining_budget: float) -> float:
    """Reserve a useful S6/S7 window after verified Job List discovery."""

    return min(25.0, max(12.0, remaining_budget * 0.40))


def _minimum_opening_execution_budget(reserved_budget: float) -> float:
    """Fail retryably when parent/child cleanup consumed most of the S6 reserve."""

    return min(10.0, max(5.0, reserved_budget * 0.50))


def _phase_deadline_budget(outer_budget: float) -> float:
    """Use a small publication tail for an already stage-bounded child process."""

    reserve = min(3.0, max(0.05, outer_budget * 0.10))
    return max(0.001, outer_budget - reserve)


def _split_opening_phase(start_at: str, remaining_budget: float) -> bool:
    if remaining_budget < 24.0:
        return False
    return PIPELINE_STAGES.index(start_at) <= PIPELINE_STAGES.index(
        STAGE_JOB_BOARD_DISCOVERY
    )


def _company_fetcher_config(
    args: argparse.Namespace,
    *,
    retry_deadline: float | None = None,
) -> FetcherConfig:
    return FetcherConfig(
        fixtures_dir=getattr(args, "fixtures_dir", None),
        offline=bool(getattr(args, "offline", False)),
        timeout=args.fetch_timeout,
        render_mode="smart" if args.render_js else "none",
        render_budget=args.render_budget,
        capture_screenshot=bool(getattr(args, "render_screenshot", False)),
        retries=int(getattr(args, "fetch_retries", 0) or 0),
        retry_base_delay=float(getattr(args, "retry_base_delay", 0.25) or 0),
        retry_deadline=retry_deadline,
        snapshot_dir=getattr(args, "snapshot_dir", None),
    )


def _checkpoint_dir(args: argparse.Namespace) -> str:
    configured = getattr(args, "checkpoint_dir", None)
    if configured:
        return str(configured)
    output = getattr(args, "output", "/tmp/live-batch-results.json")
    return str(Path(output).with_suffix(".checkpoints"))


def _company_discovery_evidence_store(
    args: argparse.Namespace,
) -> FilesystemCompanyDiscoveryEvidenceStore | None:
    path = getattr(args, "company_discovery_evidence_store", None)
    return FilesystemCompanyDiscoveryEvidenceStore(path) if path else None


def _freeze_company_discovery_evidence_revisions(
    companies: list[CompanyInput],
    args: argparse.Namespace,
) -> None:
    """Bind checkpoints to the public evidence state visible at batch start."""

    store = _company_discovery_evidence_store(args)
    if store is None:
        return
    for company in companies:
        record = None
        if company.linkedin_company_url:
            try:
                record = store.load(company.company_name, company.linkedin_company_url)
            except (OSError, TypeError, ValueError):
                record = None
        public_state = (
            dataclass_to_dict(record)
            if record is not None
            else {"status": "absent"}
        )
        company.source_trace = {
            **company.source_trace,
            "company_discovery_evidence_revision": _json_digest(public_state),
        }


def _invalidate_rejected_stored_website(
    store: FilesystemCompanyDiscoveryEvidenceStore | None,
    company: CompanyInput,
    stored_website: str,
    trace: dict,
) -> None:
    if store is None or not company.linkedin_company_url:
        return
    if not stored_website_deterministically_rejected(trace, stored_website):
        return
    try:
        store.invalidate(
            company.company_name,
            company.linkedin_company_url,
            layer="website",
            evidence_url=stored_website,
        )
    except (OSError, TypeError, ValueError):
        return


def _batch_checkpoint_dir(args: argparse.Namespace) -> str:
    configured = getattr(args, "batch_checkpoint_dir", None)
    if configured:
        return str(configured)
    output = getattr(args, "output", "/tmp/live-batch-results.json")
    return str(Path(output).with_suffix(".batch-completions"))


def _linkedin_manifest_path(args: argparse.Namespace) -> Path:
    configured = getattr(args, "linkedin_manifest", None)
    if configured:
        return Path(configured)
    return Path(_batch_checkpoint_dir(args)) / "linkedin-discovery.json"


def _recover_checkpoint_prefix(
    company: CompanyInput,
    args: argparse.Namespace,
) -> DiscoveryResult | None:
    settings = _run_configuration(args)
    fingerprint = execution_fingerprint(dataclass_to_dict(company), settings.digest)
    store = FilesystemCheckpointStore(_checkpoint_dir(args))
    context = PipelineContext.from_company(company)
    inspection = inspect_complete_checkpoint_prefix(store, fingerprint, context)
    for execution in inspection.executions:
        context.apply(execution)
        stage = execution.result.stage
        context.trace.setdefault("checkpoint_events", []).append(
            {
                "action": "parent_timeout_restore",
                "stage": stage,
                "execution_fingerprint": fingerprint,
            }
        )
    if not context.stage_results:
        return None
    return discovery_result_from_context(
        context,
        run_configuration=settings,
        execution_fingerprint_value=fingerprint,
    )


def _completed_stage_prefix(completed_result: DiscoveryResult | None) -> list:
    if completed_result is None:
        return []
    prefix = []
    for expected, stage in zip(
        PIPELINE_STAGES[:-1],
        completed_result.stage_results,
    ):
        if stage.stage != expected or stage.status not in {"success", "not_applicable"}:
            break
        prefix.append(stage)
    return prefix


def _timeout_result_trace(
    completed_result: DiscoveryResult | None,
    completed_stages: set[str],
    failure_stage: str,
    error: str,
    detail: str | None,
) -> dict:
    trace = dict(completed_result.trace) if completed_result is not None else {}
    existing_stage_traces = trace.get("stages", {})
    stage_traces = {
        stage: stage_trace
        for stage, stage_trace in (
            existing_stage_traces.items()
            if isinstance(existing_stage_traces, dict)
            else ()
        )
        if stage in completed_stages
    }
    stage_traces[failure_stage] = {
        "batch_error": error,
        "batch_error_detail": detail,
    }
    if failure_stage == STAGE_RESULT_VALIDATION:
        stage_traces[failure_stage]["pipeline_status"] = "failed"
    else:
        stage_traces[STAGE_RESULT_VALIDATION] = {
            "pipeline_status": "failed",
            "issues": [],
            "source": "parent_timeout_recovery",
        }
    trace["stages"] = stage_traces
    return trace


def failure_result(
    company: CompanyInput,
    error: str,
    detail: str | None = None,
    completed_result: DiscoveryResult | None = None,
    run_configuration: DeterministicRunConfig | None = None,
    reason_code: str | None = None,
) -> DiscoveryResult:
    error_code = canonical_reason_code(reason_code or error)
    stage_metrics = company.source_trace.get("stage_metrics", {})
    has_linkedin_input = bool(company.linkedin_job_url or company.linkedin_company_url)
    completed_prefix = _completed_stage_prefix(completed_result)
    completed_stages = {stage.stage for stage in completed_prefix}
    website_url = (
        completed_result.company_website_url
        if completed_result is not None and STAGE_WEBSITE_RESOLUTION in completed_stages
        else company.company_website_url
    ) or ""
    website_resolved = bool(website_url) and error_code != "WEBSITE_NOT_RESOLVED"
    stages = completed_prefix or [
        make_stage_result(
            STAGE_LINKEDIN_DISCOVERY,
            "success" if has_linkedin_input else "not_applicable",
            input_count=1 if has_linkedin_input else 0,
            output_count=1 if has_linkedin_input else 0,
        )
    ]
    if not completed_prefix and website_resolved:
        stages.extend(
            [
                make_stage_result(
                    STAGE_WEBSITE_RESOLUTION,
                    "success",
                    duration_ms=int(stage_metrics.get("website_resolution_duration_ms") or 0),
                    input_count=1,
                    output_count=1,
                    evidence=[{"field": "company_website_url", "url": website_url}],
                ),
                make_stage_result(
                    STAGE_HIRING_IDENTITY_RESOLUTION,
                    "success",
                    duration_ms=int(
                        stage_metrics.get("hiring_identity_resolution_duration_ms") or 0
                    ),
                    input_count=1,
                    output_count=1,
                    detail="Batch execution stopped after a complete identity result.",
                ),
            ]
        )

    failure_stage_index = len(stages)
    failure_stage = PIPELINE_STAGES[failure_stage_index]
    stages.append(
        make_stage_result(
            failure_stage,
            "failed",
            reason_code=error_code,
            input_count=1,
            detail=detail,
        )
    )
    if failure_stage != STAGE_RESULT_VALIDATION:
        for stage in PIPELINE_STAGES[failure_stage_index + 1 : -1]:
            stages.append(
                make_stage_result(
                    stage,
                    "not_run",
                    detail="A required upstream stage did not succeed.",
                )
            )
        stages.append(
            make_stage_result(
                STAGE_RESULT_VALIDATION,
                "success",
                input_count=1,
                output_count=1,
                evidence=[{"field": "pipeline_status", "value": "failed"}],
            )
        )
    settings = run_configuration or DeterministicRunConfig.from_agent_config(AgentConfig())
    fingerprint = execution_fingerprint(dataclass_to_dict(company), settings.digest)
    return DiscoveryResult(
        company_name=company.company_name,
        company_website_url=website_url,
        hiring_entity_name=(
            completed_result.hiring_entity_name
            if completed_result is not None
            and STAGE_HIRING_IDENTITY_RESOLUTION in completed_stages
            else company.hiring_entity_name
        ),
        career_root_url=(
            completed_result.career_root_url
            if completed_result is not None
            and STAGE_HIRING_IDENTITY_RESOLUTION in completed_stages
            else company.career_root_url
        ),
        linkedin_job_url=company.linkedin_job_url,
        external_apply_url=company.external_apply_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        career_page_url=(
            completed_result.career_page_url
            if completed_result is not None and STAGE_CAREER_DISCOVERY in completed_stages
            else None
        ),
        job_list_page_url=(
            completed_result.job_list_page_url
            if completed_result is not None and STAGE_JOB_BOARD_DISCOVERY in completed_stages
            else None
        ),
        open_position_url=(
            completed_result.open_position_url
            if completed_result is not None and STAGE_OPENING_MATCH in completed_stages
            else None
        ),
        status="failed",
        error=error,
        error_code=error_code,
        pipeline_status="failed",
        stage_results=stages,
        run_configuration=settings.to_payload(),
        run_configuration_digest=settings.digest,
        execution_fingerprint=fingerprint,
        trace={
            **_timeout_result_trace(
                completed_result,
                completed_stages,
                failure_stage,
                error,
                detail,
            ),
            "source": company.source,
            "source_trace": {
                **(
                    completed_result.trace.get("source_trace", {})
                    if completed_result is not None
                    and isinstance(completed_result.trace.get("source_trace"), dict)
                    else {}
                ),
                **company.source_trace,
            },
            "batch_error": error,
            "batch_error_detail": detail,
            "run_configuration_digest": settings.digest,
            "execution_fingerprint": fingerprint,
        },
    )


def print_summary(summary: dict) -> None:
    print("summary:", flush=True)
    print(f"  total: {summary['total']}", flush=True)
    print(f"  success: {summary['success']}", flush=True)
    print(f"  pipeline_statuses: {summary['pipeline_status_counts']}", flush=True)
    print(f"  with_job_list: {summary['with_job_list']}", flush=True)
    print(f"  with_opening: {summary['with_opening']}", flush=True)
    print(f"  elapsed_sec: {summary.get('elapsed_sec')}", flush=True)
    print(f"  rates: {summary['rates']}", flush=True)
    print(f"  errors: {summary['error_counts']}", flush=True)
    print(f"  reason_codes: {summary['reason_code_counts']}", flush=True)
    print(f"  providers: {summary['provider_counts']}", flush=True)
    regression = summary.get("regression")
    if regression and "rates_delta" in regression:
        print(f"  rate_delta: {regression['rates_delta']}", flush=True)
    elif regression and regression.get("comparison_status"):
        print(f"  baseline_comparison: {regression['comparison_status']}", flush=True)
    expectation_checks = summary.get("expectation_checks")
    if expectation_checks:
        print(
            f"  expectations: {expectation_checks['passed']}/{expectation_checks['total']} passed",
            flush=True,
        )
    replay_bundle = summary.get("replay_bundle")
    if replay_bundle:
        print(
            "  replay_bundle: "
            f"filter_matched={replay_bundle['filter_matched']} "
            f"selected={replay_bundle['selected']} "
            f"exported={replay_bundle['exported']} "
            f"replayed={replay_bundle['replayed']} "
            f"status={replay_bundle['status']} "
            f"reason={replay_bundle.get('reason')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
