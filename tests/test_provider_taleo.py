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
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])

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
        self.assertTrue(result.inventory_complete)

    def test_rejects_missing_or_contradictory_paging_metadata(self):
        cases = [
            {"requisitionList": [], "pagingData": {}},
            {
                "requisitionList": [job("1", "AI Engineer")],
                "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 0},
            },
            {
                "requisitionList": [job("1", "AI Engineer")],
                "pagingData": {"currentPageNo": 2, "pageSize": 25, "totalCount": 1},
            },
            {
                "requisitionList": [job("1", "AI Engineer")],
                "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 2},
            },
        ]
        for body in cases:
            with self.subTest(body=body):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        {
                            BOARD_URL: Page(url=BOARD_URL, html=shell_html()),
                            API_URL: Page(url=API_URL, html=json.dumps(body)),
                        }
                    ),
                    self.board,
                    JobQuery(title="AI Engineer"),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_page_size_changes_across_pages(self):
        first_page = [job(str(index + 1), f"Engineer {index}") for index in range(25)]

        class DriftingPageSizeFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                page_no = json.loads(data)["pageNo"]
                jobs = first_page if page_no == 1 else []
                page_size = 25 if page_no == 1 else 50
                return Page(
                    url=url,
                    html=json.dumps(
                        {
                            "requisitionList": jobs,
                            "pagingData": {
                                "currentPageNo": page_no,
                                "pageSize": page_size,
                                "totalCount": 50,
                            },
                        }
                    ),
                )

        result = self.adapter.list_jobs(
            DriftingPageSizeFetcher(),
            self.board,
            JobQuery(title="Missing Role"),
        )

        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.inventory_complete)

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
        self.assertFalse(failed.inventory_complete)
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(malformed.inventory_complete)

    def test_server_error_with_text_location_retries_title_only(self):
        class LocationFallbackFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                payload = json.loads(data)
                if payload["fieldData"]["fields"]["LOCATION"]:
                    raise FetchError(
                        "HTTP Error 500: Internal Server Error",
                        status=500,
                        reason_code="SERVER_ERROR",
                        retryable=True,
                    )
                return Page(
                    url=url,
                    html=search_json([job("260445", "AI Engineer")], total=1),
                )

        fetcher = LocationFallbackFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer", location="New York, NY"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual([candidate.title for candidate in result.candidates], ["AI Engineer"])
        post_payloads = [json.loads(request[1]) for request in fetcher.requests[1:]]
        self.assertEqual(
            [payload["fieldData"]["fields"]["LOCATION"] for payload in post_payloads],
            ["New York, NY", ""],
        )
        self.assertTrue(result.trace["location_filter_fallback"])
        self.assertEqual(
            result.trace["request_variants"],
            ["title_and_location", "title_only"],
        )

    def test_location_fallback_failure_remains_retryable(self):
        class AlwaysFailingSearchFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                raise FetchError(
                    "HTTP Error 500: Internal Server Error",
                    status=500,
                    reason_code="SERVER_ERROR",
                    retryable=True,
                )

        fetcher = AlwaysFailingSearchFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer", location="New York, NY"),
        )

        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.trace["location_filter_fallback"])

    def test_location_fallback_remains_title_only_across_pagination(self):
        first_page = [job(str(index + 1), f"Engineer {index}") for index in range(25)]

        class PagedLocationFallbackFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                payload = json.loads(data)
                fields = payload["fieldData"]["fields"]
                if fields["LOCATION"]:
                    raise FetchError(
                        "HTTP Error 500: Internal Server Error",
                        status=500,
                        reason_code="SERVER_ERROR",
                        retryable=True,
                    )
                page_no = payload["pageNo"]
                jobs = first_page if page_no == 1 else [job("999", "AI Engineer")]
                return Page(url=url, html=search_json(jobs, total=26, page=page_no))

        fetcher = PagedLocationFallbackFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer", location="New York, NY"),
        )

        post_payloads = [json.loads(request[1]) for request in fetcher.requests[1:]]
        self.assertEqual(
            [payload["fieldData"]["fields"]["LOCATION"] for payload in post_payloads],
            ["New York, NY", "", ""],
        )
        self.assertEqual([payload["pageNo"] for payload in post_payloads], [1, 1, 2])
        self.assertEqual(result.candidates[-1].title, "AI Engineer")
        self.assertEqual(
            result.trace["request_variants"],
            ["title_and_location", "title_only", "title_only"],
        )

    def test_non_server_failure_does_not_relax_location_filter(self):
        class ForbiddenSearchFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                raise FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                )

        fetcher = ForbiddenSearchFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer", location="New York, NY"),
        )

        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertFalse(result.retryable)
        self.assertFalse(result.trace["location_filter_fallback"])

    def test_whitespace_location_and_conflicting_status_do_not_trigger_fallback(self):
        class ConflictingFailureFetcher(RecordingFetcher):
            def fetch(inner, url, data=None, headers=None):
                inner.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=shell_html())
                raise FetchError(
                    "server error",
                    status=403,
                    reason_code="SERVER_ERROR",
                    retryable=False,
                )

        for location in ("   ", "New York, NY"):
            with self.subTest(location=location):
                fetcher = ConflictingFailureFetcher()
                result = self.adapter.list_jobs(
                    fetcher,
                    self.board,
                    JobQuery(title="AI Engineer", location=location),
                )
                self.assertEqual(len(fetcher.requests), 2)
                self.assertFalse(result.trace["location_filter_fallback"])
                self.assertFalse(result.retryable)

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
