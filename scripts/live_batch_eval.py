from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.evaluation import compare_summaries, evaluate_expectations, summarize_results
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
    CompanyInput,
    DiscoveryResult,
    dataclass_to_dict,
)
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.process_budget import ProcessBudgetExceeded, RemoteProcessError, run_with_process_budget
from job_source_agent.reasons import canonical_reason_code, make_stage_result
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.snapshot import SnapshottingFetcher
from job_source_agent.web import Fetcher, normalize_url
from job_source_agent.website_resolver import CompanyWebsiteResolver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a checkpointed live batch evaluation.")
    parser.add_argument("--input", help="Optional fixed company input JSON. If omitted, LinkedIn search is used.")
    parser.add_argument("--expectations", help="Optional expectations JSON keyed by company name.")
    parser.add_argument("--linkedin-keywords")
    parser.add_argument("--linkedin-location", default="United States")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--linkedin-pages", type=int, default=3)
    parser.add_argument("--fetch-timeout", type=float, default=3)
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
    parser.add_argument("--baseline-summary", help="Optional prior summary JSON used for regression deltas.")
    parser.add_argument("--workers", type=int, default=1, help="Number of companies to process concurrently.")
    parser.add_argument(
        "--resume-from-stage",
        choices=[
            STAGE_CAREER_DISCOVERY,
            STAGE_JOB_BOARD_DISCOVERY,
            STAGE_OPENING_MATCH,
        ],
        help="Reuse upstream evidence from replay input and resume from this downstream stage.",
    )
    parser.add_argument(
        "--require-all-expectations",
        action="store_true",
        help="Fail expectation checks for companies that are not present in a partial live run.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    linkedin_fetcher = Fetcher(timeout=max(args.fetch_timeout, 6))
    companies = load_batch_companies(args, linkedin_fetcher)

    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    summary_path = Path(args.summary_output)
    results = []
    traces = []
    started = time.time()

    print(f"unique companies: {len(companies)}", flush=True)
    if args.workers <= 1:
        for index, company in enumerate(companies, start=1):
            index, result, elapsed = run_company_timed(index, company, args)
            record_checkpoint(
                index,
                len(companies),
                result,
                elapsed,
                results,
                traces,
                output_path,
                trace_path,
                summary_path,
                args,
                started,
            )
    else:
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="live-company") as executor:
            futures = [
                executor.submit(run_company_timed, index, company, args)
                for index, company in enumerate(companies, start=1)
            ]
            for future in as_completed(futures):
                index, result, elapsed = future.result()
                record_checkpoint(
                    index,
                    len(companies),
                    result,
                    elapsed,
                    results,
                    traces,
                    output_path,
                    trace_path,
                    summary_path,
                    args,
                    started,
                )

    summary = build_summary(results, args, elapsed_sec=round(time.time() - started, 1))
    if args.baseline_summary:
        baseline_summary = json.loads(Path(args.baseline_summary).read_text(encoding="utf-8"))
        summary["regression"] = compare_summaries(summary, baseline_summary)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_summary(summary)
    print(f"results: {output_path}", flush=True)
    print(f"trace: {trace_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    expectation_checks = summary.get("expectation_checks", {})
    if expectation_checks.get("failed"):
        raise SystemExit("Live expectations failed; see the summary JSON for details.")


def load_batch_companies(args: argparse.Namespace, linkedin_fetcher: Fetcher) -> list[CompanyInput]:
    if args.input:
        return load_company_inputs(args.input)[: args.limit]
    if not args.linkedin_keywords:
        raise SystemExit("Provide either --input or --linkedin-keywords.")
    postings = LinkedInJobsDiscoverer(linkedin_fetcher).search(
        keywords=args.linkedin_keywords,
        location=args.linkedin_location,
        limit=args.limit,
        pages=args.linkedin_pages,
    )
    return linkedin_postings_to_company_inputs(postings)[: args.limit]


def build_summary(results: list[dict], args: argparse.Namespace, elapsed_sec: float) -> dict:
    summary = summarize_results(results, elapsed_sec=elapsed_sec)
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
    return summary


def run_company_timed(index: int, company: CompanyInput, args: argparse.Namespace) -> tuple[int, DiscoveryResult, float]:
    item_started = time.time()
    result = run_company(company, args)
    elapsed = round(time.time() - item_started, 1)
    return index, result, elapsed


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
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    trace_path.write_text(json.dumps(traces, indent=2), encoding="utf-8")
    summary = build_summary(results, args, elapsed_sec=round(time.time() - started, 1))
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
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


def run_company(company: CompanyInput, args: argparse.Namespace):
    started = time.monotonic()
    if resume_uses_replay_upstream(args):
        prepared_company = prepare_replay_company_for_resume(company, args)
    else:
        try:
            prepared_company = run_with_process_budget(
                prepare_company,
                (company, args),
                timeout=min(args.website_time_budget, args.company_time_budget),
            )
        except ProcessBudgetExceeded:
            return failure_result(
                company,
                error="company_time_budget_exhausted",
                detail=f"Website/identity resolution exceeded its {min(args.website_time_budget, args.company_time_budget):g}-second stage budget.",
            )
        except RemoteProcessError as exc:
            return failure_result(company, error="batch_worker_failed", detail=str(exc))

    if not prepared_company.company_website_url:
        return failure_result(
            prepared_company,
            error="website_not_resolved",
            detail=(
                f"--resume-from-stage {args.resume_from_stage} requires replay input with company_website_url."
                if resume_uses_replay_upstream(args)
                else "Website resolver did not produce a verified official company domain."
            ),
        )

    remaining = args.company_time_budget - (time.monotonic() - started)
    if remaining <= 0:
        return failure_result(
            prepared_company,
            error="company_time_budget_exhausted",
            detail=f"Exceeded the {args.company_time_budget:g}-second company budget after website resolution.",
        )
    try:
        return run_with_process_budget(
            discover_prepared_company,
            (prepared_company, args),
            timeout=remaining,
        )
    except ProcessBudgetExceeded:
        return failure_result(
            prepared_company,
            error="company_time_budget_exhausted",
            detail=f"Career discovery exceeded the remaining {remaining:.1f}-second company budget.",
        )
    except RemoteProcessError as exc:
        return failure_result(prepared_company, error="batch_worker_failed", detail=str(exc))


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
    fetcher = build_company_fetcher(args)
    result = JobSourceAgent(
        fetcher,
        max_candidates=args.max_career_candidates,
        max_job_pages=args.max_job_pages,
        max_career_candidate_fetches=args.max_career_fetches,
        max_career_search_queries=args.max_career_search_queries,
        max_ats_board_fetches=args.max_ats_board_fetches,
        enable_sitemap_discovery=not args.skip_sitemap,
        career_search_timeout=args.career_search_timeout,
    ).discover(company)
    retry_events = getattr(fetcher, "retry_events", None)
    if retry_events:
        result.trace["retry_events"] = retry_events
    render_events = getattr(fetcher, "render_events", None)
    if render_events:
        result.trace["render_events"] = render_events
    return result


def build_company_fetcher(args: argparse.Namespace):
    if args.render_js:
        fetcher = SmartRenderedFetcher(
            timeout=args.fetch_timeout,
            render_budget=args.render_budget,
            capture_screenshot=bool(getattr(args, "render_screenshot", False)),
        )
    else:
        fetcher = Fetcher(timeout=args.fetch_timeout)
    fetch_retries = int(getattr(args, "fetch_retries", 0) or 0)
    if fetch_retries > 0:
        fetcher = RetryingFetcher(
            fetcher,
            max_retries=fetch_retries,
            base_delay=float(getattr(args, "retry_base_delay", 0.25) or 0),
        )
    snapshot_dir = getattr(args, "snapshot_dir", None)
    if snapshot_dir:
        return SnapshottingFetcher(fetcher, snapshot_dir)
    return fetcher


def failure_result(company: CompanyInput, error: str, detail: str | None = None) -> DiscoveryResult:
    error_code = canonical_reason_code(error)
    stage_metrics = company.source_trace.get("stage_metrics", {})
    has_linkedin_input = bool(company.linkedin_job_url or company.linkedin_company_url)
    website_resolved = bool(company.company_website_url) and error_code != "WEBSITE_NOT_RESOLVED"
    stages = [
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
                [{"field": "company_website_url", "url": company.company_website_url}]
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
        company_website_url=company.company_website_url or "",
        linkedin_job_url=company.linkedin_job_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        status="failed",
        error=error,
        error_code=error_code,
        pipeline_status="failed",
        stage_results=stages,
        trace={
            "source": company.source,
            "source_trace": company.source_trace,
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
    if summary.get("regression"):
        print(f"  rate_delta: {summary['regression']['rates_delta']}", flush=True)
    expectation_checks = summary.get("expectation_checks")
    if expectation_checks:
        print(
            f"  expectations: {expectation_checks['passed']}/{expectation_checks['total']} passed",
            flush=True,
        )


if __name__ == "__main__":
    main()
