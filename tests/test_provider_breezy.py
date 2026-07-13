import unittest

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.breezy import ADAPTER, BreezyAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://velox.breezy.hr/"


def jobs_html() -> str:
    return (
        '<body class="breezy-portal">'
        '<a href="https://www.velox.com">Company website</a>'
        '<li class="position"><a href="/p/42dadbd6181a-artificial-ai-engineer">'
        '<h2>Artificial (AI) Engineer</h2>'
        '<li class="location"><span>Boise, ID</span></li></a></li>'
        '<div class="bzy-footer">Powered by Breezy</div>'
        "</body>"
    )


class RecordingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.requested = []

    def fetch(self, url, data=None, headers=None):
        self.requested.append(url)
        if self.error:
            raise self.error
        if url not in self.pages:
            raise FetchError(f"unexpected URL: {url}")
        return self.pages[url]


class BreezyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = BreezyAdapter()
        self.board = JobBoard(BOARD_URL, "breezy", "velox")

    def test_native_adapter_recognizes_and_canonicalizes_public_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["breezy"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (BOARD_URL, f"{BOARD_URL}p/42dadbd6181a-artificial-ai-engineer"):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)
        for url in (
            "http://velox.breezy.hr/",
            "https://breezy.hr/",
            "https://user@velox.breezy.hr/",
            "https://velox.breezy.hr:8443/",
            "https://velox.breezy.hr/about",
            "https://velox.breezy.hr.evil.example/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))

    def test_lists_jobs_with_title_and_location(self):
        result = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html=jobs_html())}),
            self.board,
            JobQuery(title="Artificial (AI) Engineer", location="Boise, ID"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Artificial (AI) Engineer")
        self.assertEqual(result.candidates[0].location, "Boise, ID")
        self.assertEqual(
            result.candidates[0].url,
            f"{BOARD_URL}p/42dadbd6181a-artificial-ai-engineer",
        )
        self.assertTrue(result.trace["exact_title_found"])

    def test_rejects_weak_html_and_returns_retryable_fetch_failure(self):
        weak = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html="<h1>Jobs</h1>")}),
            self.board,
            JobQuery(),
        )
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(weak.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)

    def test_derived_board_discovery_uses_adapter_inventory(self):
        homepage = "https://www.velox.com"
        fetcher = RecordingFetcher({
            homepage: Page(url=homepage, html="<html>VELOX</html>"),
            BOARD_URL: Page(url=BOARD_URL, html=jobs_html()),
        })

        career, trace = JobSourceAgent(
            fetcher,
            max_career_candidate_fetches=0,
            max_ats_board_fetches=5,
        ).find_career_page(
            homepage,
            company_name="VELOX",
            target_title="Artificial (AI) Engineer",
        )

        self.assertEqual(career, BOARD_URL)
        self.assertEqual(trace["selected_page_source"], "provider_adapter")


if __name__ == "__main__":
    unittest.main()
