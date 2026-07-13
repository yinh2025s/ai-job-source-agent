import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.providers.taleo import ADAPTER, TaleoAdapter
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://jobs.example.com/careersection/ex/jobsearch.ftl"
API_URL = "https://jobs.example.com/careersection/rest/jobboard/searchjobs?lang=en&portal=12345"


def shell_html(portal="12345", code="ex"):
    return f"""
    <script>var require = {{config: {{'fs/FacetedSearchSettings': {{
      lang: 'en', portalNo: '{portal}', urlCode: '{code}'
    }}}}}};</script>
    <script data-main="/careersection/v/js/facetedsearch/FacetedSearchPage.js"></script>
    """


def search_json(jobs, total=None, page=1):
    return json.dumps({
        "careerSectionUnAvailable": False,
        "requisitionList": jobs,
        "pagingData": {"currentPageNo": page, "pageSize": 25, "totalCount": len(jobs) if total is None else total},
        "queryString": "",
    })


def job(job_id, title, location='["Florida-Tampa"]'):
    return {"jobId": job_id, "contestNo": job_id, "column": [title, location, "Jul 13, 2026"]}


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


class TaleoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TaleoAdapter()
        self.board = JobBoard(BOARD_URL, "taleo", "jobs.example.com|ex")

    def test_native_adapter_is_discovered_and_canonicalizes_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        self.assertIs(native["taleo"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (BOARD_URL, "https://jobs.example.com/careersection/ex/jobdetail.ftl?job=42"):
            self.assertTrue(self.adapter.recognizes(url))
            self.assertEqual(self.adapter.identify_board(url), self.board)
        for url in (
            "http://jobs.example.com/careersection/ex/jobsearch.ftl",
            "https://user@jobs.example.com/careersection/ex/jobsearch.ftl",
            "https://jobs.example.com:8443/careersection/ex/jobsearch.ftl",
            "https://jobs.example.com/careersection/ex/profile.jss",
        ):
            self.assertFalse(self.adapter.recognizes(url))

    def test_lists_filtered_inventory_and_builds_canonical_detail(self):
        fetcher = RecordingFetcher({
            BOARD_URL: Page(url=BOARD_URL, html=shell_html(), source="taleo-contract"),
            API_URL: Page(url=API_URL, html=search_json([job("260445", "AI Engineer")], total=1)),
        })
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI Engineer", location="Tampa"))

        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].title, "AI Engineer")
        self.assertEqual(result.candidates[0].location, "Florida-Tampa")
        self.assertEqual(result.candidates[0].url, "https://jobs.example.com/careersection/ex/jobdetail.ftl?job=260445")
        payload = json.loads(fetcher.requests[1][1])
        self.assertEqual(payload["fieldData"]["fields"], {"KEYWORD": "AI Engineer", "LOCATION": "Tampa"})
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertNotIn("csrftoken", json.dumps(result.trace))

    def test_paginates_and_stops_on_exact_title(self):
        first = [job(str(index + 1), f"Engineer {index}") for index in range(25)]

        class PagedFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                page = json.loads(data)["pageNo"]
                jobs = first if page == 1 else [job("999", "AI Engineer")]
                return Page(url=url, html=search_json(jobs, total=26, page=page))

        fetcher = PagedFetcher()
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI Engineer"))
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(result.candidates[-1].title, "AI Engineer")

    def test_empty_filtered_inventory_is_verified_empty_response(self):
        fetcher = RecordingFetcher({
            BOARD_URL: Page(url=BOARD_URL, html=shell_html()),
            API_URL: Page(url=API_URL, html=search_json([], total=0)),
        })
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Missing Role"))
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["total_found"], 0)
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")

    def test_rejects_wrong_config_and_cross_tenant_redirect(self):
        wrong = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html=shell_html(code="internal"))}),
            self.board,
            JobQuery(),
        )
        redirected = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, final_url="https://evil.example/careersection/ex/jobsearch.ftl", html=shell_html())}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(wrong.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_returns_structured_fetch_and_json_failures(self):
        failed = self.adapter.list_jobs(RecordingFetcher(error=FetchError("timeout")), self.board, JobQuery())
        malformed = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html=shell_html()), API_URL: Page(url=API_URL, html="not-json")}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")

    def test_reports_unavailable_career_section_as_unsupported(self):
        unavailable = json.dumps({
            "careerSectionUnAvailable": True,
            "requisitionList": [],
            "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 0},
        })
        result = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(url=BOARD_URL, html=shell_html()),
                API_URL: Page(url=API_URL, html=unavailable),
            }),
            self.board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
