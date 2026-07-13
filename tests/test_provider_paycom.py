import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.paycom import ADAPTER, PaycomAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


CLIENT_KEY = "AA674B442E9B6A1284BD7F78CB0C3E73"
BOARD_URL = f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page"
API_URL = (
    "https://portal-applicant-tracking.us-cent.paycomonline.net/"
    "api/ats/job-posting-previews/search"
)


def portal_html(*, token="fixture-session-token", service_url=None):
    service_url = service_url or "https://portal-applicant-tracking.us-cent.paycomonline.net/"
    config = {
        "sessionJWT": token,
        "libConfig": json.dumps({"atsPortalMantleServiceUrl": service_url}),
    }
    return f"<script>var configsFromHost = {json.dumps(config)};</script>"


def response_html(jobs, count=None):
    return json.dumps({
        "jobPostingPreviews": jobs,
        "jobPostingPreviewsCount": len(jobs) if count is None else count,
    })


class RecordingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        page = self.pages.get(url)
        if page is None:
            raise FetchError(f"unexpected URL: {url}")
        return page


class PaycomAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PaycomAdapter()
        self.board = JobBoard(BOARD_URL, "paycom", CLIENT_KEY)

    def test_native_adapter_is_discovered_and_canonicalizes_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["paycom"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            BOARD_URL,
            f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs/247738",
            f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={CLIENT_KEY}&session_nonce=ignored",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

        self.assertFalse(self.adapter.recognizes("http://www.paycomonline.net/v4/ats/web.php/jobs"))
        self.assertFalse(self.adapter.recognizes("https://evil.example/v4/ats/web.php/jobs"))
        self.assertFalse(
            self.adapter.recognizes(
                f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/candidate-home"
            )
        )

    def test_lists_title_filtered_jobs_without_exposing_session_token(self):
        jobs = [{
            "jobId": 247738,
            "jobTitle": "AI/ML Engineer",
            "locations": "Aventura, FL",
            "positionType": "Full Time",
            "remoteType": "",
        }]
        fetcher = RecordingFetcher({
            BOARD_URL: Page(url=BOARD_URL, html=portal_html(), source="paycom-contract"),
            API_URL: Page(url=API_URL, html=response_html(jobs)),
        })

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI/ML Engineer"))

        self.assertEqual(result.reason_code, None)
        self.assertEqual(result.candidates[0].title, "AI/ML Engineer")
        self.assertEqual(
            result.candidates[0].url,
            f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs/247738",
        )
        self.assertEqual(result.candidates[0].location, "Aventura, FL")
        request = json.loads(fetcher.requests[1][1])
        self.assertEqual(request["filtersForQuery"]["keywordSearchText"], "AI/ML Engineer")
        self.assertEqual(fetcher.requests[1][2]["Authorization"], "fixture-session-token")
        self.assertNotIn("fixture-session-token", json.dumps(result.trace))
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])

    def test_paginates_with_bound_and_stops_on_exact_title(self):
        first = [{"jobId": index + 1, "jobTitle": f"Engineer {index}"} for index in range(20)]
        second = [{"jobId": 999, "jobTitle": "AI/ML Engineer"}]

        class PagedFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=portal_html())
                skip = json.loads(data)["skip"]
                jobs = first if skip == 0 else second
                return Page(url=url, html=response_html(jobs, count=41))

        fetcher = PagedFetcher()
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI/ML Engineer"))

        self.assertEqual([json.loads(item[1])["skip"] for item in fetcher.requests[1:]], [0, 20])
        self.assertEqual(result.candidates[-1].title, "AI/ML Engineer")
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_rejects_unsafe_config_redirects_and_invalid_responses(self):
        unsafe = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(
                    url=BOARD_URL,
                    html=portal_html(service_url="https://evil.example/"),
                )
            }),
            self.board,
            JobQuery(),
        )
        redirected = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(url=BOARD_URL, html=portal_html()),
                API_URL: Page(url=API_URL, final_url="https://evil.example/jobs", html="{}"),
            }),
            self.board,
            JobQuery(),
        )
        invalid = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(url=BOARD_URL, html=portal_html()),
                API_URL: Page(url=API_URL, html="not-json"),
            }),
            self.board,
            JobQuery(),
        )
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("blocked")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(unsafe.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)


if __name__ == "__main__":
    unittest.main()
