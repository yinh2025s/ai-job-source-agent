import unittest

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.jazzhr import ADAPTER, JazzHRAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://acme.applytojob.com/apply/jobs/"


def jobs_html(links=""):
    return (
        '<div id="resumator_main_wrapper">'
        '<form action="/apply/jobs" method="GET"></form>'
        f"{links}</div>"
    )


class RecordingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        if url not in self.pages:
            raise FetchError(f"unexpected URL: {url}")
        return self.pages[url]


class JazzHRAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = JazzHRAdapter()
        self.board = JobBoard(BOARD_URL, "jazzhr", "acme")

    def test_native_adapter_is_discovered_and_canonicalizes_public_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["jazzhr"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            BOARD_URL,
            "https://acme.applytojob.com/apply/jobs",
            "https://acme.applytojob.com/apply/jobs/details/Abc_123-xy?source=careers",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

        for url in (
            "http://acme.applytojob.com/apply/jobs/",
            "https://applytojob.com/apply/jobs/",
            "https://user@acme.applytojob.com/apply/jobs/",
            "https://acme.applytojob.com:8443/apply/jobs/",
            "https://acme.applytojob.com/about",
            "https://applytojob.com.evil.example/apply/jobs/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))

    def test_lists_public_jobs_and_normalizes_detail_urls(self):
        html = jobs_html(
            '<a class="job_title_link" href="/apply/jobs/details/9ZW2SJ880l?&">AI Programmer</a>'
            '<a class="job_title_link featured" href="https://acme.applytojob.com/apply/jobs/details/mv8Xr5KgTK/">Program Manager</a>'
        )
        fetcher = RecordingFetcher({
            BOARD_URL: Page(url=BOARD_URL, html=html, source="jazzhr-contract")
        })

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI Programmer"))

        self.assertIsNone(result.reason_code)
        self.assertEqual([candidate.title for candidate in result.candidates], ["AI Programmer", "Program Manager"])
        self.assertEqual(
            result.candidates[0].url,
            "https://acme.applytojob.com/apply/jobs/details/9ZW2SJ880l",
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["inventory_scope"], "full")

    def test_rejects_cross_tenant_and_non_detail_links(self):
        html = jobs_html(
            '<a class="job_title_link" href="https://other.applytojob.com/apply/jobs/details/Abcd1234">Wrong tenant</a>'
            '<a class="job_title_link" href="/apply/jobs">Not a detail</a>'
            '<a class="job_title_link" href="javascript:alert(1)">Script URL</a>'
        )
        result = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html=html)}),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["rejected_link_count"], 3)

    def test_rejects_cross_tenant_redirect_and_weak_page(self):
        redirected = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(
                    url=BOARD_URL,
                    final_url="https://other.applytojob.com/apply/jobs/",
                    html=jobs_html(),
                )
            }),
            self.board,
            JobQuery(),
        )
        weak = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html="<h1>Job Listings</h1>")}),
            self.board,
            JobQuery(),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(weak.reason_code, "INVALID_STRUCTURED_DATA")

    def test_returns_retryable_fetch_failure(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)

    def test_job_board_traversal_keeps_registry_backed_link(self):
        career_url = "https://company.example/careers"
        fetcher = RecordingFetcher({
            career_url: Page(
                url=career_url,
                html=f'<a href="{BOARD_URL}">View current openings</a>',
            ),
            BOARD_URL: Page(url=BOARD_URL, html=jobs_html()),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career_url)

        self.assertEqual(job_list, BOARD_URL)
        self.assertEqual(trace["provider"], "jazzhr")


if __name__ == "__main__":
    unittest.main()
