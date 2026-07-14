import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.providers.talemetry import ADAPTER, TalemetryAdapter
from job_source_agent.web import FetchError, Page


BOARD_PAGE = "https://careers.example.com/openings?source=site#jobs"
BOARD_URL = "https://careers.example.com/"
API_URL = "https://careers.example.com/search/jobs.json?q=AI+Engineer+II"


def talemetry_html(*, career_site_id="4276", asset_host="careers.example.com"):
    identity = ""
    if career_site_id is not None:
        identity = f',"careerSite":{{"id":"{career_site_id}"}}'
    return f"""
        <script src="//{asset_host}/pack/talemetry_careersites/index.js"></script>
        <script>window.talemetry = window.talemetry || {{}};</script>
        <script>
          window.csns.paths = {{
            "search_jobs_json":"/search/jobs.json",
            "job":"/jobs/:id"
          }};
          CareerSite.Path.configure({{
            "search_jobs_json":"/search/jobs.json",
            "job":"/jobs/:id"{identity}
          }});
        </script>
    """


class RecordingFetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if self.page is None:
            raise FetchError(f"unexpected URL: {url}")
        return self.page


class TalemetryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TalemetryAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_PAGE, html=talemetry_html())
        )
        self.assertIsNotNone(self.board)

    def test_native_page_aware_adapter_is_auto_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["talemetry"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(ADAPTER.supports_listing)
        self.assertFalse(ADAPTER.recognizes(BOARD_PAGE))
        self.assertIsNone(ADAPTER.identify_board(BOARD_PAGE))

    def test_identifies_strong_fingerprint_and_canonicalizes_first_party_board(self):
        selected = ProviderRegistry((self.adapter,)).board_for_page(
            Page(
                url="https://old.example.com/jobs",
                final_url=BOARD_PAGE,
                html=talemetry_html(),
            )
        )

        self.assertIsNotNone(selected)
        self.assertIs(selected[0], self.adapter)
        board = selected[1]
        self.assertEqual(board.url, BOARD_URL)
        self.assertEqual(board.provider, "talemetry")
        self.assertEqual(
            json.loads(board.identifier),
            {"host": "careers.example.com", "career_site_id": "4276"},
        )

    def test_recognizes_second_synthetic_tenant_with_generic_fingerprint(self):
        host = "jobs.second-tenant.example"
        board = self.adapter.identify_board_from_page(
            Page(
                url=f"https://{host}/department/engineering?source=website",
                html=talemetry_html(career_site_id="9001", asset_host=host),
                source="synthetic-second-tenant",
            )
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.url, f"https://{host}/")
        self.assertEqual(
            json.loads(board.identifier),
            {"host": host, "career_site_id": "9001"},
        )

    def test_rejects_weak_or_commented_fingerprints(self):
        cases = (
            '<script src="https://apply.app.jobvite.com/assets/app/apply.js"></script>',
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
            '<script src="/pack/talemetry_careersites/index.js"></script>',
            '<script>window.talemetry={}; CareerSite.Path.configure({});</script>',
            (
                '<script>CareerSite.Path.configure({'
                '"search_jobs_json":"/search/jobs.json","job":"/jobs/:id"});</script>'
            ),
            f"<!-- {talemetry_html()} -->",
            talemetry_html() + talemetry_html(career_site_id="9999"),
        )
        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=html))
                )

    def test_rejects_unsafe_urls_and_omits_unobserved_career_site_id(self):
        for url in (
            "http://careers.example.com/jobs",
            "https://user@careers.example.com/jobs",
            "https://careers.example.com:8443/jobs",
        ):
            with self.subTest(url=url):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=url, html=talemetry_html())
                    )
                )

        board = self.adapter.identify_board_from_page(
            Page(url=BOARD_PAGE, html=talemetry_html(career_site_id=None))
        )
        self.assertEqual(json.loads(board.identifier), {"host": "careers.example.com"})

    def test_requests_only_same_origin_search_json_with_title_query(self):
        fetcher = RecordingFetcher(page=Page(url=API_URL, html='{"jobs":[]}'))

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="AI Engineer II", location="Chicago")
        )

        self.assertEqual(
            fetcher.requests,
            [(API_URL, None, {"Accept": "application/json"})],
        )
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertFalse(result.trace["inventory_complete"])
        self.assertEqual(result.candidates, [])

    def test_classifies_fetch_403_as_forbidden_and_incomplete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
            self.board,
            JobQuery(title="AI Engineer II"),
        )

        self.assertEqual(result.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertEqual(result.candidates, [])

    def test_preserves_typed_missing_fixture_and_timeout_failures(self):
        cases = (
            (
                FetchError("No fixture found for https://careers.example.com/search/jobs.json"),
                "OFFLINE_FIXTURE_MISSING",
                False,
            ),
            (FetchError("The read operation timed out"), "NETWORK_TIMEOUT", True),
        )
        for error, reason_code, retryable in cases:
            with self.subTest(reason_code=reason_code):
                result = self.adapter.list_jobs(
                    RecordingFetcher(error=error),
                    self.board,
                    JobQuery(title="AI Engineer II"),
                )

                self.assertEqual(result.reason_code, reason_code)
                self.assertEqual(result.retryable, retryable)
                self.assertEqual(result.inventory_scope, "unknown")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.trace["inventory_scope"], "unknown")
                self.assertFalse(result.trace["inventory_complete"])
                self.assertEqual(result.candidates, [])

    def test_classifies_cloudflare_challenge_html_as_bot_protection(self):
        challenge = """
            <!doctype html><title>Just a moment...</title>
            <script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=1"></script>
            <div>Cloudflare Ray ID: abc123</div>
        """
        result = self.adapter.list_jobs(
            RecordingFetcher(page=Page(url=API_URL, html=challenge)),
            self.board,
            JobQuery(title="AI Engineer II"),
        )

        self.assertEqual(result.reason_code, "BOT_PROTECTION")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "unknown")
        self.assertEqual(result.candidates, [])

    def test_unknown_json_variants_never_create_candidates_or_claim_empty(self):
        variants = (
            [],
            {"jobs": []},
            {"results": [{"title": "AI Engineer II", "id": "123"}]},
        )
        for payload in variants:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(
                    RecordingFetcher(page=Page(url=API_URL, html=json.dumps(payload))),
                    self.board,
                    JobQuery(title="AI Engineer II"),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertNotEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
                self.assertEqual(result.inventory_scope, "unknown")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.trace["inventory_scope"], "unknown")
                self.assertEqual(result.candidates, [])

    def test_rejects_identifier_tampering_and_cross_origin_response(self):
        identity = json.loads(self.board.identifier)
        identity["host"] = "other.example.com"
        tampered = JobBoard(
            url=self.board.url,
            provider="talemetry",
            identifier=json.dumps(identity, separators=(",", ":"), sort_keys=True),
        )
        invalid = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())
        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                page=Page(
                    url=API_URL,
                    final_url="https://evil.example/search/jobs.json?q=AI+Engineer+II",
                    html='{"jobs":[]}',
                )
            ),
            self.board,
            JobQuery(title="AI Engineer II"),
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.inventory_scope, "unknown")
        self.assertEqual(redirected.inventory_scope, "unknown")
        self.assertFalse(invalid.inventory_complete)
        self.assertFalse(redirected.inventory_complete)
        self.assertEqual(invalid.trace["inventory_scope"], "unknown")
        self.assertEqual(redirected.trace["inventory_scope"], "unknown")


if __name__ == "__main__":
    unittest.main()
