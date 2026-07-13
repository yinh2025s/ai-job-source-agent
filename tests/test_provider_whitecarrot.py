import json
import unittest
from pathlib import Path

from job_source_agent.providers.base import JobQuery
from job_source_agent.providers.whitecarrot import WhiteCarrotAdapter
from job_source_agent.web import FetchError, Page


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "samples"
    / "sites"
    / "app.whitecarrot.io"
    / "api"
    / "careers"
    / "acme"
    / "index.html"
)
CUSTOM_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "samples"
    / "sites"
    / "careers.whitecarrot.ai"
    / "jobs"
    / "index.html"
)


class StubFetcher:
    def __init__(self, body="", *, error=None, final_url=None):
        self.body = body
        self.error = error
        self.final_url = final_url
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append({"url": url, "data": data, "headers": headers or {}})
        if self.error:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.body,
            source="whitecarrot-fixture",
        )


class RoutingFetcher:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append({"url": url, "data": data, "headers": headers or {}})
        response = self.pages.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        return Page(url=url, final_url=url, html=response, source="whitecarrot-fixture")


class WhiteCarrotAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WhiteCarrotAdapter()

    def test_recognizes_supported_board_and_detail_urls(self):
        supported = (
            "https://app.whitecarrot.io/careers/acme",
            "https://app.whitecarrot.io/careers/acme/",
            "https://app.whitecarrot.io/careers/acme/job/11111111-1111-4111-8111-111111111111/",
            "https://app.whitecarrot.io/share/careers/acme",
            "https://app.whitecarrot.io/share/careers/acme/job/11111111-1111-4111-8111-111111111111/",
            "https://acme.whitecarrot.ai/jobs",
            "https://acme.whitecarrot.ai/jobs/11111111-1111-4111-8111-111111111111",
        )

        for url in supported:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))

    def test_rejects_unsafe_or_unrelated_board_urls(self):
        rejected = (
            "https://user@app.whitecarrot.io/careers/acme",
            "https://app.whitecarrot.io:bad/careers/acme",
            "https://app.whitecarrot.io:8443/careers/acme",
            "https://app.whitecarrot.io.example.com/careers/acme",
            "https://evil.example/careers/acme",
            "ftp://app.whitecarrot.io/careers/acme",
            "http://app.whitecarrot.io/careers/acme",
            "https://app.whitecarrot.io/careers/bad.slug",
            "https://app.whitecarrot.io/careers/-bad",
            "https://app.whitecarrot.io/careers/acme/other",
            "https://acme.whitecarrot.ai/profile-builder/11111111-1111-4111-8111-111111111111",
            "https://bad.slug.whitecarrot.ai/jobs",
            "https://whitecarrot.ai/jobs",
        )

        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_identifies_canonical_board_from_each_supported_url_shape(self):
        urls = (
            "https://app.whitecarrot.io/careers/acme?utm_source=test",
            "https://app.whitecarrot.io/careers/acme/job/11111111-1111-4111-8111-111111111111/",
            "https://app.whitecarrot.io/share/careers/acme",
            "https://app.whitecarrot.io/share/careers/acme/job/11111111-1111-4111-8111-111111111111/",
        )

        for url in urls:
            with self.subTest(url=url):
                board = self.adapter.identify_board(url)
                self.assertIsNotNone(board)
                self.assertEqual(board.url, "https://app.whitecarrot.io/careers/acme")
                self.assertEqual(board.provider, "whitecarrot")
                self.assertEqual(board.identifier, "acme")

    def test_custom_hosts_canonicalize_same_origin_without_tenant_inference(self):
        urls = (
            "https://acme.whitecarrot.ai/jobs",
            "https://acme.whitecarrot.ai/jobs/11111111-1111-4111-8111-111111111111?source=linkedin",
            "https://careers.whitecarrot.ai/jobs",
            "https://careers.whitecarrot.ai/jobs/11111111-1111-4111-8111-111111111111",
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                board = self.adapter.identify_board(url)
                self.assertIsNotNone(board)
                expected_host = "careers.whitecarrot.ai" if "//careers." in url else "acme.whitecarrot.ai"
                self.assertEqual(board.url, f"https://{expected_host}/jobs")
                self.assertEqual(board.provider, "whitecarrot")
                self.assertEqual(board.identifier, f"host:{expected_host}")

    def test_requests_api_once_with_contract_headers_and_lists_published_jobs(self):
        fetcher = StubFetcher(FIXTURE.read_text(encoding="utf-8"))
        board = self.adapter.identify_board("https://app.whitecarrot.io/careers/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(fetcher.requests), 1)
        request = fetcher.requests[0]
        self.assertEqual(request["url"], "https://app.whitecarrot.io/api/careers/acme")
        self.assertIsNone(request["data"])
        headers = {key.lower(): value for key, value in request["headers"].items()}
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["x-app-version"], "2.0.33")
        self.assertEqual(
            [(candidate.title, candidate.location, candidate.provider, candidate.url) for candidate in result.candidates],
            [
                (
                    "Machine Learning Engineer",
                    "Bengaluru, India",
                    "whitecarrot",
                    "https://acme.whitecarrot.ai/jobs/11111111-1111-4111-8111-111111111111",
                ),
                (
                    "Platform Engineer",
                    "London, United Kingdom",
                    "whitecarrot",
                    "https://acme.whitecarrot.ai/jobs/22222222-2222-4222-8222-222222222222",
                ),
            ],
        )
        self.assertEqual(
            [candidate.raw for candidate in result.candidates],
            [
                {"id": "11111111-1111-4111-8111-111111111111", "status": "PUBLISHED"},
                {"id": "22222222-2222-4222-8222-222222222222", "status": "PUBLISHED"},
            ],
        )
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.inventory_complete)

    def test_lists_custom_ssr_jobs_from_strong_same_origin_items(self):
        fetcher = RoutingFetcher([CUSTOM_FIXTURE.read_text(encoding="utf-8")])
        board = self.adapter.identify_board("https://careers.whitecarrot.ai/jobs")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual([request["url"] for request in fetcher.requests], [board.url])
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Staff Backend Engineer")
        self.assertEqual(candidate.provider, "whitecarrot")
        self.assertEqual(
            candidate.url,
            "https://careers.whitecarrot.ai/jobs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        self.assertEqual(
            candidate.raw,
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "status": "PUBLISHED"},
        )
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.inventory_complete)

    def test_custom_ssr_without_strong_marker_or_empty_evidence_is_invalid(self):
        weak_pages = (
            '<a href="/jobs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa">Backend Engineer</a>',
            "<!doctype html><html><body><main></main></body></html>",
        )

        for html in weak_pages:
            with self.subTest(html=html):
                board = self.adapter.identify_board("https://careers.whitecarrot.ai/jobs")
                result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_requires_nonempty_title_and_exact_tenant_uuid_link(self):
        valid_id = "77777777-7777-4777-8777-777777777777"
        payload = {
            "publicCareerPageUrl": "https://acme.whitecarrot.ai",
            "roles": [
                {
                    "id": valid_id,
                    "roleName": "   ",
                    "status": "PUBLISHED",
                    "link": f"https://acme.whitecarrot.ai/jobs/{valid_id}",
                },
                {
                    "id": valid_id,
                    "roleName": "Mismatched ID",
                    "status": "PUBLISHED",
                    "link": "https://acme.whitecarrot.ai/jobs/88888888-8888-4888-8888-888888888888",
                },
                {
                    "id": valid_id,
                    "roleName": "Query Link",
                    "status": "PUBLISHED",
                    "link": f"https://acme.whitecarrot.ai/jobs/{valid_id}?redirect=other",
                },
            ],
        }

        result = self._list_payload(payload)

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.inventory_complete)

    def test_constructs_missing_link_only_from_strict_public_career_page_url(self):
        role_id = "77777777-7777-4777-8777-777777777777"
        role = {"id": role_id, "roleName": "Security Engineer", "status": "PUBLISHED"}

        trusted = self._list_payload(
            {
                "publicCareerPageUrl": "https://acme.whitecarrot.ai",
                "roles": [role],
            }
        )
        alternate_provider_host = self._list_payload(
            {
                "publicCareerPageUrl": "https://other.whitecarrot.ai",
                "roles": [role],
            }
        )
        untrusted = self._list_payload(
            {
                "publicCareerPageUrl": "https://evil.example/acme",
                "roles": [role],
            }
        )

        self.assertEqual(
            trusted.candidates[0].url,
            f"https://acme.whitecarrot.ai/jobs/{role_id}",
        )
        self.assertEqual(
            alternate_provider_host.candidates[0].url,
            f"https://other.whitecarrot.ai/jobs/{role_id}",
        )
        self.assertEqual(untrusted.candidates, [])
        self.assertEqual(untrusted.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(untrusted.inventory_complete)

    def test_empty_roles_is_complete_empty_provider_response(self):
        result = self._list_payload({"companyName": "Acme", "roles": []})

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")

    def test_invalid_payload_shapes_are_incomplete_invalid_structured_data(self):
        payloads = ("{broken", "[]", "{}", '{"roles": null}', '{"roles": {}}')

        for payload in payloads:
            with self.subTest(payload=payload):
                result = self._list_body(payload)
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.candidates, [])
                self.assertFalse(result.inventory_complete)

    def test_fetch_error_is_retryable_provider_failure(self):
        board = self.adapter.identify_board("https://app.whitecarrot.io/careers/acme")

        result = self.adapter.list_jobs(
            StubFetcher(error=FetchError("offline")),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_api_redirect_must_preserve_exact_endpoint_and_tenant(self):
        payload = json.dumps({"companyName": "Acme", "roles": []})
        rejected = (
            "https://evil.example/api/careers/acme",
            "https://app.whitecarrot.io/api/careers/other",
            "https://app.whitecarrot.io/api/careers/acme?tenant=other",
            "https://app.whitecarrot.io/api/careers/acme/",
        )

        for final_url in rejected:
            with self.subTest(final_url=final_url):
                result = self._list_body(payload, final_url=final_url)
                self.assertEqual(result.candidates, [])
                self.assertFalse(result.inventory_complete)
                self.assertNotEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

        accepted = self._list_body(
            payload,
            final_url="https://app.whitecarrot.io/api/careers/acme",
        )
        self.assertEqual(accepted.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(accepted.inventory_complete)

    def test_title_query_does_not_filter_complete_inventory(self):
        result = self._list_body(
            FIXTURE.read_text(encoding="utf-8"),
            query=JobQuery(title="Machine Learning Engineer"),
        )

        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Machine Learning Engineer", "Platform Engineer"],
        )
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertTrue(result.trace["inventory_complete"])

    def _list_payload(self, payload, *, query=None):
        return self._list_body(json.dumps(payload), query=query)

    def _list_body(self, body, *, final_url=None, query=None):
        board = self.adapter.identify_board("https://app.whitecarrot.io/careers/acme")
        return self.adapter.list_jobs(
            StubFetcher(body, final_url=final_url),
            board,
            query or JobQuery(),
        )


if __name__ == "__main__":
    unittest.main()
