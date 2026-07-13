import unittest
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.domain import DomainProviderAdapter
from job_source_agent.providers.meta_careers import ADAPTER, MetaCareersAdapter
from job_source_agent.providers.registry import (
    build_default_provider_registry,
    discover_native_adapters,
)
from job_source_agent.web import FetchError, Page


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "samples" / "sites" / "js-heavy-rendered-cohort" / "meta" / "rendered.html"
BOARD_URL = "https://www.metacareers.com/jobsearch/"


class RecordingFetcher:
    def __init__(self, html="", final_url=None, error=None):
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
            source="meta-rendered-contract",
        )


class MetaCareersAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MetaCareersAdapter()
        self.board = JobBoard(BOARD_URL, "meta_careers", "meta")

    def test_native_adapter_is_auto_discovered_and_replaces_domain_adapter(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        selected = build_default_provider_registry().adapter_for(
            "https://metacareers.com/jobs/"
        )

        self.assertIs(native["meta_careers"], ADAPTER)
        self.assertIs(selected, ADAPTER)
        self.assertIsInstance(selected, ProviderAdapter)
        self.assertNotIsInstance(selected, DomainProviderAdapter)
        self.assertTrue(selected.supports_listing)

    def test_recognizes_only_safe_supported_routes_and_canonicalizes_board(self):
        valid = (
            "https://metacareers.com/jobs",
            "https://www.metacareers.com/jobsearch/?q=engineer",
            "https://www.metacareers.com:443/profile/job_details/123456?source=jobs",
        )
        invalid = (
            "http://www.metacareers.com/jobs",
            "https://jobs.metacareers.com/jobs",
            "https://www.metacareers.com.evil.example/jobs",
            "https://evil@www.metacareers.com/jobs",
            "https://user:secret@www.metacareers.com/jobsearch/",
            "https://www.metacareers.com:8443/jobsearch/",
            "https://www.metacareers.com/jobs//",
            "https://www.metacareers.com/profile/job_details/not-numeric",
            "https://www.metacareers.com/profile/job_details/123//",
            "https://www.metacareers.com/profile/job_details/123/extra",
            "https://www.metacareers.com/careers",
        )

        for url in valid:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_saved_rendered_candidates_with_locations_and_partial_inventory(self):
        fetcher = RecordingFetcher(FIXTURE.read_text(encoding="utf-8"))

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Data Scientist, Product Analytics"),
        )

        self.assertEqual(
            fetcher.requested_urls,
            [
                "https://www.metacareers.com/jobsearch/"
                "?q=Data+Scientist%2C+Product+Analytics"
            ],
        )
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            [
                "Critical Facility Engineer",
                "Data Scientist, Product Analytics",
                "Product Manager",
            ],
        )
        self.assertEqual(
            result.candidates[1].url,
            "https://www.metacareers.com/profile/job_details/1070147800777577",
        )
        self.assertEqual(
            result.candidates[1].location,
            "Sunnyvale, CA +9 locations - Data Science",
        )
        self.assertEqual(result.candidates[1].raw["job_id"], "1070147800777577")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "visible_page")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "visible_page")
        self.assertFalse(result.trace["inventory_complete"])

    def test_dedupes_canonical_details_and_keeps_title_association_inside_link(self):
        html = """
            <div>Borrowed title outside link</div>
            <a href="/profile/job_details/123?ref=one">
              <div><h3>Software Engineer</h3><span>Menlo Park, CA</span></div>
            </a>
            <a href="https://metacareers.com/profile/job_details/123/">
              Duplicate title <span>Remote</span>
            </a>
            <a href="/profile/job_details/456"><span>Location only</span></a>
        """

        result = self.adapter.list_jobs(
            RecordingFetcher(html), self.board, JobQuery(title="Software Engineer")
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Software Engineer")
        self.assertEqual(result.candidates[0].location, "Menlo Park, CA")
        self.assertEqual(
            result.candidates[0].url,
            "https://www.metacareers.com/profile/job_details/123",
        )
        self.assertEqual(result.trace["rejected_link_count"], 1)

    def test_empty_shell_login_challenge_and_400_like_pages_are_unsupported(self):
        variants = (
            "<html><main id='root'></main><script src='/bundle.js'></script></html>",
            "<h1>Log in to continue</h1><form><input type='password'></form>",
            "<title>Security challenge</title><p>Verify you are human</p>",
            "<h1>400 Bad Request</h1>",
        )

        for html in variants:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(html), self.board, JobQuery(title="Engineer")
                )
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.retryable)
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.inventory_scope, "unknown")
                self.assertNotIn("EMPTY_PROVIDER_RESPONSE", repr(result.trace))

    def test_retries_one_browser_shell_before_accepting_rendered_evidence(self):
        rendered = FIXTURE.read_text(encoding="utf-8")

        class HydratingFetcher:
            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                html = "<html><main id='root'></main></html>" if self.calls == 1 else rendered
                return Page(
                    url=url,
                    final_url=url,
                    html=html,
                    source="browser",
                )

        fetcher = HydratingFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Product Manager"),
        )

        self.assertEqual(fetcher.calls, 2)
        self.assertEqual(result.trace["attempt_count"], 2)
        self.assertEqual(result.candidates[-1].title, "Product Manager")
        self.assertFalse(result.inventory_complete)

    def test_rejects_redirects_invalid_board_and_sanitizes_trace(self):
        unsafe_url = "https://user:secret@evil.example/jobsearch/?token=private"
        redirected = self.adapter.list_jobs(
            RecordingFetcher(final_url=unsafe_url),
            self.board,
            JobQuery(title="Engineer"),
        )
        wrong_meta_host = self.adapter.list_jobs(
            RecordingFetcher(final_url="https://metacareers.com/jobsearch/?q=Engineer"),
            self.board,
            JobQuery(title="Engineer"),
        )
        invalid_board = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard("https://metacareers.com/jobsearch/", "meta_careers", "meta"),
            JobQuery(),
        )

        for result in (redirected, wrong_meta_host, invalid_board):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertNotIn("secret", repr(redirected.trace))
        self.assertNotIn("private", repr(redirected.trace))
        self.assertNotIn("evil.example", repr(redirected.trace))

    def test_rejects_unsafe_navigation_and_cross_host_detail_links(self):
        html = """
            <a href="/jobs">Jobs navigation</a>
            <a href="javascript:alert(1)">Script job</a>
            <a href="https://evil.example/profile/job_details/123">Cross host</a>
            <a href="https://www.metacareers.com:8443/profile/job_details/234">Bad port</a>
            <a href="https://user@www.metacareers.com/profile/job_details/345">Credentials</a>
            <a href="/profile/job_details/not-a-number">Malformed ID</a>
        """

        result = self.adapter.list_jobs(
            RecordingFetcher(html), self.board, JobQuery(title="Engineer")
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.trace["rejected_link_count"], 6)
        self.assertNotIn("evil.example", repr(result.trace))

    def test_fetch_failure_is_retryable_and_does_not_leak_error_text(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("token=private timeout")),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["error_type"], "FetchError")
        self.assertNotIn("private", repr(result.trace))


if __name__ == "__main__":
    unittest.main()
