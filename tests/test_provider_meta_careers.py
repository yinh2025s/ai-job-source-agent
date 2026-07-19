import unittest
from pathlib import Path
import json
from urllib.parse import parse_qs

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.domain import DomainProviderAdapter
from job_source_agent.providers.meta_careers import ADAPTER, MetaCareersAdapter
from job_source_agent.providers.registry import (
    build_default_provider_registry,
    discover_native_adapters,
)
from job_source_agent.web import FetchError, Page


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "samples" / "sites" / "js-heavy-rendered-cohort" / "meta" / "rendered.html"
BOARD_URL = "https://www.metacareers.com/jobsearch/"


class RecordingFetcher:
    def __init__(self, html="", final_url=None, error=None):
        self.html = html
        self.final_url = final_url
        self.error = error
        self.requested_urls = []
        self.requested_headers = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        self.requested_headers.append(headers)
        if self.error:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="meta-rendered-contract",
        )


class MetaCareersAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MetaCareersAdapter()
        self.board = JobBoard(BOARD_URL, "meta_careers", "meta")

    def test_native_adapter_is_auto_discovered_and_replaces_domain_adapter(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        selected = build_default_provider_registry().adapter_for(
            "https://metacareers.com/jobs/"
        )

        self.assertIs(native["meta_careers"], ADAPTER)
        self.assertIs(selected, ADAPTER)
        self.assertIsInstance(selected, ProviderAdapter)
        self.assertNotIsInstance(selected, DomainProviderAdapter)
        self.assertTrue(selected.supports_listing)

    def test_uses_official_graphql_title_inventory_from_bootstrap_contract(self):
        bootstrap = '<script>["LSD",[],{"token":"one-time-lsd"},323]</script>'
        payload = {
            "data": {
                "job_search_with_featured_jobs_v2": {
                    "all_jobs": [
                        {
                            "id": "123456",
                            "title": "Product Manager",
                            "locations": ["New York, NY", "Menlo Park, CA"],
                        },
                        {"id": "not-numeric", "title": "Unsafe", "locations": []},
                    ]
                }
            },
            "extensions": {"is_final": True},
        }

        class GraphQLFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                if url.startswith(BOARD_URL):
                    return Page(url=url, html=bootstrap, source="live")
                return Page(
                    url=url,
                    html="for (;;);" + json.dumps(payload),
                    source="live",
                )

        fetcher = GraphQLFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" Product   Manager ", location="New York, NY"),
        )

        self.assertEqual(
            [request[0] for request in fetcher.requests],
            [f"{BOARD_URL}?q=Product+Manager", "https://www.metacareers.com/graphql"],
        )
        form = parse_qs(fetcher.requests[1][1].decode("utf-8"))
        self.assertEqual(form["fb_api_req_friendly_name"], ["CareersJobSearchResultsV2DataQuery"])
        self.assertEqual(form["doc_id"], ["27129360303422352"])
        variables = json.loads(form["variables"][0])
        self.assertEqual(variables["search_input"]["q"], "Product Manager")
        self.assertEqual(fetcher.requests[1][2]["X-FB-LSD"], "one-time-lsd")
        self.assertNotIn("one-time-lsd", repr(result.trace))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Product Manager")
        self.assertEqual(
            result.candidates[0].url,
            "https://www.metacareers.com/profile/job_details/123456",
        )
        self.assertEqual(
            result.candidates[0].location, "New York, NY + Menlo Park, CA"
        )
        self.assertEqual(result.inventory_scope, "filtered_query")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["rejected_candidate_count"], 1)
        self.assertTrue(result.trace["absence_established"])

    def test_rejects_graphql_cross_host_and_malformed_inventory(self):
        bootstrap = '<script>["LSD",[],{"token":"one-time-lsd"},323]</script>'

        class BadGraphQLFetcher:
            def __init__(self, *, final_url=None, body="{}"):
                self.final_url = final_url
                self.body = body

            def fetch(self, url, data=None, headers=None):
                if url.startswith(BOARD_URL):
                    return Page(url=url, html=bootstrap, source="live")
                return Page(
                    url=url,
                    final_url=self.final_url or url,
                    html=self.body,
                    source="live",
                )

        redirected = self.adapter.list_jobs(
            BadGraphQLFetcher(final_url="https://evil.example/graphql"),
            self.board,
            JobQuery(title="Engineer"),
        )
        malformed = self.adapter.list_jobs(
            BadGraphQLFetcher(body='{"data":{"job_search_with_featured_jobs_v2":{}}}'),
            self.board,
            JobQuery(title="Engineer"),
        )

        for result in (redirected, malformed):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])
        self.assertNotIn("evil.example", repr(redirected.trace))

    def test_recognizes_only_safe_supported_routes_and_canonicalizes_board(self):
        valid = (
            "https://metacareers.com/jobs",
            "https://www.metacareers.com/jobsearch/?q=engineer",
            "https://www.metacareers.com:443/profile/job_details/123456?source=jobs",
        )
        invalid = (
            "http://www.metacareers.com/jobs",
            "https://jobs.metacareers.com/jobs",
            "https://www.metacareers.com.evil.example/jobs",
            "https://evil@www.metacareers.com/jobs",
            "https://user:secret@www.metacareers.com/jobsearch/",
            "https://www.metacareers.com:8443/jobsearch/",
            "https://www.metacareers.com/jobs//",
            "https://www.metacareers.com/profile/job_details/not-numeric",
            "https://www.metacareers.com/profile/job_details/123//",
            "https://www.metacareers.com/profile/job_details/123/extra",
            "https://www.metacareers.com/careers",
        )

        for url in valid:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_canonical_unfiltered_inventory_for_downstream_title_match(self):
        fetcher = RecordingFetcher(FIXTURE.read_text(encoding="utf-8"))

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Data Scientist, Product Analytics"),
        )

        self.assertEqual(
            fetcher.requested_urls,
            [f"{BOARD_URL}?q=Data+Scientist%2C+Product+Analytics"],
        )
        self.assertEqual(
            fetcher.requested_headers[0]["User-Agent"],
            "AI-Job-Source-Agent/1.0",
        )
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            [
                "Critical Facility Engineer",
                "Data Scientist, Product Analytics",
                "Product Manager",
            ],
        )
        self.assertEqual(
            result.candidates[1].url,
            "https://www.metacareers.com/profile/job_details/1070147800777577",
        )
        self.assertEqual(
            result.candidates[1].location,
            "Sunnyvale, CA +9 locations - Data Science",
        )
        self.assertEqual(result.candidates[1].raw["job_id"], "1070147800777577")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "visible_page")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], "visible_page")
        self.assertFalse(result.trace["inventory_complete"])
        self.assertEqual(result.trace["variant"], "anonymous_title_search")
        self.assertEqual(result.trace["query_transport"], "official_title_query")

    def test_falls_back_to_public_sitemap_and_jobposting_json_ld(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        first_url = "https://www.metacareers.com/profile/job_details/123"
        target_url = "https://www.metacareers.com/profile/job_details/456"
        sitemap = f"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{first_url}/</loc></url>
              <url><loc>https://evil.example/profile/job_details/999</loc></url>
              <url><loc>{target_url}</loc></url>
              <url><loc>https://www.metacareers.com/profile/job_details/789</loc></url>
            </urlset>
        """
        details = {
            first_url: """
                <script type="application/ld+json">
                  {"@type":"JobPosting","title":"Data Engineer",
                   "hiringOrganization":{"name":"Meta","sameAs":"https://www.meta.com/"}}
                </script>
            """,
            target_url: """
                <script type="application/ld+json">
                  {"@graph":[{"@type":"JobPosting","title":"Product Manager",
                    "jobLocation":[{"name":"Menlo Park, CA"},{"name":"Remote, US"}],
                    "hiringOrganization":{"name":"Meta","sameAs":"https://www.meta.com/"}}]}
                </script>
            """,
        }

        class SitemapFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, headers))
                if url.startswith(f"{BOARD_URL}?"):
                    raise FetchError("HTTP Error 400: Bad Request", status=400)
                if url == sitemap_url:
                    return Page(url=url, html=sitemap, source="live")
                return Page(url=url, html=details[url], source="live")

        fetcher = SitemapFetcher()
        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title=" Product   Manager ")
        )

        self.assertEqual(
            [url for url, _ in fetcher.requests],
            [f"{BOARD_URL}?q=Product+Manager", sitemap_url, first_url, target_url],
        )
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Data Engineer", "Product Manager"],
        )
        self.assertEqual(result.candidates[-1].url, target_url)
        self.assertEqual(
            result.candidates[-1].location, "Menlo Park, CA + Remote, US"
        )
        self.assertEqual(
            result.candidates[-1].raw,
            {
                "job_id": "456",
                "evidence": "schema_org_jobposting",
                "hiring_organization": "Meta",
            },
        )
        self.assertEqual(result.trace["variant"], "public_sitemap_jobposting")
        self.assertEqual(result.trace["rejected_url_count"], 1)
        self.assertEqual(result.trace["detail_probe_count"], 2)
        self.assertEqual(
            result.trace["query_transport"],
            "bounded_title_directed_sitemap_probe",
        )
        self.assertEqual(
            result.trace["detail_selection_strategy"], "complete_sitemap_order"
        )
        self.assertFalse(result.trace["absence_established"])
        self.assertFalse(result.inventory_complete)
        for _, headers in fetcher.requests:
            self.assertEqual(headers["User-Agent"], "AI-Job-Source-Agent/1.0")

    def test_sitemap_detail_sampling_is_bounded_for_latency(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        detail_urls = [
            f"https://www.metacareers.com/profile/job_details/{1000 + index}"
            for index in range(12)
        ]
        sitemap = "<urlset>" + "".join(
            f"<url><loc>{url}</loc></url>" for url in detail_urls
        ) + "</urlset>"

        class BoundedFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                if url == BOARD_URL:
                    return Page(url=url, html="<main></main>", source="live")
                if url == sitemap_url:
                    return Page(url=url, html=sitemap, source="live")
                return Page(
                    url=url,
                    html=(
                        '<script type="application/ld+json">'
                        '{"@type":"JobPosting","title":"Unrelated Role"}'
                        "</script>"
                    ),
                    source="live",
                )

        fetcher = BoundedFetcher()
        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Product Design Engineer")
        )

        self.assertEqual(result.trace["detail_probe_limit"], 8)
        self.assertEqual(result.trace["detail_probe_count"], 8)
        selected = fetcher.requests[2:]
        self.assertEqual(len(selected), 8)
        self.assertNotEqual(selected, detail_urls[:8])
        self.assertEqual(
            result.trace["detail_selection_strategy"],
            "title_seeded_stratified_sitemap",
        )
        self.assertEqual(result.trace["detail_selection_count"], 8)
        self.assertFalse(result.trace["absence_established"])

        replay = BoundedFetcher()
        self.adapter.list_jobs(
            replay, self.board, JobQuery(title="  product   design engineer ")
        )
        self.assertEqual(replay.requests[2:], selected)

    def test_retryable_title_query_timeout_falls_back_to_public_sitemap(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        detail_url = "https://www.metacareers.com/profile/job_details/888"

        class TimeoutThenSitemapFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                if url.startswith(f"{BOARD_URL}?"):
                    raise FetchError("timed out", reason_code="NETWORK_TIMEOUT")
                if url == sitemap_url:
                    return Page(
                        url=url,
                        html=f"<urlset><url><loc>{detail_url}</loc></url></urlset>",
                    )
                return Page(
                    url=url,
                    html="""
                    <script type="application/ld+json">
                    {"@type":"JobPosting","title":"Product Manager",
                     "jobLocation":{"name":"New York, NY"},
                     "hiringOrganization":{"name":"Meta",
                       "sameAs":"https://www.meta.com/"}}
                    </script>
                    """,
                )

        fetcher = TimeoutThenSitemapFetcher()
        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Product Manager")
        )

        self.assertEqual(
            fetcher.requests,
            [f"{BOARD_URL}?q=Product+Manager", sitemap_url, detail_url],
        )
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].title, "Product Manager")
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["absence_established"])

    def test_detail_timeout_is_retryable_and_does_not_establish_absence(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        detail_urls = [
            f"https://www.metacareers.com/profile/job_details/{2000 + index}"
            for index in range(12)
        ]
        sitemap = "<urlset>" + "".join(
            f"<url><loc>{url}</loc></url>" for url in detail_urls
        ) + "</urlset>"

        class InconclusiveFetcher:
            def fetch(self, url, data=None, headers=None):
                if url.startswith(f"{BOARD_URL}?"):
                    raise FetchError("bad request", status=400)
                if url == sitemap_url:
                    return Page(url=url, html=sitemap, source="live")
                if url.endswith("0"):
                    raise FetchError("timeout", reason_code="NETWORK_TIMEOUT")
                return Page(url=url, html="<main>missing structured data</main>")

        result = self.adapter.list_jobs(
            InconclusiveFetcher(),
            self.board,
            JobQuery(title="Infrastructure Engineer"),
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.trace["absence_established"])
        self.assertGreater(
            result.trace["detail_failure_count"]
            + result.trace["malformed_detail_count"],
            0,
        )

    def test_rejects_cross_entity_payload_and_keeps_no_match_incomplete(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        detail_url = "https://www.metacareers.com/profile/job_details/901"
        sitemap = f"<urlset><url><loc>{detail_url}</loc></url></urlset>"

        class PayloadFetcher:
            def fetch(self, url, data=None, headers=None):
                if url.startswith(f"{BOARD_URL}?"):
                    raise FetchError("bad request", status=400)
                if url == sitemap_url:
                    return Page(url=url, html=sitemap)
                return Page(
                    url=url,
                    html="""
                    <script type="application/ld+json">
                    {"@type":"JobPosting","title":"Product Manager",
                     "url":"https://evil.example/jobs/901",
                     "hiringOrganization":{"name":"Other Co",
                       "sameAs":"https://evil.example/"}}
                    </script>
                    """,
                )

        result = self.adapter.list_jobs(
            PayloadFetcher(), self.board, JobQuery(title="Product Manager")
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["absence_established"])
        self.assertNotIn("evil.example", repr(result.trace))

    def test_rejects_malformed_meta_same_as_payload(self):
        sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"
        detail_url = "https://www.metacareers.com/profile/job_details/902"

        class MalformedPayloadFetcher:
            def fetch(self, url, data=None, headers=None):
                if url.startswith(f"{BOARD_URL}?"):
                    raise FetchError("bad request", status=400)
                if url == sitemap_url:
                    return Page(
                        url=url,
                        html=f"<urlset><url><loc>{detail_url}</loc></url></urlset>",
                    )
                return Page(
                    url=url,
                    html="""
                    <script type="application/ld+json">
                    {"@type":"JobPosting","title":"Product Manager",
                     "hiringOrganization":{"name":"Meta",
                       "sameAs":"https://www.meta.com:not-a-port/"}}
                    </script>
                    """,
                )

        result = self.adapter.list_jobs(
            MalformedPayloadFetcher(), self.board, JobQuery(title="Product Manager")
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_dedupes_canonical_details_and_keeps_title_association_inside_link(self):
        html = """
            <div>Borrowed title outside link</div>
            <a href="/profile/job_details/123?ref=one">
              <div><h3>Software Engineer</h3><span>Menlo Park, CA</span></div>
            </a>
            <a href="https://metacareers.com/profile/job_details/123/">
              Duplicate title <span>Remote</span>
            </a>
            <a href="/profile/job_details/456"><span>Location only</span></a>
        """

        result = self.adapter.list_jobs(
            RecordingFetcher(html), self.board, JobQuery(title="Software Engineer")
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Software Engineer")
        self.assertEqual(result.candidates[0].location, "Menlo Park, CA")
        self.assertEqual(
            result.candidates[0].url,
            "https://www.metacareers.com/profile/job_details/123",
        )
        self.assertEqual(result.trace["rejected_link_count"], 1)

    def test_empty_shell_login_challenge_and_400_like_pages_are_unsupported(self):
        variants = (
            "<html><main id='root'></main><script src='/bundle.js'></script></html>",
            "<h1>Log in to continue</h1><form><input type='password'></form>",
            "<title>Security challenge</title><p>Verify you are human</p>",
            "<h1>400 Bad Request</h1>",
        )

        for html in variants:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(html), self.board, JobQuery(title="Engineer")
                )
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.retryable)
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.inventory_scope, "unknown")
                self.assertNotIn("EMPTY_PROVIDER_RESPONSE", repr(result.trace))

    def test_retries_one_browser_shell_before_accepting_rendered_evidence(self):
        rendered = FIXTURE.read_text(encoding="utf-8")

        class HydratingFetcher:
            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                html = "<html><main id='root'></main></html>" if self.calls == 1 else rendered
                return Page(
                    url=url,
                    final_url=url,
                    html=html,
                    source="browser",
                )

        fetcher = HydratingFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Product Manager"),
        )

        self.assertEqual(fetcher.calls, 2)
        self.assertEqual(result.trace["attempt_count"], 2)
        self.assertEqual(result.candidates[-1].title, "Product Manager")
        self.assertFalse(result.inventory_complete)

    def test_rejects_redirects_invalid_board_and_sanitizes_trace(self):
        unsafe_url = "https://user:secret@evil.example/jobsearch/?token=private"
        redirected = self.adapter.list_jobs(
            RecordingFetcher(final_url=unsafe_url),
            self.board,
            JobQuery(title="Engineer"),
        )
        wrong_meta_host = self.adapter.list_jobs(
            RecordingFetcher(final_url="https://metacareers.com/jobsearch/?q=Engineer"),
            self.board,
            JobQuery(title="Engineer"),
        )
        invalid_board = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard("https://metacareers.com/jobsearch/", "meta_careers", "meta"),
            JobQuery(),
        )

        for result in (redirected, wrong_meta_host, invalid_board):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertNotIn("secret", repr(redirected.trace))
        self.assertNotIn("private", repr(redirected.trace))
        self.assertNotIn("evil.example", repr(redirected.trace))

    def test_rejects_unsafe_navigation_and_cross_host_detail_links(self):
        html = """
            <a href="/jobs">Jobs navigation</a>
            <a href="javascript:alert(1)">Script job</a>
            <a href="https://evil.example/profile/job_details/123">Cross host</a>
            <a href="https://www.metacareers.com:8443/profile/job_details/234">Bad port</a>
            <a href="https://user@www.metacareers.com/profile/job_details/345">Credentials</a>
            <a href="/profile/job_details/not-a-number">Malformed ID</a>
        """

        result = self.adapter.list_jobs(
            RecordingFetcher(html), self.board, JobQuery(title="Engineer")
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.trace["rejected_link_count"], 6)
        self.assertNotIn("evil.example", repr(result.trace))

    def test_untyped_fetch_failure_is_retryable_and_does_not_leak_error_text(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("token=private timeout")),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["error_type"], "FetchError")
        self.assertEqual(result.trace["error_classification"], "NETWORK_TIMEOUT")
        self.assertEqual(
            result.trace["board_urls"], ["https://www.metacareers.com/jobsearch/sitemap.xml"]
        )
        self.assertNotIn("private", repr(result.trace))

    def test_preserves_typed_fetch_failure_and_retryability(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                error=FetchError(
                    "credential=private",
                    status=403,
                    reason_code="BOT_PROTECTION",
                    retryable=False,
                )
            ),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(result.reason_code, "BOT_PROTECTION")
        self.assertFalse(result.retryable)
        self.assertEqual(result.trace["error_status"], 403)
        self.assertEqual(result.trace["error_classification"], "BOT_PROTECTION")
        self.assertNotIn("private", repr(result.trace))

    def test_http_400_is_typed_as_unsupported_transport_capability(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                error=FetchError(
                    "HTTP Error 400: Bad Request",
                    status=400,
                    reason_code="FETCH_FAILED",
                    retryable=True,
                )
            ),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.retryable)
        self.assertEqual(result.trace["error_status"], 400)
        self.assertEqual(result.trace["variant"], "public_sitemap_jobposting")
        self.assertEqual(result.trace["board_responses"], [])


if __name__ == "__main__":
    unittest.main()
