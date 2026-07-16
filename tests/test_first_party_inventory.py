import json
import unittest

from job_source_agent.errors import DiscoveryError
from job_source_agent.first_party_inventory import (
    AssetSource,
    MAX_INVENTORY_BYTES,
    probe_first_party_job_inventory,
)
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.opening_matcher import structured_job_links
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


class DeclaredCrossOriginInventoryTests(unittest.TestCase):
    page_url = "https://careers.example.com/jobs"
    asset_url = "https://careers.example.com/assets/jobs.js?v=1"
    endpoint = "https://public-api.example.net/openings"

    def page(self, endpoint=None, attribute="data-public-jobs-url"):
        endpoint = endpoint or self.endpoint
        return Page(
            url=self.page_url,
            html=f'<main {attribute}="{endpoint}">Open roles</main>',
        )

    def asset(self, body=None):
        return AssetSource(
            self.asset_url,
            body
            or (
                "const inventoryUrl = root.dataset.publicJobsUrl;"
                "fetch(inventoryUrl).then(renderJobs);"
            ),
        )

    def response(self, jobs, *, final_url=None, body=None):
        return Page(
            url=self.endpoint,
            final_url=final_url or self.endpoint,
            html=body if body is not None else json.dumps({"jobs": jobs}),
        )

    def probe(self, fetcher, *, page=None, assets=None):
        return probe_first_party_job_inventory(
            fetcher,
            page or self.page(),
            assets or [self.asset()],
            ashby_identity,
        )

    def test_verifies_declared_cross_origin_inventory_with_anonymous_fetch(self):
        openings = [
            "https://openings.example.org/jobs/role-1",
            "https://openings.example.org/jobs/role-2",
        ]
        fetcher = RecordingFetcher(
            {
                self.endpoint: self.response(
                    [
                        {"title": "Platform Engineer", "url": openings[0]},
                        {
                            "title": "Data Engineer",
                            "url": openings[1],
                            "location": "Remote",
                        },
                    ]
                )
            }
        )

        probe = self.probe(fetcher)

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["method"], "first_party_declared_inventory")
        self.assertEqual(probe.trace["status"], "verified")
        self.assertTrue(probe.trace["inventory_complete"])
        self.assertEqual(probe.trace["inventory_count"], 2)
        self.assertEqual(fetcher.requests, [(self.endpoint, None, None)])
        for opening in openings:
            self.assertIn(opening, probe.page.html)

    def test_accepts_get_attribute_evidence_for_the_same_data_attribute(self):
        asset = self.asset(
            'let url=node.getAttribute("data-public-jobs-url");window.fetch(url)'
        )
        opening = "https://openings.example.org/jobs/role-1"
        fetcher = RecordingFetcher(
            {self.endpoint: self.response([{"title": "Engineer", "url": opening}])}
        )

        probe = self.probe(fetcher, assets=[asset])

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")

    def test_accepts_pascal_case_rows_and_explicit_cors_get(self):
        asset = self.asset(
            'const url=root.getAttribute("data-public-jobs-url");'
            'fetch(url,{mode:"cors",method:"GET",credentials:"omit"})'
        )
        opening = "https://openings.example.org/jobs/role-1"
        fetcher = RecordingFetcher(
            {
                self.endpoint: self.response(
                    [{"Title": "Registered Nurse", "Url": opening, "Location": "Oxnard, CA"}]
                )
            }
        )

        probe = self.probe(fetcher, assets=[asset])

        self.assertIsNotNone(probe)
        assert probe is not None
        self.assertEqual(probe.trace["status"], "verified")
        self.assertIn("Registered Nurse", probe.page.html)
        self.assertEqual(
            [(link.text, link.url) for link in structured_job_links(probe.page.html, self.page_url)],
            [("Registered Nurse", opening)],
        )

    def test_requires_matching_first_party_js_anonymous_fetch_evidence(self):
        cases = {
            "cross_origin_asset": AssetSource(
                "https://cdn.example.net/jobs.js",
                "const u=root.dataset.publicJobsUrl;fetch(u)",
            ),
            "different_attribute": self.asset(
                "const u=root.dataset.privateJobsUrl;fetch(u)"
            ),
            "literal_fetch": self.asset(f'fetch("{self.endpoint}")'),
            "credentials": self.asset(
                'const u=root.dataset.publicJobsUrl;fetch(u,{credentials:"include"})'
            ),
            "reassigned": self.asset(
                "let u=root.dataset.publicJobsUrl;u=other;fetch(u)"
            ),
        }
        for name, asset in cases.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({})
                self.assertIsNone(self.probe(fetcher, assets=[asset]))
                self.assertEqual(fetcher.requests, [])

    def test_rejects_unsafe_or_non_cross_origin_declared_endpoint(self):
        endpoints = [
            "http://public-api.example.net/openings",
            "https://public-api.example.net:8443/openings",
            "https://user@public-api.example.net/openings",
            "https://public-api.example.net/openings?tenant=example",
            "https://public-api.example.net/openings#jobs",
            "https://careers.example.com/openings",
            "https://127.0.0.1/openings",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                fetcher = RecordingFetcher({})
                self.assertIsNone(self.probe(fetcher, page=self.page(endpoint)))
                self.assertEqual(fetcher.requests, [])

    def test_rejects_redirect_payload_shape_size_and_row_cap(self):
        opening = "https://openings.example.org/jobs/role-1"
        invalid_responses = {
            "redirect": self.response(
                [{"title": "Engineer", "url": opening}],
                final_url=self.endpoint + "/redirected",
            ),
            "wrong_root": self.response([], body=json.dumps({"data": []})),
            "extra_root": self.response([], body=json.dumps({"jobs": [], "total": 0})),
            "oversized": self.response([], body=" " * (MAX_INVENTORY_BYTES + 1)),
            "too_many_rows": self.response([], body=json.dumps({"jobs": [{}] * 5001})),
        }
        for name, response in invalid_responses.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({self.endpoint: response})
                probe = self.probe(fetcher)
                self.assertIsNotNone(probe)
                assert probe is not None
                self.assertFalse(probe.trace["inventory_complete"])
                self.assertNotIn("verified_job_urls", probe.page.html)

    def test_rejects_mixed_or_unsafe_opening_url_family(self):
        cases = {
            "origin": [
                "https://openings.example.org/jobs/role-1",
                "https://other.example.org/jobs/role-2",
            ],
            "path": [
                "https://openings.example.org/jobs/role-1",
                "https://openings.example.org/careers/role-2",
            ],
            "query": ["https://openings.example.org/jobs/role-1?token=x"],
            "fragment": ["https://openings.example.org/jobs/role-1#apply"],
            "port": ["https://openings.example.org:444/jobs/role-1"],
            "auth": ["https://user@openings.example.org/jobs/role-1"],
        }
        for name, urls in cases.items():
            with self.subTest(name=name):
                jobs = [
                    {"title": f"Engineer {index}", "url": url}
                    for index, url in enumerate(urls)
                ]
                fetcher = RecordingFetcher({self.endpoint: self.response(jobs)})
                probe = self.probe(fetcher)
                self.assertIsNotNone(probe)
                assert probe is not None
                self.assertEqual(probe.trace["status"], "invalid_inventory_payload")
                self.assertFalse(probe.trace["inventory_complete"])

    def test_empty_and_fetch_failure_never_claim_completeness(self):
        responses = [
            self.response([]),
            FetchError("timeout", reason_code="NETWORK_TIMEOUT", retryable=True),
        ]
        for response in responses:
            with self.subTest(response=response):
                fetcher = RecordingFetcher({self.endpoint: response})
                probe = self.probe(fetcher)
                self.assertIsNotNone(probe)
                assert probe is not None
                self.assertFalse(probe.trace["inventory_complete"])
                self.assertNotIn("verified_job_urls", probe.page.html)


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

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=1,
        ).find_job_board_portfolio(self.career)

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/example")
        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio.primary.detection_method, "page_evidence")
        self.assertEqual(
            portfolio.primary.evidence_url,
            "https://jobs.ashbyhq.com/example",
        )
        self.assertEqual(portfolio.primary.relationship_evidence_url, self.career)
        self.assertEqual(trace["provider"], "ashby")
        probe = trace["content_payload_probes"][0]
        self.assertEqual(probe["method"], "first_party_dynamic_inventory")
        self.assertEqual(probe["status"], "verified")

    def test_pipeline_promotes_semantically_bound_first_party_cards(self):
        html = """
            <main>
              <div class="card">
                <h3 class="card-title">AI/ML Engineer</h3>
                <a href="apply?id=role-123">Apply</a>
              </div>
              <div class="card">
                <h3 class="card-title">Platform Engineer</h3>
                <a href="apply?id=role-456">Apply</a>
              </div>
            </main>
        """
        fetcher = RecordingFetcher(
            {self.career: Page(url=self.career, html=html)}
        )

        job_list, trace = JobSourceAgent(
            fetcher,
            max_job_pages=1,
        ).find_job_board(self.career)

        self.assertEqual(job_list, self.career)
        self.assertEqual(
            trace["selected_from"],
            "verified_first_party_listing_inventory",
        )
        self.assertEqual(
            trace["first_party_listing_inventory"]["candidate_count"],
            2,
        )

    def test_pipeline_does_not_promote_hidden_first_party_cards(self):
        html = """
            <main>
              <div class="card" hidden>
                <h3 class="card-title">AI/ML Engineer</h3>
                <a href="apply?id=role-123">Apply</a>
              </div>
            </main>
        """
        fetcher = RecordingFetcher(
            {self.career: Page(url=self.career, html=html)}
        )

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(fetcher, max_job_pages=1).find_job_board(self.career)

        self.assertEqual(raised.exception.code, "job_board_not_found")

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
