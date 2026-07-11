from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.evaluation import compare_summaries, summarize_results
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
from job_source_agent.web import Fetcher
from job_source_agent.website_resolver import CompanyWebsiteResolver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a checkpointed live batch evaluation.")
    parser.add_argument("--linkedin-keywords", required=True)
    parser.add_argument("--linkedin-location", default="United States")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--linkedin-pages", type=int, default=3)
    parser.add_argument("--fetch-timeout", type=float, default=3)
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
    parser.add_argument("--output", default="/tmp/live-batch-results.json")
    parser.add_argument("--trace-output", default="/tmp/live-batch-trace.json")
    parser.add_argument("--summary-output", default="/tmp/live-batch-summary.json")
    parser.add_argument("--baseline-summary", help="Optional prior summary JSON used for regression deltas.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    linkedin_fetcher = Fetcher(timeout=max(args.fetch_timeout, 6))
    postings = LinkedInJobsDiscoverer(linkedin_fetcher).search(
        keywords=args.linkedin_keywords,
        location=args.linkedin_location,
        limit=args.limit,
        pages=args.linkedin_pages,
    )
    companies = linkedin_postings_to_company_inputs(postings)[: args.limit]

    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    summary_path = Path(args.summary_output)
    results = []
    traces = []
    started = time.time()

    print(f"unique companies: {len(companies)}", flush=True)
    for index, company in enumerate(companies, start=1):
        item_started = time.time()
        result = run_company(company, args)
        elapsed = round(time.time() - item_started, 1)
        results.append(result.result_record())
        traces.append(dataclass_to_dict(result.trace_record()))
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        trace_path.write_text(json.dumps(traces, indent=2), encoding="utf-8")
        summary_path.write_text(
            json.dumps(summarize_results(results, elapsed_sec=round(time.time() - started, 1)), indent=2),
            encoding="utf-8",
        )

        print(
            f"[{index:02d}/{len(companies):02d}] "
            f"{result.status.upper()} {result.company_name} "
            f"career={bool(result.career_page_url)} "
            f"job_list={bool(result.job_list_page_url)} "
            f"opening={bool(result.open_position_url)} "
            f"error={result.error} "
            f"elapsed={elapsed}s",
            flush=True,
        )

    summary = summarize_results(results, elapsed_sec=round(time.time() - started, 1))
    if args.baseline_summary:
        baseline_summary = json.loads(Path(args.baseline_summary).read_text(encoding="utf-8"))
        summary["regression"] = compare_summaries(summary, baseline_summary)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_summary(summary)
    print(f"results: {output_path}", flush=True)
    print(f"trace: {trace_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)


def run_company(company: CompanyInput, args: argparse.Namespace):
    started = time.monotonic()
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
            detail="Website resolver did not produce a verified official company domain.",
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
    return company


def discover_prepared_company(company: CompanyInput, args: argparse.Namespace) -> DiscoveryResult:
    fetcher = build_company_fetcher(args)
    return JobSourceAgent(
        fetcher,
        max_candidates=args.max_career_candidates,
        max_job_pages=args.max_job_pages,
        max_career_candidate_fetches=args.max_career_fetches,
        max_career_search_queries=args.max_career_search_queries,
        max_ats_board_fetches=args.max_ats_board_fetches,
        enable_sitemap_discovery=not args.skip_sitemap,
        career_search_timeout=args.career_search_timeout,
    ).discover(company)


def build_company_fetcher(args: argparse.Namespace):
    if args.render_js:
        return SmartRenderedFetcher(timeout=args.fetch_timeout, render_budget=args.render_budget)
    return Fetcher(timeout=args.fetch_timeout)


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


if __name__ == "__main__":
    main()
