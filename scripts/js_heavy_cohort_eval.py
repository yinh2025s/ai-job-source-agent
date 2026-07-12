#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_source_agent.rendered_fetcher import SmartRenderedFetcher, _visible_text
from job_source_agent.web import Page, extract_links, normalize_url


DEFAULT_FIXTURE_ROOT = ROOT / "samples" / "sites" / "js-heavy-rendered-cohort"


def cohort_diversity(cases: list[dict]) -> dict[str, int | bool]:
    provider_count = len({case["provider"] for case in cases})
    technology_count = len({case["technology"] for case in cases})
    return {
        "provider_count": provider_count,
        "technology_count": technology_count,
        "diversity_passed": provider_count >= 3 and technology_count >= 3,
    }


class FixtureCohortFetcher(SmartRenderedFetcher):
    def __init__(self, cases: list[dict], fixture_root: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fixture_root = fixture_root
        self._cases = {case["url"]: case for case in cases}

    def _static_live(self, url, data=None, headers=None):
        case = self._cases[url]
        return self._page(url, case["static_fixture"], "saved_static_shell")

    def _render_live(self, url, reason="manual"):
        self.render_attempts += 1
        case = self._cases[url]
        return self._page(url, case["rendered_fixture"], "saved_browser_dom")

    def _page(self, url: str, relative_path: str, source: str) -> Page:
        path = self._fixture_root / relative_path
        return Page(
            url=url,
            final_url=url,
            html=path.read_text(encoding="utf-8"),
            source=source,
        )


def load_cases(fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> list[dict]:
    cases = json.loads((fixture_root / "cohort.json").read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) < 5:
        raise ValueError("JS-heavy cohort must contain at least five cases")
    required = {
        "company",
        "provider",
        "technology",
        "url",
        "evidence_text",
        "static_fixture",
        "rendered_fixture",
        "fixture_provenance",
    }
    for case in cases:
        if not isinstance(case, dict) or not required <= case.keys():
            raise ValueError("invalid JS-heavy cohort record")
        provenance = case["fixture_provenance"]
        if not isinstance(provenance, dict) or not {
            "captured_at",
            "static_source",
            "rendered_source",
            "capture_kind",
            "complete",
        } <= provenance.keys():
            raise ValueError("invalid JS-heavy fixture provenance")
        for fixture_key in ("static_fixture", "rendered_fixture"):
            if not (fixture_root / case[fixture_key]).is_file():
                raise ValueError(f"missing cohort fixture: {case[fixture_key]}")
    return cases


def evaluate_saved_cohort(fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> dict:
    cases = load_cases(fixture_root)
    fetcher = FixtureCohortFetcher(
        cases,
        fixture_root,
        render_budget=len(cases),
        min_visible_text_chars=120,
    )
    rows = []
    for case in cases:
        static_page = fetcher._static_live(case["url"])
        trigger_reason = fetcher._render_reason(static_page)
        attempts_before = fetcher.render_attempts
        page = fetcher._fetch_live(case["url"])
        visible_text = _visible_text(page.html)
        text_evidence_found = case["evidence_text"].casefold() in visible_text.casefold()
        expected_url = case.get("evidence_url")
        url_evidence_found = None
        if expected_url:
            normalized_expected_url = normalize_url(expected_url)
            url_evidence_found = any(
                normalize_url(link.url) == normalized_expected_url for link in extract_links(page)
            )
        evidence_found = text_evidence_found and url_evidence_found is not False
        rows.append(
            {
                "company": case["company"],
                "provider": case["provider"],
                "technology": case["technology"],
                "url": case["url"],
                "evidence_text": case["evidence_text"],
                "evidence_url": expected_url,
                "fixture_provenance": case["fixture_provenance"],
                "trigger_reason": trigger_reason,
                "render_triggered": fetcher.render_attempts == attempts_before + 1,
                "render_source": page.source,
                "career_job_evidence_found": evidence_found,
                "text_evidence_found": text_evidence_found,
                "url_evidence_found": url_evidence_found,
            }
        )

    # A sixth renderable request must consume no browser work after the shared
    # five-page budget is exhausted.
    attempts_before_exhausted_request = fetcher.render_attempts
    exhausted_page = fetcher._fetch_live(cases[0]["url"])
    budget_skip = fetcher.render_events[-1]
    budget_not_exceeded = (
        fetcher.render_attempts == attempts_before_exhausted_request == len(cases)
        and budget_skip["outcome"] == "skipped_budget"
        and exhausted_page.source == "saved_static_shell"
    )
    diversity = cohort_diversity(cases)
    provider_count = diversity["provider_count"]
    technology_count = diversity["technology_count"]
    diversity_passed = diversity["diversity_passed"]
    passed = all(
        row["trigger_reason"]
        and row["render_triggered"]
        and row["career_job_evidence_found"]
        for row in rows
    ) and budget_not_exceeded and diversity_passed
    return {
        "mode": "saved_fixture",
        "case_count": len(cases),
        "render_budget": len(cases),
        "render_attempts": fetcher.render_attempts,
        "provider_count": provider_count,
        "technology_count": technology_count,
        "diversity_passed": diversity_passed,
        "budget_not_exceeded": budget_not_exceeded,
        "exhausted_request_outcome": budget_skip["outcome"],
        "passed": passed,
        "cases": rows,
    }


def evaluate_live_smoke(
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
    *,
    timeout: float = 8.0,
    render_budget: int = 5,
) -> dict:
    cases = load_cases(fixture_root)
    fetcher = SmartRenderedFetcher(timeout=timeout, render_budget=render_budget)
    rows = []
    for case in cases:
        attempts_before = fetcher.render_attempts
        try:
            page = fetcher.fetch(case["url"])
            links = extract_links(page)
            visible_text = _visible_text(page.html)
            text_evidence_found = case["evidence_text"].casefold() in visible_text.casefold()
            expected_url = case.get("evidence_url")
            url_evidence_found = None
            if expected_url:
                normalized_expected_url = normalize_url(expected_url)
                url_evidence_found = any(
                    normalize_url(link.url) == normalized_expected_url for link in links
                )
            career_evidence_found = text_evidence_found
            rows.append(
                {
                    "company": case["company"],
                    "provider": case["provider"],
                    "technology": case["technology"],
                    "source": page.source,
                    "render_triggered": fetcher.render_attempts == attempts_before + 1,
                    "career_evidence_found": career_evidence_found,
                    "text_evidence_found": text_evidence_found,
                    "evidence_url_found": url_evidence_found,
                    "error": None,
                }
            )
        except Exception as error:  # live smoke records environmental failures
            rows.append(
                {
                    "company": case["company"],
                    "provider": case["provider"],
                    "technology": case["technology"],
                    "source": None,
                    "render_triggered": fetcher.render_attempts == attempts_before + 1,
                    "career_evidence_found": False,
                    "text_evidence_found": False,
                    "evidence_url_found": False if case.get("evidence_url") else None,
                    "error": str(error),
                }
            )
    diversity = cohort_diversity(cases)
    provider_count = diversity["provider_count"]
    technology_count = diversity["technology_count"]
    diversity_passed = diversity["diversity_passed"]
    budget_not_exceeded = fetcher.render_attempts <= render_budget
    passed = budget_not_exceeded and diversity_passed and all(
        row["render_triggered"] and row["career_evidence_found"] and row["error"] is None
        for row in rows
    )
    return {
        "mode": "live_browser_smoke",
        "case_count": len(cases),
        "timeout_seconds_per_page": timeout,
        "render_budget": render_budget,
        "render_attempts": fetcher.render_attempts,
        "provider_count": provider_count,
        "technology_count": technology_count,
        "diversity_passed": diversity_passed,
        "budget_not_exceeded": budget_not_exceeded,
        "passed": passed,
        "cases": rows,
        "render_events": fetcher.render_events,
    }


def summary_exit_code(summary: dict) -> int:
    return 0 if summary.get("budget_not_exceeded") and summary.get("passed") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the fixed five-company JS-heavy cohort.")
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--live", action="store_true", help="Run a bounded Playwright smoke against live pages.")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--render-budget", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.live:
        summary = evaluate_live_smoke(
            args.fixture_root,
            timeout=args.timeout,
            render_budget=args.render_budget,
        )
    else:
        summary = evaluate_saved_cohort(args.fixture_root)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return summary_exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
