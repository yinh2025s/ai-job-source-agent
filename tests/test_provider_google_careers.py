import unittest
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.domain import DomainProviderAdapter
from job_source_agent.providers.google_careers import ADAPTER, GoogleCareersAdapter
from job_source_agent.providers.registry import (
    build_default_provider_registry,
    discover_native_adapters,
)
from job_source_agent.web import FetchError, Page


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "samples" / "sites" / "google_careers"


class FixtureFetcher:
    def __init__(self, html: str, final_url: str | None = None, error=None):
        self.html = html
        self.final_url = final_url
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="google-careers-contract",
        )


class GoogleCareersAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GoogleCareersAdapter()

    def test_native_adapter_is_auto_discovered_and_replaces_legacy_compatibility(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        selected = build_default_provider_registry().adapter_for(
            "https://www.google.com/about/careers/applications/"
        )

        self.assertIs(native["google_careers"], ADAPTER)
        self.assertIs(selected, ADAPTER)
        self.assertIsInstance(selected, ProviderAdapter)
        self.assertNotIsInstance(selected, DomainProviderAdapter)
        self.assertTrue(selected.supports_listing)

    def test_recognizes_only_safe_google_careers_paths(self):
        valid = (
            "https://www.google.com/about/careers/applications/",
            "https://google.com/about/careers/applications/jobs/results/",
            "https://www.google.com/about/careers/applications/jobs/results/123-product-manager-ads",
        )
        invalid = (
            "https://www.google.com/search?q=careers",
            "https://mail.google.com/about/careers/applications/",
            "https://evil@www.google.com/about/careers/applications/",
            "https://www.google.com:8443/about/careers/applications/",
            "http://www.google.com/about/careers/applications/",
            "https://www.google.com/about/careers/applications/jobs/results/not-a-job",
        )
        for url in valid:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))

    def test_lists_exact_opening_from_server_rendered_contract(self):
        html = (FIXTURES / "software-engineer-results.html").read_text(encoding="utf-8")
        fetcher = FixtureFetcher(html)
        board = self.adapter.identify_board(
            "https://www.google.com/about/careers/applications/"
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Software Engineer"))

        self.assertEqual(
            fetcher.requested_urls,
            [
                "https://www.google.com/about/careers/applications/jobs/results/"
                "?q=Software+Engineer"
            ],
        )
        self.assertEqual([candidate.title for candidate in result.candidates], [
            "Software Engineer",
            "Software Engineer, AI Software Agents",
        ])
        self.assertEqual(
            result.candidates[0].url,
            "https://www.google.com/about/careers/applications/jobs/results/"
            "119871476428874438-software-engineer",
        )
        self.assertEqual(result.candidates[0].raw["job_id"], "119871476428874438")
        self.assertEqual(result.trace["variant"], "server_rendered_search")
        self.assertIsNone(result.reason_code)

    def test_rejects_cross_domain_redirect_and_unsafe_candidate_urls(self):
        board = self.adapter.identify_board(
            "https://www.google.com/about/careers/applications/"
        )
        redirected = self.adapter.list_jobs(
            FixtureFetcher("", final_url="https://evil.example/jobs/results/"),
            board,
            JobQuery(title="Software Engineer"),
        )
        unsafe_html = (FIXTURES / "unsafe-results.html").read_text(encoding="utf-8")
        unsafe = self.adapter.list_jobs(
            FixtureFetcher(unsafe_html),
            board,
            JobQuery(title="Software Engineer"),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(unsafe.candidates, [])
        self.assertEqual(unsafe.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(len(unsafe.trace["rejected_urls"]), 3)

    def test_returns_standard_fetch_and_board_failures(self):
        board = self.adapter.identify_board(
            "https://www.google.com/about/careers/applications/"
        )
        failed = self.adapter.list_jobs(
            FixtureFetcher("", error=FetchError("blocked")),
            board,
            JobQuery(title="Software Engineer"),
        )
        unsupported = self.adapter.list_jobs(
            FixtureFetcher(""),
            JobBoard(
                url="https://www.google.com/search",
                provider="google_careers",
                identifier="www.google.com",
            ),
            JobQuery(),
        )

        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(unsupported.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
