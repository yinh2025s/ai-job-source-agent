from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.composition import FetcherConfig, build_application
from job_source_agent.evaluation import compare_summaries, evaluate_expectations, summarize_results
from job_source_agent.evaluation_history import cohort_identities_compatible, derive_cohort_identity
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import dataclass_to_dict
from job_source_agent.run_configuration import AgentConfig


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixed benchmark set against the job-source pipeline.")
    parser.add_argument("--input", default=str(ROOT / "samples" / "benchmark_companies.json"))
    parser.add_argument(
        "--expectations",
        default=str(ROOT / "samples" / "benchmark_expectations.json"),
        help="Optional benchmark expectations JSON keyed by company name.",
    )
    parser.add_argument("--fixtures-dir")
    parser.add_argument("--live", action="store_true", help="Use live network instead of requiring fixtures.")
    parser.add_argument("--fetch-timeout", type=float, default=4)
    parser.add_argument("--max-career-candidates", type=int, default=8)
    parser.add_argument("--max-career-fetches", type=int, default=6)
    parser.add_argument("--max-career-search-queries", type=int, default=5)
    parser.add_argument("--max-ats-board-fetches", type=int, default=5)
    parser.add_argument("--max-job-pages", type=int, default=4)
    parser.add_argument("--skip-sitemap", action="store_true")
    parser.add_argument("--output", default="/tmp/benchmark-results.json")
    parser.add_argument("--trace-output", default="/tmp/benchmark-trace.json")
    parser.add_argument("--summary-output", default="/tmp/benchmark-summary.json")
    parser.add_argument("--baseline-summary", help="Optional prior summary JSON used for regression deltas.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    companies = load_company_inputs(args.input)
    expectations = json.loads(Path(args.expectations).read_text(encoding="utf-8")) if args.expectations else {}
    evaluation_manifest = {
        "companies_sha256": _json_digest([dataclass_to_dict(company) for company in companies]),
        "expectations_sha256": _json_digest(expectations),
    }
    fixtures_dir = args.fixtures_dir or (str(ROOT / "samples" / "sites") if not args.live else None)
    application = build_application(
        FetcherConfig(
            fixtures_dir=fixtures_dir,
            offline=not args.live,
            timeout=args.fetch_timeout,
        ),
        AgentConfig(
            max_candidates=args.max_career_candidates,
            max_job_pages=args.max_job_pages,
            max_career_candidate_fetches=args.max_career_fetches,
            max_career_search_queries=args.max_career_search_queries,
            max_ats_board_fetches=args.max_ats_board_fetches,
            enable_sitemap_discovery=not args.skip_sitemap,
        ),
    )
    run_configuration = application.pipeline.run_configuration
    evaluation_manifest["run_configuration_digest"] = run_configuration.digest
    output_path = Path(args.output)
    trace_path = Path(args.trace_output)
    summary_path = Path(args.summary_output)
    result_records = []
    trace_records = []

    for index, company in enumerate(companies, start=1):
        item_started = time.time()
        result = application.pipeline.discover(company)
        result_records.append(result.result_record())
        trace_records.append(dataclass_to_dict(result.trace_record()))
        summary = summarize_results(result_records, elapsed_sec=round(time.time() - started, 3))
        summary["run_configuration"] = run_configuration.to_payload()
        summary["run_configuration_digest"] = run_configuration.digest
        summary["expectation_checks"] = evaluate_expectations(result_records, expectations)
        summary["evaluation_manifest"] = evaluation_manifest

        output_path.write_text(json.dumps(result_records, indent=2), encoding="utf-8")
        trace_path.write_text(json.dumps(trace_records, indent=2), encoding="utf-8")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"[{index:02d}/{len(companies):02d}] "
            f"{result.status.upper()} {result.company_name} "
            f"career={bool(result.career_page_url)} "
            f"job_list={bool(result.job_list_page_url)} "
            f"opening={bool(result.open_position_url)} "
            f"error={result.error} "
            f"elapsed={round(time.time() - item_started, 1)}s",
            flush=True,
        )

    summary = summarize_results(result_records, elapsed_sec=round(time.time() - started, 3))
    summary["run_configuration"] = run_configuration.to_payload()
    summary["run_configuration_digest"] = run_configuration.digest
    summary["expectation_checks"] = evaluate_expectations(result_records, expectations)
    summary["evaluation_manifest"] = evaluation_manifest
    if args.baseline_summary:
        baseline_summary = json.loads(Path(args.baseline_summary).read_text(encoding="utf-8"))
        if cohort_identities_compatible(
            derive_cohort_identity(summary),
            derive_cohort_identity(baseline_summary),
        ):
            summary["regression"] = compare_summaries(summary, baseline_summary)
        else:
            summary["regression"] = {"comparison_status": "no_compatible_baseline"}

    output_path.write_text(json.dumps(result_records, indent=2), encoding="utf-8")
    trace_path.write_text(json.dumps(trace_records, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print_summary(summary)
    print(f"results: {args.output}", flush=True)
    print(f"trace: {args.trace_output}", flush=True)
    print(f"summary: {args.summary_output}", flush=True)
    if summary["expectation_checks"]["failed"]:
        raise SystemExit("Benchmark expectations failed; see the summary JSON for the failing companies.")


def print_summary(summary: dict) -> None:
    print("benchmark summary:", flush=True)
    print(f"  total: {summary['total']}", flush=True)
    print(f"  success: {summary['success']}", flush=True)
    print(f"  pipeline_statuses: {summary['pipeline_status_counts']}", flush=True)
    print(f"  with_job_list: {summary['with_job_list']}", flush=True)
    print(f"  with_opening: {summary['with_opening']}", flush=True)
    expectation_checks = summary.get("expectation_checks", {})
    if expectation_checks:
        print(
            f"  expectations: {expectation_checks['passed']}/{expectation_checks['total']} passed",
            flush=True,
        )
    regression = summary.get("regression")
    if regression and "rates_delta" in regression:
        print(f"  rate_delta: {regression['rates_delta']}", flush=True)
    elif regression and regression.get("comparison_status"):
        print(f"  baseline_comparison: {regression['comparison_status']}", flush=True)
    print(f"  rates: {summary['rates']}", flush=True)
    print(f"  providers: {summary['provider_counts']}", flush=True)


def _json_digest(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    main()
