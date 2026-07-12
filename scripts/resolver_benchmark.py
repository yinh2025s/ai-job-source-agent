from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.linkedin import load_company_inputs
from job_source_agent.web import Fetcher, domain_of
from job_source_agent.website_resolver import CompanyWebsiteResolver


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the deterministic offline website resolver benchmark.")
    parser.add_argument("--input", default=str(ROOT / "samples" / "resolver_benchmark_companies.json"))
    parser.add_argument(
        "--expectations",
        default=str(ROOT / "samples" / "resolver_benchmark_expectations.json"),
    )
    parser.add_argument("--fixtures-dir", default=str(ROOT / "samples" / "resolver_sites"))
    parser.add_argument("--output", default="/tmp/resolver-benchmark-results.json")
    return parser


def run_benchmark(input_path: str | Path, expectations_path: str | Path, fixtures_dir: str | Path) -> dict:
    companies = load_company_inputs(input_path)
    expectations = json.loads(Path(expectations_path).read_text(encoding="utf-8"))
    expectation_by_name = {item["company_name"]: item for item in expectations}
    results: list[dict] = []

    for company in companies:
        expectation = expectation_by_name[company.company_name]
        case_dir = Path(fixtures_dir) / expectation["fixture_case"]
        # Exercise every bounded fixture candidate so the benchmark measures
        # resolver identity decisions instead of the production latency budget.
        resolver = CompanyWebsiteResolver(
            Fetcher(fixtures_dir=case_dir, offline=True),
            verify_limit=20,
        )
        website_url, trace = resolver.resolve(company.company_name, company.linkedin_company_url)
        actual_domain = domain_of(website_url or "") or None
        expected_domain = expectation.get("expected_official_domain")
        selected_ok = actual_domain == expected_domain

        candidate_domains = {
            domain_of(str(candidate.get("url") or ""))
            for candidate in trace.get("candidates", [])
        }
        rejected_domains = expectation.get("must_reject_domains", [])
        rejection_checks = {
            domain: domain != actual_domain and domain in candidate_domains
            for domain in rejected_domains
        }
        passed = selected_ok and all(rejection_checks.values())
        results.append(
            {
                "company_name": company.company_name,
                "linkedin_company_url": company.linkedin_company_url,
                "company_website_url": company.company_website_url,
                "expected_official_domain": expected_domain,
                "actual_official_domain": actual_domain,
                "selected_url": website_url,
                "selected_ok": selected_ok,
                "rejection_checks": rejection_checks,
                "passed": passed,
                "trace": trace,
            }
        )

    unknown = sorted(set(expectation_by_name) - {company.company_name for company in companies})
    if unknown:
        raise ValueError(f"Expectations reference unknown companies: {', '.join(unknown)}")
    return {
        "total": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "results": results,
    }


def main() -> None:
    args = build_parser().parse_args()
    report = run_benchmark(args.input, args.expectations, args.fixtures_dir)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for result in report["results"]:
        print(
            f"{'PASS' if result['passed'] else 'FAIL'} {result['company_name']}: "
            f"expected={result['expected_official_domain']} actual={result['actual_official_domain']}"
        )
    print(f"resolver benchmark: {report['passed']}/{report['total']} passed")
    print(f"results: {args.output}")
    if report["failed"]:
        raise SystemExit("Resolver benchmark expectations failed; see the output JSON.")


if __name__ == "__main__":
    main()
