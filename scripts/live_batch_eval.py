from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.linkedin_discovery import (
    LinkedInJobsDiscoverer,
    linkedin_postings_to_company_inputs,
)
from job_source_agent.models import CompanyInput, DiscoveryResult, dataclass_to_dict
from job_source_agent.pipeline import JobSourceAgent
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
    parser.add_argument("--verify-limit", type=int, default=0)
    parser.add_argument("--max-career-candidates", type=int, default=6)
    parser.add_argument("--max-job-pages", type=int, default=3)
    parser.add_argument("--skip-sitemap", action="store_true")
    parser.add_argument("--output", default="/tmp/live-batch-results.json")
    parser.add_argument("--trace-output", default="/tmp/live-batch-trace.json")
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
    results = []
    traces = []
    started = time.time()

    print(f"unique companies: {len(companies)}", flush=True)
    for index, company in enumerate(companies, start=1):
        item_started = time.time()
        try:
            result = run_company(company, args)
        except Exception as exc:
            result = failure_result(
                company,
                error=f"batch_exception:{exc.__class__.__name__}",
                detail=traceback.format_exc(limit=8),
            )
        elapsed = round(time.time() - item_started, 1)
        results.append(result.result_record())
        traces.append(dataclass_to_dict(result.trace_record()))
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        trace_path.write_text(json.dumps(traces, indent=2), encoding="utf-8")

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

    print_summary(results, round(time.time() - started, 1))
    print(f"results: {output_path}", flush=True)
    print(f"trace: {trace_path}", flush=True)


def run_company(company: CompanyInput, args: argparse.Namespace):
    fetcher = Fetcher(timeout=args.fetch_timeout)
    identity_resolver = CompanyIdentityResolver()
    website_resolver = CompanyWebsiteResolver(fetcher, verify_limit=args.verify_limit)

    identity, identity_trace = identity_resolver.resolve(
        company.company_name,
        company.company_website_url or None,
        company.linkedin_company_url,
    )
    company.source_trace["identity_resolution"] = identity_trace
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

    if not company.company_website_url:
        result = JobSourceAgent(
            fetcher,
            max_candidates=args.max_career_candidates,
            max_job_pages=args.max_job_pages,
            enable_sitemap_discovery=not args.skip_sitemap,
            career_search_timeout=args.career_search_timeout,
        ).discover(
            CompanyInput(
                company_name=company.company_name,
                company_website_url="https://invalid.local",
                linkedin_job_url=company.linkedin_job_url,
                linkedin_company_url=company.linkedin_company_url,
                job_title=company.job_title,
                job_location=company.job_location,
                source=company.source,
                source_trace=company.source_trace,
            )
        )
        result.company_website_url = ""
        result.error = "website_not_resolved"
        return result

    return JobSourceAgent(
        fetcher,
        max_candidates=args.max_career_candidates,
        max_job_pages=args.max_job_pages,
        enable_sitemap_discovery=not args.skip_sitemap,
        career_search_timeout=args.career_search_timeout,
    ).discover(company)


def failure_result(company: CompanyInput, error: str, detail: str | None = None) -> DiscoveryResult:
    return DiscoveryResult(
        company_name=company.company_name,
        company_website_url=company.company_website_url or "",
        linkedin_job_url=company.linkedin_job_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        status="failed",
        error=error,
        trace={
            "source": company.source,
            "source_trace": company.source_trace,
            "batch_error": error,
            "batch_error_detail": detail,
        },
    )


def print_summary(results: list[dict], elapsed: float) -> None:
    total = len(results)
    success = sum(1 for result in results if result.get("status") == "success")
    with_job_list = sum(1 for result in results if result.get("job_list_page_url"))
    with_opening = sum(1 for result in results if result.get("open_position_url"))
    errors = Counter(result.get("error") or "none" for result in results)
    print("summary:", flush=True)
    print(f"  total: {total}", flush=True)
    print(f"  success: {success}", flush=True)
    print(f"  with_job_list: {with_job_list}", flush=True)
    print(f"  with_opening: {with_opening}", flush=True)
    print(f"  elapsed_sec: {elapsed}", flush=True)
    print(f"  errors: {dict(errors)}", flush=True)


if __name__ == "__main__":
    main()
