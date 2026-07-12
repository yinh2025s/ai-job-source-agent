from __future__ import annotations

import argparse
import json
from pathlib import Path

from .company_identity import CompanyIdentityResolver
from .composition import FetcherConfig, build_application
from .contracts import FetchClient
from .linkedin import load_company_inputs
from .linkedin_discovery import LinkedInJobsDiscoverer, linkedin_postings_to_company_inputs
from .models import dataclass_to_dict
from .website_resolver import CompanyWebsiteResolver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover career pages and one open role per company.")
    parser.add_argument("--input", help="JSON records from the LinkedIn extractor adapter.")
    parser.add_argument("--linkedin-keywords", help="Search public LinkedIn jobs for hiring companies.")
    parser.add_argument("--linkedin-location", default="United States", help="LinkedIn job-search location.")
    parser.add_argument("--linkedin-pages", type=int, default=2, help="LinkedIn result pages to scan.")
    parser.add_argument("--website-overrides", help="Optional JSON map of company name to official website.")
    parser.add_argument("--output", default="results.json", help="Path for concise result JSON.")
    parser.add_argument("--trace-output", default="trace.json", help="Path for detailed trace JSON.")
    parser.add_argument("--fixtures-dir", help="Optional offline fixture directory for deterministic demos.")
    parser.add_argument("--offline", action="store_true", help="Fail instead of using the live network.")
    parser.add_argument("--render-js", action="store_true", help="Use optional Playwright browser fallback.")
    parser.add_argument(
        "--render-js-always",
        action="store_true",
        help="Render every live HTML page through Playwright instead of using smart fallback.",
    )
    parser.add_argument("--render-budget", type=int, default=3, help="Maximum browser-rendered pages per run.")
    parser.add_argument(
        "--render-screenshot",
        action="store_true",
        help="Capture a browser screenshot artifact for pages rendered with Playwright.",
    )
    parser.add_argument("--fetch-timeout", type=float, default=8, help="Per-page fetch timeout in seconds.")
    parser.add_argument("--limit", type=int, help="Optional limit for quick demo runs.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    application = build_application(
        FetcherConfig(
            fixtures_dir=args.fixtures_dir,
            offline=args.offline,
            timeout=args.fetch_timeout,
            render_mode="always" if args.render_js_always else "smart" if args.render_js else "none",
            render_budget=args.render_budget,
            capture_screenshot=args.render_screenshot,
        )
    )
    fetcher = application.fetcher
    companies = _load_companies(args, fetcher)
    if args.limit:
        companies = companies[: args.limit]

    results = [application.agent.discover(company) for company in companies]

    Path(args.output).write_text(
        json.dumps([result.result_record() for result in results], indent=2),
        encoding="utf-8",
    )
    Path(args.trace_output).write_text(
        json.dumps([dataclass_to_dict(result.trace_record()) for result in results], indent=2),
        encoding="utf-8",
    )

    for result in results:
        status_icon = "OK" if result.status == "success" else "FAIL"
        print(f"{status_icon} {result.company_name}")
        if result.linkedin_job_title:
            print(f"  linkedin job: {result.linkedin_job_title}")
        print(f"  website: {result.company_website_url}")
        print(f"  career: {result.career_page_url}")
        print(f"  job list: {result.job_list_page_url}")
        print(f"  opening: {result.open_position_url}")
        if result.error:
            print(f"  error: {result.error}")


def _load_companies(args: argparse.Namespace, fetcher: FetchClient):
    if args.linkedin_keywords:
        discoverer = LinkedInJobsDiscoverer(fetcher)
        postings = discoverer.search(
            keywords=args.linkedin_keywords,
            location=args.linkedin_location,
            limit=args.limit or 10,
            pages=args.linkedin_pages,
        )
        companies = linkedin_postings_to_company_inputs(postings)
        resolver = CompanyWebsiteResolver(fetcher, overrides_path=args.website_overrides, verify_limit=3)
        identity_resolver = CompanyIdentityResolver()
        for company in companies:
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
                company.source = "linkedin_public_jobs"
                company.source_trace["website_resolution"] = {
                    "selected": {
                        "url": company.company_website_url,
                        "reason": "provided by company identity resolver",
                    }
                }
                continue

            website_url, trace = resolver.resolve(company.company_name, company.linkedin_company_url)
            company.company_website_url = website_url or ""
            company.source = "linkedin_public_jobs"
            company.source_trace["website_resolution"] = trace
        return [company for company in companies if company.company_website_url]

    if args.input:
        return load_company_inputs(args.input)

    raise SystemExit("Provide either --input or --linkedin-keywords.")
