from __future__ import annotations

import argparse
import json
from pathlib import Path

from .linkedin import load_company_inputs
from .models import dataclass_to_dict
from .pipeline import JobSourceAgent
from .web import Fetcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover career pages and one open role per company.")
    parser.add_argument("--input", required=True, help="JSON records from the LinkedIn extractor adapter.")
    parser.add_argument("--output", default="results.json", help="Path for concise result JSON.")
    parser.add_argument("--trace-output", default="trace.json", help="Path for detailed trace JSON.")
    parser.add_argument("--fixtures-dir", help="Optional offline fixture directory for deterministic demos.")
    parser.add_argument("--offline", action="store_true", help="Fail instead of using the live network.")
    parser.add_argument("--limit", type=int, help="Optional limit for quick demo runs.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    companies = load_company_inputs(args.input)
    if args.limit:
        companies = companies[: args.limit]

    fetcher = Fetcher(fixtures_dir=args.fixtures_dir, offline=args.offline)
    agent = JobSourceAgent(fetcher)
    results = [agent.discover(company) for company in companies]

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
        print(f"  career: {result.career_page_url}")
        print(f"  opening: {result.open_position_url}")
        if result.error:
            print(f"  error: {result.error}")
