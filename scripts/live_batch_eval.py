from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.batch_checkpoint import FilesystemBatchCompletionStore
from job_source_agent.batch_discovery import LinkedInDiscoveryManifestStore
from job_source_agent.checkpoint import input_fingerprint
from job_source_agent.composition import AgentConfig, FetcherConfig, build_application, build_fetcher
from job_source_agent.contracts import FetchClient
from job_source_agent.evaluation import compare_summaries, evaluate_expectations, summarize_results
from job_source_agent.evaluation_history import cohort_identities_compatible, derive_cohort_identity
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.linkedin_discovery import (
    LinkedInJobsDiscoverer,
    linkedin_postings_to_company_inputs,
)
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
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.web import normalize_url
from job_source_agent.website_resolver import CompanyWebsiteResolver
from scripts.replay_failure_bundle import replay_failure_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a checkpointed live batch evaluation.")
    parser.add_argument("--input", help="Optional fixed company input JSON. If omitted, LinkedIn search is used.")
    parser.add_argument("--expectations", help="Optional expectations JSON keyed by company name.")
    parser.add_argument("--linkedin-keywords")
    parser.add_argument("--linkedin-location", default="United States")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--linkedin-pages", type=int, default=3)
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
    parser.add_argument("--max-ats-board-fetches", type=int, default=5)
    parser.add_argument("--max-job-pages", type=int, default=3)
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
    args.cohort_companies_sha256 = _json_digest(
        [dataclass_to_dict(company) for company in companies]
    )

    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    summary_path = Path(args.summary_output)
    completion_store = FilesystemBatchCompletionStore(_batch_checkpoint_dir(args))
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
        f"unique companies: {len(companies)} "
        f"restored: {len(completed)} pending: {len(pending)}",
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
                    result = failure_result(company, "batch_worker_failed", detail=repr(error))
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

    results, traces = _ordered_records(completed)
    summary = build_summary(
        results,
        args,
        elapsed_sec=round(time.time() - started, 1),
        traces=traces,
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
    bundle_manifest = build_automatic_failure_bundle(args, trace_path)
    if bundle_manifest is not None:
        summary["failure_bundle"] = {
            "status": bundle_manifest["status"],
            "reason": bundle_manifest.get("reason"),
            "selected": int(bundle_manifest.get("summary", {}).get("total", 0)),
            "manifest": str(Path(args.failure_bundle_dir) / "bundle-manifest.json"),
        }
    _atomic_write_json(summary_path, summary)
    print_summary(summary)
    print(f"results: {output_path}", flush=True)
    print(f"trace: {trace_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    expectation_checks = summary.get("expectation_checks", {})
    if expectation_checks.get("failed"):
        raise SystemExit("Live expectations failed; see the summary JSON for details.")


def validate_artifact_args(args: argparse.Namespace) -> None:
    if getattr(args, "failure_bundle_dir", None) and not getattr(args, "snapshot_dir", None):
        raise SystemExit("--failure-bundle-dir requires --snapshot-dir.")
    if int(getattr(args, "failure_bundle_limit", 20)) <= 0:
        raise SystemExit("--failure-bundle-limit must be greater than zero.")


def build_automatic_failure_bundle(args: argparse.Namespace, trace_path: Path) -> dict | None:
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
    )
    return replay_failure_bundle(replay_args, allow_empty=True)


def load_batch_companies(args: argparse.Namespace, linkedin_fetcher: FetchClient) -> list[CompanyInput]:
    if args.input:
        return load_company_inputs(args.input)[: args.limit]
    if not args.linkedin_keywords:
        raise SystemExit("Provide either --input or --linkedin-keywords.")
    request = {
        "keywords": args.linkedin_keywords,
        "location": args.linkedin_location,
        "limit": args.limit,
        "pages": args.linkedin_pages,
    }
    manifest_path = _linkedin_manifest_path(args)
    store = LinkedInDiscoveryManifestStore(manifest_path)

    def discover() -> list[dict]:
        postings = LinkedInJobsDiscoverer(linkedin_fetcher).search(
            keywords=args.linkedin_keywords,
            location=args.linkedin_location,
            limit=args.limit,
            pages=args.linkedin_pages,
        )
        companies = linkedin_postings_to_company_inputs(postings)[: args.limit]
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
    evaluation_manifest = {}
    companies_sha256 = getattr(args, "cohort_companies_sha256", None)
    if companies_sha256:
        evaluation_manifest["companies_sha256"] = companies_sha256
    manifest_action = getattr(args, "linkedin_manifest_action", None)
    if manifest_action:
        summary["linkedin_discovery_manifest"] = {
            "action": manifest_action,
            "path": getattr(args, "linkedin_manifest_resolved_path", None),
        }
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


def run_company_timed(index: int, company: CompanyInput, args: argparse.Namespace) -> tuple[int, DiscoveryResult, float]:
    item_started = time.time()
    result = run_company(company, args)
    elapsed = round(time.time() - item_started, 1)
    return index, result, elapsed


def _load_completed_companies(
    companies: list[CompanyInput],
    store: FilesystemBatchCompletionStore,
    args: argparse.Namespace,
) -> dict[int, tuple[dict, dict, float]]:
    if getattr(args, "no_resume", False) or getattr(args, "rerun_stage", None):
        return {}
    input_records = [dataclass_to_dict(company) for company in companies]
    saved = store.scan(input_records)
    completed: dict[int, tuple[dict, dict, float]] = {}
    for index, input_record in enumerate(input_records, start=1):
        completion = saved.get(input_fingerprint(input_record))
        if completion is not None:
            completed[index] = (completion.result, completion.trace, completion.elapsed)
    return completed


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


def run_company(company: CompanyInput, args: argparse.Namespace):
    started = time.monotonic()
    upstream_result: DiscoveryResult | None = None
    if resume_uses_replay_upstream(args):
        prepare_replay_company_for_resume(company, args)
    else:
        upstream_budget = min(args.website_time_budget, args.company_time_budget)
        upstream_deadline = time.monotonic() + _inner_deadline_budget(upstream_budget)
        try:
            upstream_result = run_with_process_budget(
                run_pipeline_phase,
                (
                    company,
                    args,
                    None,
                    STAGE_HIRING_IDENTITY_RESOLUTION,
                    _upstream_rerun_stage(args),
                    upstream_deadline,
                ),
                timeout=upstream_budget,
            )
        except ProcessBudgetExceeded:
            return failure_result(
                company,
                error="company_time_budget_exhausted",
                detail=f"Website/identity resolution exceeded its {min(args.website_time_budget, args.company_time_budget):g}-second stage budget.",
            )
        except RemoteProcessError as exc:
            return failure_result(company, error="batch_worker_failed", detail=str(exc))
        if not upstream_result.company_website_url and not company.external_apply_url:
            return upstream_result

    downstream_start, resume_fallback = _downstream_start_stage(company, args)
    if resume_fallback == "missing_resume_evidence":
        return failure_result(
            company,
            error="website_not_resolved",
            detail=(
                f"--resume-from-stage {args.resume_from_stage} requires a complete, "
                "compatible upstream checkpoint chain, replay company_website_url, or "
                "a supported external_apply_url."
            ),
        )

    remaining = args.company_time_budget - (time.monotonic() - started)
    if remaining <= 0:
        return failure_result(
            company,
            error="company_time_budget_exhausted",
            detail=f"Exceeded the {args.company_time_budget:g}-second company budget after website resolution.",
            completed_result=upstream_result,
        )
    try:
        downstream_deadline = time.monotonic() + _inner_deadline_budget(remaining)
        return run_with_process_budget(
            run_pipeline_phase,
            (
                company,
                args,
                downstream_start,
                None,
                _downstream_rerun_stage(args),
                downstream_deadline,
            ),
            timeout=remaining,
        )
    except ProcessBudgetExceeded:
        return failure_result(
            company,
            error="company_time_budget_exhausted",
            detail=f"Career discovery exceeded the remaining {remaining:.1f}-second company budget.",
            completed_result=upstream_result,
        )
    except RemoteProcessError as exc:
        return failure_result(
            company,
            error="batch_worker_failed",
            detail=str(exc),
            completed_result=upstream_result,
        )


def _upstream_rerun_stage(args: argparse.Namespace) -> str | None:
    rerun_stage = getattr(args, "rerun_stage", None)
    if rerun_stage in {
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

    missing = _missing_resume_checkpoints(company, args, requested)
    if not missing:
        company.source_trace.setdefault("resume", {}).update(
            {
                "effective_start_stage": requested,
                "checkpoint_chain": "complete",
                "used_replay_upstream": False,
                "skipped_stages": [],
            }
        )
        return requested, None

    resume_trace = company.source_trace.setdefault("resume", {})
    resume_trace.update(
        {
            "effective_start_stage": STAGE_CAREER_DISCOVERY,
            "checkpoint_chain": "incomplete",
            "missing_checkpoints": missing,
            "used_replay_upstream": True,
        }
    )
    if company.company_website_url or company.external_apply_url:
        resume_trace["fallback"] = "rebuild_downstream_from_career_discovery"
        return STAGE_CAREER_DISCOVERY, "rebuild_downstream"
    resume_trace["fallback"] = "missing_resume_evidence"
    return STAGE_CAREER_DISCOVERY, "missing_resume_evidence"


def _missing_resume_checkpoints(
    company: CompanyInput,
    args: argparse.Namespace,
    requested: str,
) -> list[str]:
    fingerprint = input_fingerprint(dataclass_to_dict(company))
    store = FilesystemCheckpointStore(_checkpoint_dir(args))
    requested_index = PIPELINE_STAGES.index(requested)
    missing: list[str] = []
    for stage in PIPELINE_STAGES[:requested_index]:
        execution = store.load(fingerprint, stage)
        if execution is None or execution.result.status not in {"success", "not_applicable"}:
            missing.append(stage)
    return missing


def _downstream_rerun_stage(args: argparse.Namespace) -> str | None:
    rerun_stage = getattr(args, "rerun_stage", None)
    if rerun_stage in {
        STAGE_CAREER_DISCOVERY,
        STAGE_JOB_BOARD_DISCOVERY,
        STAGE_OPENING_MATCH,
    }:
        return rerun_stage
    return None


def resume_uses_replay_upstream(args: argparse.Namespace) -> bool:
    return getattr(args, "resume_from_stage", None) in {
        STAGE_CAREER_DISCOVERY,
        STAGE_JOB_BOARD_DISCOVERY,
        STAGE_OPENING_MATCH,
    }


def prepare_replay_company_for_resume(company: CompanyInput, args: argparse.Namespace) -> CompanyInput:
    if company.company_website_url:
        company.company_website_url = normalize_url(company.company_website_url)
    if company.career_root_url:
        company.career_root_url = normalize_url(company.career_root_url)
    company.source_trace.setdefault("resume", {})
    company.source_trace["resume"].update(
        {
            "resume_from_stage": args.resume_from_stage,
            "used_replay_upstream": True,
            "skipped_stages": [
                STAGE_WEBSITE_RESOLUTION,
                STAGE_HIRING_IDENTITY_RESOLUTION,
            ],
        }
    )
    company.source_trace.setdefault("stage_metrics", {})
    company.source_trace["website_resolution"] = {
        "selected": {
            "url": company.company_website_url,
            "reason": f"reused from replay input for --resume-from-stage {args.resume_from_stage}",
        }
    }
    return company


def prepare_company(company: CompanyInput, args: argparse.Namespace) -> CompanyInput:
    fetcher = build_company_fetcher(args)
    identity_resolver = CompanyIdentityResolver()
    website_resolver = CompanyWebsiteResolver(fetcher, verify_limit=args.verify_limit)

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
    if identity:
        company.hiring_entity_name = identity.hiring_entity_name
        company.career_root_url = identity.career_root_url
        if identity.official_website_url:
            company.company_website_url = identity.official_website_url
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
        )
        company.company_website_url = website_url or ""
        company.source_trace["website_resolution"] = website_trace
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
) -> DiscoveryResult:
    application = build_application(
        _company_fetcher_config(args, retry_deadline=retry_deadline),
        AgentConfig(
            max_candidates=args.max_career_candidates,
            max_job_pages=args.max_job_pages,
            max_career_candidate_fetches=args.max_career_fetches,
            max_career_search_queries=args.max_career_search_queries,
            max_ats_board_fetches=args.max_ats_board_fetches,
            enable_sitemap_discovery=not args.skip_sitemap,
            career_search_timeout=args.career_search_timeout,
        ),
        checkpoint_dir=_checkpoint_dir(args),
    )
    result = application.pipeline.discover(
        company,
        start_at=start_at,
        stop_after=stop_after,
        rerun_from=rerun_from,
    )
    fetcher = application.fetcher
    retry_events = getattr(fetcher, "retry_events", None)
    if retry_events:
        result.trace["retry_events"] = retry_events
    render_events = getattr(fetcher, "render_events", None)
    if render_events:
        result.trace["render_events"] = render_events
    return result


def build_company_fetcher(args: argparse.Namespace):
    return build_fetcher(_company_fetcher_config(args))


def _inner_deadline_budget(outer_budget: float) -> float:
    """Leave time to finalize stage results and publish checkpoints before hard kill."""

    reserve = min(1.0, max(0.05, outer_budget * 0.05))
    return max(0.001, outer_budget - reserve)


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


def failure_result(
    company: CompanyInput,
    error: str,
    detail: str | None = None,
    completed_result: DiscoveryResult | None = None,
) -> DiscoveryResult:
    error_code = canonical_reason_code(error)
    stage_metrics = company.source_trace.get("stage_metrics", {})
    has_linkedin_input = bool(company.linkedin_job_url or company.linkedin_company_url)
    website_url = (
        completed_result.company_website_url
        if completed_result is not None
        else company.company_website_url
    ) or ""
    website_resolved = bool(website_url) and error_code != "WEBSITE_NOT_RESOLVED"
    completed_prefix = (
        [
            stage
            for stage in completed_result.stage_results
            if stage.stage
            in {
                STAGE_LINKEDIN_DISCOVERY,
                STAGE_WEBSITE_RESOLUTION,
                STAGE_HIRING_IDENTITY_RESOLUTION,
            }
        ]
        if completed_result is not None and website_resolved
        else []
    )
    stages = completed_prefix or [
        make_stage_result(
            STAGE_LINKEDIN_DISCOVERY,
            "success" if has_linkedin_input else "not_applicable",
            input_count=1 if has_linkedin_input else 0,
            output_count=1 if has_linkedin_input else 0,
        ),
        make_stage_result(
            STAGE_WEBSITE_RESOLUTION,
            "success" if website_resolved else "failed",
            reason_code=None if website_resolved else error_code,
            duration_ms=int(stage_metrics.get("website_resolution_duration_ms") or 0),
            input_count=1,
            output_count=1 if website_resolved else 0,
            evidence=(
                [{"field": "company_website_url", "url": website_url}]
                if website_resolved
                else []
            ),
            detail=None if website_resolved else detail,
        ),
        make_stage_result(
            STAGE_HIRING_IDENTITY_RESOLUTION,
            "success" if website_resolved else "not_run",
            duration_ms=int(stage_metrics.get("hiring_identity_resolution_duration_ms") or 0),
            input_count=1 if website_resolved else 0,
            output_count=1 if website_resolved else 0,
            detail="Batch execution stopped before a complete identity result." if website_resolved else "Website resolution failed.",
        ),
    ]
    if website_resolved:
        stages.append(
            make_stage_result(
                STAGE_CAREER_DISCOVERY,
                "failed",
                reason_code=error_code,
                input_count=1,
                detail=detail,
            )
        )
    else:
        stages.append(make_stage_result(STAGE_CAREER_DISCOVERY, "not_run", detail="Website resolution failed."))
    stages.extend(
        [
            make_stage_result(STAGE_JOB_BOARD_DISCOVERY, "not_run", detail="A required upstream stage did not succeed."),
            make_stage_result(STAGE_OPENING_MATCH, "not_run", detail="A required upstream stage did not succeed."),
            make_stage_result(
                STAGE_RESULT_VALIDATION,
                "success",
                input_count=1,
                output_count=1,
                evidence=[{"field": "pipeline_status", "value": "failed"}],
            ),
        ]
    )
    return DiscoveryResult(
        company_name=company.company_name,
        company_website_url=website_url,
        hiring_entity_name=(
            completed_result.hiring_entity_name
            if completed_result is not None
            else company.hiring_entity_name
        ),
        career_root_url=(
            completed_result.career_root_url
            if completed_result is not None
            else company.career_root_url
        ),
        linkedin_job_url=company.linkedin_job_url,
        external_apply_url=company.external_apply_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        status="failed",
        error=error,
        error_code=error_code,
        pipeline_status="failed",
        stage_results=stages,
        trace={
            **(completed_result.trace if completed_result is not None else {}),
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


if __name__ == "__main__":
    main()
