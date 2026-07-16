import unittest
from pathlib import Path

from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.providers import (
    GreenhouseAdapter,
    JobQuery,
    ProviderRegistry,
    build_default_provider_registry,
    discover_native_adapters,
)
from job_source_agent.web import Fetcher, Page


ROOT = Path(__file__).resolve().parents[1]


class ProviderRegistryTests(unittest.TestCase):
    def test_native_adapters_are_discovered_without_central_registration(self):
        adapters = discover_native_adapters()

        self.assertIn("greenhouse", {adapter.name for adapter in adapters})
        self.assertTrue(next(adapter for adapter in adapters if adapter.name == "greenhouse").supports_listing)

    def test_default_registry_preserves_existing_provider_detection(self):
        registry = build_default_provider_registry()
        cases = {
            "https://boards.greenhouse.io/acme": "greenhouse",
            "https://jobs.lever.co/acme": "lever",
            "https://jobs.ashbyhq.com/acme": "ashby",
            "https://company.wd5.myworkdayjobs.com/acme": "workday",
            "https://careers-acme.icims.com/jobs/search": "icims",
        }

        for url, provider in cases.items():
            with self.subTest(url=url):
                self.assertEqual(registry.detect(url), provider)

    def test_native_adapters_canonicalize_detail_urls_to_tenant_boards(self):
        registry = build_default_provider_registry()
        cases = (
            (
                "https://jobs.lever.co/acme/role-123",
                "https://jobs.lever.co/acme",
            ),
            (
                "https://job-boards.greenhouse.io/acme/jobs/123",
                "https://job-boards.greenhouse.io/acme",
            ),
        )
        for detail_url, expected_board in cases:
            with self.subTest(detail_url=detail_url):
                adapter = registry.adapter_for(detail_url)
                self.assertIsNotNone(adapter)
                self.assertEqual(adapter.identify_board(detail_url).url, expected_board)

    def test_registry_rejects_duplicate_provider_names(self):
        registry = ProviderRegistry([GreenhouseAdapter()])

        with self.assertRaises(ValueError):
            registry.register(GreenhouseAdapter())

    def test_registry_detects_page_aware_provider_without_hardcoding_domain(self):
        registry = build_default_provider_registry()
        page = Page(
            url="https://jobs.example.org/region/jobs",
            html=(
                '<html data-jibe-search-version="4.11">'
                '<script>window.searchConfig = {"externalSearch":true};</script>'
                '<script src="https://app.jibecdn.com/prod/search/4/main.js"></script>'
            ),
        )

        adapter, board = registry.board_for_page(page)

        self.assertEqual(adapter.name, "icims")
        self.assertEqual(board.identifier, "jobs.example.org")

    def test_greenhouse_adapter_lists_normalized_candidates(self):
        adapter = GreenhouseAdapter()
        board = adapter.identify_board("https://boards.greenhouse.io/acme")

        result = adapter.list_jobs(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            board,
            JobQuery(title="Data Analyst"),
        )

        self.assertEqual(result.provider, "greenhouse")
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(result.candidates[0].url, "https://boards.greenhouse.io/acme/jobs/12345")
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_opening_matcher_uses_native_greenhouse_adapter(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match("https://boards.greenhouse.io/acme", "Data Analyst")

        self.assertEqual(match.url, "https://boards.greenhouse.io/acme/jobs/12345")
        self.assertEqual(trace["provider_api"]["adapter"], "greenhouse")
        self.assertIn("provider adapter result", match.reasons)


if __name__ == "__main__":
    unittest.main()
