from __future__ import annotations

import argparse
import json
from pathlib import Path

from .checkpoint_prefix import CheckpointPrefixError
from .composition import FetcherConfig, build_application
from .contracts import FetchClient
from .linkedin import load_company_inputs
from .linkedin_discovery import LinkedInJobsDiscoverer, linkedin_postings_to_company_inputs
from .models import PIPELINE_STAGES, dataclass_to_dict
from .run_configuration import AgentConfig


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
    parser.add_argument(
        "--max-career-transport-calls",
        type=int,
        default=32,
        help="Maximum underlying fetch dispatches during career discovery per company.",
    )
    parser.add_argument("--max-job-board-attempts", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Optional limit for quick demo runs.")
    parser.add_argument(
        "--checkpoint-dir",
        help="Optional directory for compatible per-company, per-stage checkpoints.",
    )
    parser.add_argument(
        "--linkedin-evidence-cache",
        help="Optional path for reusable LinkedIn official-website evidence.",
    )
    stage_group = parser.add_mutually_exclusive_group()
    stage_group.add_argument(
        "--resume-from-stage",
        choices=PIPELINE_STAGES,
        help="Reuse compatible upstream checkpoints and continue from this stage.",
    )
    stage_group.add_argument(
        "--rerun-stage",
        choices=PIPELINE_STAGES,
        help="Invalidate this stage and downstream checkpoints, then recompute them.",
    )
    parser.add_argument(
        "--stop-after-stage",
        choices=PIPELINE_STAGES,
        help="Stop after this stage and mark later stages not run.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if (args.resume_from_stage or args.rerun_stage) and not args.checkpoint_dir:
        raise SystemExit("--resume-from-stage and --rerun-stage require --checkpoint-dir.")
    application = build_application(
        FetcherConfig(
            fixtures_dir=args.fixtures_dir,
            offline=args.offline,
            timeout=args.fetch_timeout,
            render_mode="always" if args.render_js_always else "smart" if args.render_js else "none",
            render_budget=args.render_budget,
            capture_screenshot=args.render_screenshot,
        ),
        AgentConfig(
            max_career_discovery_transport_calls=args.max_career_transport_calls,
            max_job_board_attempts=args.max_job_board_attempts,
        ),
        checkpoint_dir=args.checkpoint_dir,
        website_overrides=args.website_overrides,
        linkedin_evidence_cache_path=args.linkedin_evidence_cache,
    )
    fetcher = application.fetcher
    companies = _load_companies(args, fetcher)
    if args.limit:
        companies = companies[: args.limit]

    try:
        results = [
            application.pipeline.discover(
                company,
                start_at=args.resume_from_stage,
                stop_after=args.stop_after_stage,
                rerun_from=args.rerun_stage,
            )
            for company in companies
        ]
    except CheckpointPrefixError as error:
        inspection = error.inspection
        defect_summary = ", ".join(
            f"{defect.stage}:{defect.defect_class}"
            for defect in inspection.defects
        )
        raise SystemExit(
            f"Cannot rerun from {inspection.requested_start}: checkpoint prefix is "
            f"not reusable ({defect_summary}). Resume from "
            f"{inspection.effective_start} or rebuild the missing checkpoints."
        ) from None

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
        for company in companies:
            company.source = "linkedin_public_jobs"
        return companies

    if args.input:
        return load_company_inputs(args.input)

    raise SystemExit("Provide either --input or --linkedin-keywords.")
