import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.loxo import ADAPTER, LoxoAdapter
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://example-search.app.loxo.co/example-search"


def listing(*, query="Engineer", records="", empty=False):
    empty_html = (
        '<div class="jobs-listing-empty-state">No results found</div>' if empty else ""
    )
    return f"""
      <div>Job Openings</div>
      <form><input name="query" value="{query}"></form>
      {records}{empty_html}
      <div class="powered-by-loxo">Powered by Loxo</div>
    """


def record(title="Engineer", token="QUJDREVGR0g=", location="Austin, Texas"):
    return f"""
      <div class="jobs-listing-card">
        <div><a class="job-title" href="/job/{token}?disable_addthis=true">{title}</a></div>
        <div class="job-location">{location}</div>
      </div>
    """


class RecordingFetcher:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if isinstance(self.response, BaseException):
            raise self.response
        return Page(url, self.response, final_url=url, source="fixture-loxo")


class LoxoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LoxoAdapter()
        self.board = self.adapter.identify_board(BOARD_URL)

    def test_is_typed_and_canonicalizes_safe_board_variants(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertIsNotNone(self.board)
        for url in (
            BOARD_URL,
            BOARD_URL + "/",
            BOARD_URL + "?disable_addthis=true",
            BOARD_URL + "?query=Engineer&disable_addthis=true",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_unsafe_cross_tenant_and_non_board_urls(self):
        rejected = (
            "http://example-search.app.loxo.co/example-search",
            "https://user@example-search.app.loxo.co/example-search",
            "https://example-search.app.loxo.co:8443/example-search",
            "https://example-search.app.loxo.co/example/search",
            "https://example-search.app.loxo.co/job/QUJDREVGR0g=",
            "https://example-search.app.loxo.co/example-search?token=secret",
            "https://division.example-search.app.loxo.co/example-search",
            "https://example-search.app.loxo.co.evil.test/example-search",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_complete_title_filtered_inventory(self):
        fetcher = RecordingFetcher(
            listing(records=record("Software Engineer", location="New York, NY"))
        )
        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery("Engineer", "New York, NY")
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Software Engineer")
        self.assertEqual(candidate.location, "New York, NY")
        self.assertEqual(
            candidate.url,
            "https://example-search.app.loxo.co/job/QUJDREVGR0g=",
        )
        query = parse_qs(urlparse(fetcher.requests[0][0]).query)
        self.assertEqual(query["query"], ["Engineer"])
        self.assertEqual(query["disable_addthis"], ["true"])

    def test_accepts_explicit_complete_empty_filtered_inventory(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(listing(query="Software Engineer", empty=True)),
            self.board,
            JobQuery("Software Engineer"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])

    def test_fails_closed_on_query_identity_and_record_contradictions(self):
        cases = (
            listing(query="Other", empty=True),
            listing(records=record(token="short")),
            listing(records=record() + record()),
            listing(records=record()).replace("powered-by-loxo", "other"),
            listing(records=record()).replace("Job Openings", "Jobs"),
            listing(records=record().replace(
                "/job/QUJDREVGR0g=?disable_addthis=true",
                "https://other.app.loxo.co/job/QUJDREVGR0g=",
            )),
        )
        for html in cases:
            with self.subTest(html=html[:100]):
                result = self.adapter.list_jobs(
                    RecordingFetcher(html), self.board, JobQuery("Engineer")
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.candidates, [])

    def test_preserves_fetch_failure_without_claiming_no_match(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(FetchError("timed out")),
            self.board,
            JobQuery("Engineer"),
        )
        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.retryable)


if __name__ == "__main__":
    unittest.main()
