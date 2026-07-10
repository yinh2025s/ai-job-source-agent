from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.evaluation import summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import dataclass_to_dict
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixed benchmark set against the job-source pipeline.")
    parser.add_argument("--input", default=str(ROOT / "samples" / "benchmark_companies.json"))
    parser.add_argument("--fixtures-dir", default=str(ROOT / "samples" / "sites"))
    parser.add_argument("--live", action="store_true", help="Use live network instead of requiring fixtures.")
    parser.add_argument("--fetch-timeout", type=float, default=4)
    parser.add_argument("--max-career-candidates", type=int, default=8)
    parser.add_argument("--max-job-pages", type=int, default=4)
    parser.add_argument("--output", default="/tmp/benchmark-results.json")
    parser.add_argument("--trace-output", default="/tmp/benchmark-trace.json")
    parser.add_argument("--summary-output", default="/tmp/benchmark-summary.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    companies = load_company_inputs(args.input)
    fetcher = Fetcher(
        fixtures_dir=args.fixtures_dir,
        offline=not args.live,
        timeout=args.fetch_timeout,
    )
    agent = JobSourceAgent(
        fetcher,
        max_candidates=args.max_career_candidates,
        max_job_pages=args.max_job_pages,
    )
    results = [agent.discover(company) for company in companies]
    result_records = [result.result_record() for result in results]
    trace_records = [dataclass_to_dict(result.trace_record()) for result in results]
    summary = summarize_results(result_records, elapsed_sec=round(time.time() - started, 3))

    Path(args.output).write_text(json.dumps(result_records, indent=2), encoding="utf-8")
    Path(args.trace_output).write_text(json.dumps(trace_records, indent=2), encoding="utf-8")
    Path(args.summary_output).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print_summary(summary)
    print(f"results: {args.output}", flush=True)
    print(f"trace: {args.trace_output}", flush=True)
    print(f"summary: {args.summary_output}", flush=True)


def print_summary(summary: dict) -> None:
    print("benchmark summary:", flush=True)
    print(f"  total: {summary['total']}", flush=True)
    print(f"  success: {summary['success']}", flush=True)
    print(f"  with_job_list: {summary['with_job_list']}", flush=True)
    print(f"  with_opening: {summary['with_opening']}", flush=True)
    print(f"  rates: {summary['rates']}", flush=True)
    print(f"  providers: {summary['provider_counts']}", flush=True)


if __name__ == "__main__":
    main()
