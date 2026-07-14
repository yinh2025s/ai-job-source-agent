import json
import unittest

from job_source_agent.errors import DiscoveryError
from job_source_agent.first_party_inventory import (
    AssetSource,
    MAX_INVENTORY_BYTES,
    probe_first_party_job_inventory,
)
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.snapshot import sanitize_snapshot_body
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.pages.get(url)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FetchError(f"fixture miss: {url}")
        return value


def ashby_identity(url):
    prefix = "https://jobs.ashbyhq.com/"
    if not url.startswith(prefix):
        return None
    tenant = url[len(prefix) :].split("/", 1)[0]
    if not tenant:
        return None
    return "ashby", f"{prefix}{tenant}"


class FirstPartyInventoryProbeTests(unittest.TestCase):
    page_url = "https://www.example.com/career"
    route_asset = "https://www.example.com/_nuxt/career.js"
    client_asset = "https://www.example.com/_nuxt/client.js"
    endpoint = "https://www.example.com/api-proxy/api/get-career-posting-list"

    def page(self):
        return Page(url=self.page_url, html="<main>Open roles</main>")

    def route_source(self):
        return AssetSource(
            self.route_asset,
            'import{x as B}from"./client.js";B.get("/api/get-career-posting-list")',
        )

    def client_page(self, *, authorization='Authorization:"Bearer www.example.com"'):
        return Page(
            url=self.client_asset,
            html=(
                'const base="https://www.example.com/api-proxy";'
                f"const headers={{ {authorization} }};"
            ),
        )

    def response(self, records):
        return Page(
            url=self.endpoint,
            final_url=self.endpoint,
            html=json.dumps({"data": records, "correlation_id": "public-request-id"}),
        )

    def test_verifies_literal_get_and_same_tenant_inventory(self):
        opening = "https://jobs.ashbyhq.com/example/11111111-1111-1111-1111-111111111111"
        fetcher = RecordingFetcher(
            {
                self.client_asset: self.client_page(),
                self.endpoint: self.response(
                    [{"title": "AI Engineer", "url": opening, "location": "Remote"}]
                ),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")
        self.assertEqual(probe.trace["inventory_count"], 1)
        self.assertEqual(probe.trace["provider"], "ashby")
        self.assertEqual(probe.trace["board_url"], "https://jobs.ashbyhq.com/example")
        self.assertNotIn(opening, json.dumps(probe.trace))
        self.assertIn(opening, probe.page.html)
        self.assertEqual(
            fetcher.requests,
            [
                (self.client_asset, None, None),
                (
                    self.endpoint,
                    None,
                    {"Authorization": "Bearer www.example.com"},
                ),
            ],
        )

    def test_allows_endpoint_without_authorization_contract(self):
        direct_endpoint = "https://www.example.com/api/get-career-posting-list"
        source = AssetSource(
            self.route_asset,
            (
                'const base="https://www.example.com/api";'
                'B.get("/api/get-career-posting-list")'
            ),
        )
        fetcher = RecordingFetcher(
            {
                direct_endpoint: Page(
                    url=direct_endpoint,
                    final_url=direct_endpoint,
                    html=json.dumps({"data": []}),
                )
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [source],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")
        self.assertTrue(probe.trace["inventory_complete"])
        self.assertEqual(probe.trace["inventory_count"], 0)
        self.assertEqual(fetcher.requests, [(direct_endpoint, None, None)])

    def test_never_forwards_bundle_authorization_value(self):
        fetcher = RecordingFetcher(
            {
                self.client_asset: self.client_page(
                    authorization='Authorization:"Bearer secret"'
                ),
                self.endpoint: self.response([]),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")
        self.assertEqual(
            fetcher.requests[-1],
            (
                self.endpoint,
                None,
                {"Authorization": "Bearer www.example.com"},
            ),
        )

    def test_sanitized_client_bundle_replays_same_public_proxy_request(self):
        sanitized_client = self.client_page()
        sanitized_client.html = sanitize_snapshot_body(sanitized_client.html)
        fetcher = RecordingFetcher(
            {
                self.client_asset: sanitized_client,
                self.endpoint: self.response([]),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")
        self.assertEqual(
            fetcher.requests[-1][2],
            {"Authorization": "Bearer www.example.com"},
        )

    def test_rejects_cross_origin_api_base(self):
        source = AssetSource(
            self.route_asset,
            (
                'const base="https://api.example.net/api-proxy";'
                'B.get("/api/get-career-posting-list")'
            ),
        )
        fetcher = RecordingFetcher({})

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [source],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "api_base_not_verified")
        self.assertEqual(fetcher.requests, [])

    def test_rejects_cross_origin_client_dependency(self):
        source = AssetSource(
            self.route_asset,
            (
                'import{x as B}from"https://evil.example/client.js";'
                'B.get("/api/get-career-posting-list")'
            ),
        )
        fetcher = RecordingFetcher({})

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [source],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "api_base_not_verified")
        self.assertEqual(fetcher.requests, [])

    def test_rejects_inventory_redirect(self):
        redirected = "https://www.example.com/api-proxy/other"
        fetcher = RecordingFetcher(
            {
                self.client_asset: self.client_page(),
                self.endpoint: Page(
                    url=self.endpoint,
                    final_url=redirected,
                    html=json.dumps({"data": []}),
                ),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "inventory_redirect_rejected")
        self.assertFalse(probe.trace["inventory_complete"])

    def test_rejects_mixed_tenant_and_malformed_rows_as_incomplete(self):
        mixed = self.response(
            [
                {"title": "AI Engineer", "url": "https://jobs.ashbyhq.com/example/1"},
                {"title": "Data Engineer", "url": "https://jobs.ashbyhq.com/other/2"},
            ]
        )
        malformed = self.response(
            [{"title": "AI Engineer", "url": "https://untrusted.example/jobs/1"}]
        )
        for response in (mixed, malformed):
            with self.subTest(response=response.html):
                fetcher = RecordingFetcher(
                    {self.client_asset: self.client_page(), self.endpoint: response}
                )
                probe = probe_first_party_job_inventory(
                    fetcher,
                    self.page(),
                    [self.route_source()],
                    ashby_identity,
                )
                self.assertIsNotNone(probe)
                assert probe is not None
                self.assertEqual(probe.trace["status"], "invalid_inventory_payload")
                self.assertFalse(probe.trace["inventory_complete"])
                self.assertNotIn("verified_job_urls", probe.page.html)

    def test_rejects_oversized_payload(self):
        fetcher = RecordingFetcher(
            {
                self.client_asset: self.client_page(),
                self.endpoint: Page(
                    url=self.endpoint,
                    html=" " * (MAX_INVENTORY_BYTES + 1),
                ),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "invalid_inventory_payload")

    def test_preserves_retryable_fetch_classification_without_rows(self):
        fetcher = RecordingFetcher(
            {
                self.client_asset: self.client_page(),
                self.endpoint: FetchError(
                    "timed out",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                ),
            }
        )

        probe = probe_first_party_job_inventory(
            fetcher,
            self.page(),
            [self.route_source()],
            ashby_identity,
        )

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "inventory_fetch_failed")
        self.assertEqual(probe.trace["fetch_error"]["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(probe.trace["fetch_error"]["retryable"])


class FirstPartyInventoryPipelineTests(unittest.TestCase):
    career = "https://www.example.com/career"
    route_asset = "https://www.example.com/_nuxt/career.js"
    client_asset = "https://www.example.com/_nuxt/client.js"
    endpoint = "https://www.example.com/api-proxy/api/get-career-posting-list"

    def pages(self, records):
        return {
            self.career: Page(
                url=self.career,
                html=f'<script src="{self.route_asset}"></script><main>Open roles</main>',
            ),
            self.route_asset: Page(
                url=self.route_asset,
                html=(
                    'import{x as B}from"./client.js";'
                    'B.get("/api/get-career-posting-list")'
                ),
            ),
            self.client_asset: Page(
                url=self.client_asset,
                html=(
                    'const base="https://www.example.com/api-proxy";'
                    'const h={Authorization:"Bearer www.example.com"};'
                ),
            ),
            self.endpoint: Page(
                url=self.endpoint,
                html=json.dumps({"data": records, "correlation_id": "request-id"}),
            ),
        }

    def test_pipeline_promotes_verified_dynamic_inventory_to_native_board(self):
        opening = "https://jobs.ashbyhq.com/example/11111111-1111-1111-1111-111111111111"
        fetcher = RecordingFetcher(
            self.pages([{"title": "AI Engineer", "url": opening}])
        )

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=1).find_job_board(
            self.career
        )

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/example")
        self.assertEqual(trace["provider"], "ashby")
        probe = trace["content_payload_probes"][0]
        self.assertEqual(probe["method"], "first_party_dynamic_inventory")
        self.assertEqual(probe["status"], "verified")

    def test_pipeline_prioritizes_tail_route_chunk_with_three_asset_cap(self):
        opening = "https://jobs.ashbyhq.com/example/11111111-1111-1111-1111-111111111111"
        decoys = [f"https://www.example.com/_nuxt/hash-{index}.js" for index in range(4)]
        opaque_route = "https://www.example.com/_nuxt/Qx8.js"
        tail = "https://www.example.com/_nuxt/tail.js"
        pages = self.pages([{"title": "AI Engineer", "url": opening}])
        pages[opaque_route] = pages.pop(self.route_asset)
        pages[opaque_route].url = opaque_route
        pages[self.career] = Page(
            url=self.career,
            html="".join(
                f'<link rel="modulepreload" href="{url}">'
                for url in (*decoys, opaque_route, tail)
            ),
        )
        for url in (*decoys, tail):
            pages[url] = Page(url=url, html="const unrelated = true;")
        fetcher = RecordingFetcher(pages)

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=1).find_job_board(
            self.career
        )

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/example")
        probe = trace["content_payload_probes"][0]
        self.assertEqual(
            probe["asset_urls"],
            [tail, opaque_route, decoys[-1]],
        )
        requested_urls = [url for url, _data, _headers in fetcher.requests]
        self.assertNotIn(decoys[0], requested_urls)
        self.assertNotIn(decoys[1], requested_urls)

    def test_pipeline_classifies_verified_empty_inventory(self):
        fetcher = RecordingFetcher(self.pages([]))

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(fetcher, max_job_pages=1).find_job_board(self.career)

        self.assertEqual(raised.exception.code, "NO_PUBLIC_OPENINGS")
        self.assertEqual(
            raised.exception.trace["explicit_empty_inventory"]["source"],
            "first_party_dynamic_inventory",
        )

    def test_pipeline_preserves_retryable_inventory_failure(self):
        pages = self.pages([])
        pages[self.endpoint] = FetchError(
            "timed out",
            reason_code="NETWORK_TIMEOUT",
            retryable=True,
        )
        fetcher = RecordingFetcher(pages)

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(fetcher, max_job_pages=1).find_job_board(self.career)

        self.assertEqual(raised.exception.code, "NETWORK_TIMEOUT")
        failure = raised.exception.trace["fetch_errors"][-1]
        self.assertEqual(failure["origin"], "first_party_dynamic_inventory")
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
