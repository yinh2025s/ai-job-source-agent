import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.ceipal import ADAPTER, CeipalAdapter
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/find-jobs"
API_KEY = "tenant-api-key"
PORTAL_ID = "tenant-career-portal"
API_URL = (
    "https://careerapi.ceipal.com/careerPortalWidget/"
    "?themeid=&bgcolor=&job_id=&apikey=tenant-api-key&cp_id=tenant-career-portal"
)


def widget_html(
    *,
    src="https://jobsapi.ceipal.com/APISource/widget.js",
    api_key=API_KEY,
    portal_id=PORTAL_ID,
):
    return (
        f'<script src="{src}" data-ceipal-api-key="{api_key}" '
        f'data-ceipal-career-portal-id="{portal_id}"></script>'
    )


class RecordingFetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        if self.page is None:
            raise FetchError(f"unexpected URL: {url}")
        return self.page


class CeipalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CeipalAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL, html=widget_html())
        )
        self.assertIsNotNone(self.board)

    def test_native_page_aware_adapter_is_auto_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["ceipal"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(ADAPTER.recognizes("https://jobsapi.ceipal.com/APISource/widget.js"))
        self.assertIsNone(ADAPTER.identify_board(BOARD_URL))

    def test_identifies_active_widget_on_safe_first_party_page(self):
        page = Page(
            url="https://old.example.com/jobs",
            final_url=BOARD_URL + "?source=careers#openings",
            html=widget_html(),
        )
        selected = ProviderRegistry((self.adapter,)).board_for_page(page)

        self.assertIsNotNone(selected)
        self.assertIs(selected[0], self.adapter)
        board = selected[1]
        self.assertEqual(board.url, BOARD_URL)
        self.assertEqual(board.provider, "ceipal")
        self.assertEqual(
            json.loads(board.identifier),
            {
                "origin": "https://careers.example.com",
                "api_key": API_KEY,
                "career_portal_id": PORTAL_ID,
            },
        )

    def test_identifies_a_second_synthetic_tenant_without_cross_tenant_state(self):
        second_url = "https://jobs.second-example.test:443/careers/search?campaign=direct#jobs"
        board = self.adapter.identify_board_from_page(
            Page(
                url=second_url,
                html=(
                    "<!-- an inactive widget must not supply either tenant id -->"
                    + widget_html(
                        api_key="second-api-key",
                        portal_id="second-career-portal",
                    )
                ),
                source="synthetic-second-tenant",
            )
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.url, "https://jobs.second-example.test:443/careers/search")
        identity = json.loads(board.identifier)
        self.assertEqual(
            identity,
            {
                "origin": "https://jobs.second-example.test",
                "api_key": "second-api-key",
                "career_portal_id": "second-career-portal",
            },
        )
        self.assertNotEqual(board.identifier, self.board.identifier)

    def test_rejects_commented_or_inexact_or_split_widget_evidence(self):
        cases = [
            f"<!-- {widget_html()} -->",
            widget_html(src="http://jobsapi.ceipal.com/APISource/widget.js"),
            widget_html(src="https://jobsapi.ceipal.com/APISource/widget.js?v=1"),
            widget_html(src="https://jobsapi.ceipal.com/apisource/widget.js"),
            widget_html(api_key=""),
            widget_html(portal_id="   "),
            (
                '<script src="https://jobsapi.ceipal.com/APISource/widget.js" '
                f'data-ceipal-api-key="{API_KEY}"></script>'
                f'<script data-ceipal-career-portal-id="{PORTAL_ID}"></script>'
            ),
        ]

        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=html))
                )

    def test_rejects_unsafe_page_urls_and_ambiguous_tenants(self):
        unsafe_urls = [
            "http://careers.example.com/find-jobs",
            "https://user@careers.example.com/find-jobs",
            "https://careers.example.com:8443/find-jobs",
        ]
        for url in unsafe_urls:
            with self.subTest(url=url):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=url, html=widget_html()))
                )

        ambiguous = widget_html() + widget_html(api_key="other-key")
        self.assertIsNone(
            self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=ambiguous))
        )

    def test_requests_frozen_widget_endpoint_and_classifies_bot_block(self):
        response = Page(
            url=API_URL,
            html=json.dumps(
                {"status": 400, "success": 0, "message": "Bot access is not allowed"}
            ),
            source="frozen-ceipal-response",
        )
        fetcher = RecordingFetcher(page=response)

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI Engineer"))

        self.assertEqual(
            fetcher.requests,
            [
                (
                    API_URL,
                    None,
                    {
                        "Accept": "application/json",
                        "X-Referer-Host": "https://jobsapi.ceipal.com/",
                    },
                )
            ],
        )
        self.assertEqual(result.reason_code, "BOT_PROTECTION")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertNotIn(API_KEY, json.dumps(result.trace))
        self.assertFalse(result.trace["inventory_complete"])

    def test_unknown_success_schema_is_unsupported_and_never_constructs_jobs(self):
        response = Page(
            url=API_URL,
            html=json.dumps(
                {
                    "status": 200,
                    "success": 1,
                    "html": '<a href="/job/123">AI Engineer</a>',
                }
            ),
        )

        result = self.adapter.list_jobs(
            RecordingFetcher(page=response), self.board, JobQuery()
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertNotIn(API_KEY, json.dumps(result.trace))

    def test_rejects_identifier_origin_tampering_and_api_redirects(self):
        identity = json.loads(self.board.identifier)
        identity["origin"] = "https://other.example.com"
        tampered = JobBoard(
            self.board.url,
            "ceipal",
            json.dumps(identity, separators=(",", ":"), sort_keys=True),
        )
        invalid = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())
        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                page=Page(
                    url=API_URL,
                    final_url="https://evil.example/widget",
                    html='{"status": 400, "message": "Bot access is not allowed"}',
                )
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.inventory_scope, "unknown")
        self.assertFalse(invalid.inventory_complete)
        self.assertEqual(invalid.trace["inventory_scope"], "unknown")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(redirected.inventory_scope, "unknown")
        self.assertFalse(redirected.inventory_complete)
        self.assertEqual(redirected.trace["inventory_scope"], "unknown")

    def test_http_forbidden_fetch_failure_is_typed_and_nonretryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertNotIn(API_KEY, json.dumps(result.trace))

    def test_timeout_fetch_failure_is_typed_and_retryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertNotIn(API_KEY, json.dumps(result.trace))


if __name__ == "__main__":
    unittest.main()
