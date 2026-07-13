import unittest

from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.providers.avature import ADAPTER, AvatureAdapter
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


HOST = "careers.example.com"
ROOT = f"https://{HOST}/en_US/careers"
BOARD = f"{ROOT}/SearchJobs"


def portal_html(body="", *, portal_id="7", language="en_US", portal="careers", page="SearchJobs"):
    return (
        f'<meta name="avature.portal.id" content="{portal_id}">'
        f'<meta name="avature.portal.lang" content="{language}">'
        f'<meta name="avature.portal.urlPath" content="{portal}">'
        f'<meta name="avature.portal.page" content="{page}">'
        f"{body}"
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


class AvatureAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AvatureAdapter()
        self.portal_page = Page(url=ROOT, html=portal_html(page=""))
        self.board = self.adapter.identify_board_from_page(self.portal_page)

    def test_native_page_aware_adapter_is_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["avature"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(self.adapter.recognizes(BOARD))
        self.assertIsNone(self.adapter.identify_board(BOARD))
        self.assertEqual(self.board.url, BOARD)
        self.assertEqual(self.board.identifier, f"{HOST}|en_US|careers")

    def test_requires_strong_portal_meta_and_matching_url_path(self):
        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page(url=ROOT, html=portal_html(portal_id="not-numeric"))
            )
        )
        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page(url=f"https://{HOST}/fr_FR/jobs", html=portal_html())
            )
        )

    def test_redirect_alias_can_use_same_host_search_route_evidence(self):
        alias = Page(
            url=f"https://{HOST}",
            html=portal_html(f'<form action="{BOARD}?sort=relevancy"></form>'),
        )

        self.assertEqual(self.adapter.identify_board_from_page(alias), self.board)

    def test_lists_title_filtered_jobs_and_normalizes_same_portal_details(self):
        search_url = f"{BOARD}?sort=relevancy&search=Agentic+AI+Engineer"
        html = portal_html(
            '<a href="/en_US/careers/JobDetail/Agentic-AI-Engineer/355577">Agentic AI Engineer</a>'
            '<a href="https://careers.example.com/en_US/careers/JobDetail/Data-Engineer/355578?src=search">Data Engineer</a>'
        )
        fetcher = RecordingFetcher({
            search_url: Page(url=search_url, html=html, source="avature-contract")
        })

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Agentic AI Engineer"))

        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(
            result.candidates[0].url,
            f"{ROOT}/JobDetail/Agentic-AI-Engineer/355577",
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")

    def test_rejects_cross_host_and_cross_portal_details(self):
        search_url = f"{BOARD}?sort=relevancy&search=Engineer"
        html = portal_html(
            '<a href="https://evil.example/en_US/careers/JobDetail/Engineer/1234">Wrong host</a>'
            '<a href="/fr_FR/jobs/JobDetail/Engineer/1234">Wrong portal</a>'
        )
        result = self.adapter.list_jobs(
            RecordingFetcher({search_url: Page(url=search_url, html=html)}),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["rejected_link_count"], 2)

    def test_rejects_redirect_and_returns_retryable_fetch_failure(self):
        search_url = f"{BOARD}?sort=relevancy&search=Engineer"
        redirected = self.adapter.list_jobs(
            RecordingFetcher({
                search_url: Page(
                    url=search_url,
                    final_url="https://other.example/en_US/careers/SearchJobs",
                    html=portal_html(),
                )
            }),
            self.board,
            JobQuery(title="Engineer"),
        )
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)

    def test_opening_matcher_detects_customer_domain_from_page(self):
        search_url = f"{BOARD}?sort=relevancy&search=Agentic+AI+Engineer"
        fetcher = RecordingFetcher({
            ROOT: self.portal_page,
            search_url: Page(
                url=search_url,
                html=portal_html(
                    '<a href="/en_US/careers/JobDetail/Agentic-AI-Engineer/355577">Agentic AI Engineer</a>'
                ),
            ),
        })

        match, trace = JobOpeningMatcher(fetcher).match(ROOT, "Agentic AI Engineer")

        self.assertEqual(match.url, f"{ROOT}/JobDetail/Agentic-AI-Engineer/355577")
        self.assertEqual(trace["provider"], "avature")


if __name__ == "__main__":
    unittest.main()
