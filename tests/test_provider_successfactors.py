import unittest
import json
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.successfactors import ADAPTER, SuccessFactorsAdapter
from job_source_agent.web import FetchError, Page


FIXTURE = (
    Path(__file__).parents[1]
    / "samples"
    / "sites"
    / "successfactors.example"
    / "ajax-theme.html"
)
LIVE_CONTRACT_FIXTURES = FIXTURE.parent / "live-contracts"


class StubFetcher:
    def __init__(self, html):
        self.html = html
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        return Page(url=url, final_url=url, html=self.html, source="successfactors-fixture")


class SuccessFactorsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SuccessFactorsAdapter()

    def test_exported_adapter_satisfies_provider_contract(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertEqual(ADAPTER.name, "successfactors")
        self.assertTrue(ADAPTER.supports_listing)

    def test_recognizes_successfactors_and_sapsf_without_lookalikes(self):
        self.assertTrue(self.adapter.recognizes("https://career4.successfactors.com/career"))
        self.assertTrue(self.adapter.recognizes("https://acme.jobs.sapsf.com/career"))
        self.assertTrue(self.adapter.recognizes("https://acme-careers.jobs.hr.cloud.sap/search/"))
        self.assertFalse(self.adapter.recognizes("https://successfactors.com.evil.example/career"))
        self.assertFalse(self.adapter.recognizes("https://example.com/successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("ftp://career4.successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("https://career4.successfactors.com:8443/career"))
        self.assertFalse(self.adapter.recognizes("https://user@career4.successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("https://[broken/career"))

    def test_identifies_cloud_sap_board_and_canonicalizes_search_path(self):
        board = self.adapter.identify_board(
            "https://acme-careers.jobs.hr.cloud.sap/job/Old/123-en_GB/"
            "?locale=en_GB&q=Analyst#top"
        )

        self.assertEqual(board.identifier, "cloud:acme-careers.jobs.hr.cloud.sap")
        self.assertEqual(
            board.url,
            "https://acme-careers.jobs.hr.cloud.sap/search/?locale=en_GB",
        )

    def test_cloud_sap_api_uses_csrf_paginates_and_builds_detail_urls(self):
        search_html = """
        <script>
          var CSRFToken = "csrf-123";
          var appParams = { locale: "en_GB" };
        </script>
        """

        class CloudFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append({"url": url, "data": data, "headers": headers})
                if data is None:
                    return Page(url=url, final_url=url, html=search_html, source="cloud-search")
                payload = json.loads(data)
                page_number = payload["pageNumber"]
                count = 10 if page_number == 0 else 1
                jobs = [
                    {"response": {
                        "id": str(9500 + page_number * 10 + index),
                        "unifiedStandardTitle": (
                            "Process Engineer" if page_number == 0 and index == 0 else f"Role {index}"
                        ),
                        "unifiedUrlTitle": (
                            "Process-Engineer" if page_number == 0 and index == 0 else f"Role-{index}"
                        ),
                        "jobLocationShort": ["Chillicothe, USA, 64601<br/>"]
                    }}
                    for index in range(count)
                ]
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({"jobSearchResult": jobs, "totalJobs": 11}),
                    source="cloud-api",
                )

        fetcher = CloudFetcher()
        board = self.adapter.identify_board(
            "https://acme-careers.jobs.hr.cloud.sap/search/?locale=en_GB"
        )

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Engineer", location="USA"),
        )

        self.assertEqual(len(result.candidates), 11)
        self.assertEqual(
            result.candidates[0].url,
            "https://acme-careers.jobs.hr.cloud.sap/job/Process-Engineer/9500-en_GB/",
        )
        self.assertEqual(result.candidates[0].location, "Chillicothe, USA, 64601")
        self.assertEqual(result.trace["variant"], "cloud_sap")
        self.assertEqual(result.trace["page_count"], 2)
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])
        api_calls = fetcher.calls[1:]
        self.assertEqual([json.loads(call["data"])["pageNumber"] for call in api_calls], [0, 1])
        self.assertEqual(json.loads(api_calls[0]["data"])["keywords"], "Engineer")
        self.assertEqual(json.loads(api_calls[0]["data"])["location"], "USA")
        self.assertEqual(api_calls[0]["headers"]["X-CSRF-Token"], "csrf-123")
        self.assertEqual(
            api_calls[0]["headers"]["Origin"],
            "https://acme-careers.jobs.hr.cloud.sap",
        )

    def test_cloud_sap_live_contracts_build_verified_exact_urls(self):
        contracts = [
            (
                "wlgore",
                "https://wlgore.jobs.hr.cloud.sap/search/?locale=en_US",
                "Process Engineer",
                "https://wlgore.jobs.hr.cloud.sap/job/Process-Engineer/1816-en_US/",
                "Elkton, MD, USA, 21922-1220",
            ),
            (
                "colas",
                "https://colas.jobs.hr.cloud.sap/search/?locale=en_US",
                "Project Engineer Intern",
                "https://colas.jobs.hr.cloud.sap/job/Project-Engineer-Intern/117408-en_US/",
                "ANCHORAGE, ALASKA, USA",
            ),
            (
                "tbs",
                "https://tbs.jobs.hr.cloud.sap/search/?locale=en_US",
                "Industry Sales Executive",
                "https://tbs.jobs.hr.cloud.sap/job/Industry-Sales-Executive/760-en_GB/",
                "Melbourne, Victoria, Australia",
            ),
            (
                "novagr",
                "https://novagr.jobs.hr.cloud.sap/search/?locale=en_US",
                "Presales Engineer (B2B)",
                "https://novagr.jobs.hr.cloud.sap/job/Presales-Engineer-%28B2B%29/781-en_GB/",
                None,
            ),
        ]

        for tenant, board_url, title, exact_url, location in contracts:
            with self.subTest(tenant=tenant):
                fixture_dir = LIVE_CONTRACT_FIXTURES / tenant

                class ContractFetcher:
                    def __init__(self):
                        self.calls = []

                    def fetch(self, url, data=None, headers=None):
                        self.calls.append({"url": url, "data": data, "headers": headers})
                        fixture = "search.html" if data is None else "jobs.json"
                        return Page(
                            url=url,
                            final_url=url,
                            html=(fixture_dir / fixture).read_text(encoding="utf-8"),
                            source=f"{tenant}-live-contract",
                        )

                fetcher = ContractFetcher()
                board = self.adapter.identify_board(board_url)
                result = self.adapter.list_jobs(fetcher, board, JobQuery(title=title))

                self.assertEqual(len(result.candidates), 1)
                self.assertEqual(result.candidates[0].title, title)
                self.assertEqual(result.candidates[0].url, exact_url)
                self.assertEqual(result.candidates[0].location, location)
                self.assertTrue(result.trace["exact_title_found"])
                self.assertEqual(result.trace["page_count"], 1)
                self.assertEqual(len(fetcher.calls), 2)

    def test_cloud_sap_page_locale_overrides_stale_query_locale(self):
        fixture_dir = LIVE_CONTRACT_FIXTURES / "tbs"

        class LocaleFetcher:
            def fetch(self, url, data=None, headers=None):
                fixture = "search.html" if data is None else "jobs.json"
                return Page(
                    url=url,
                    final_url=url,
                    html=(fixture_dir / fixture).read_text(encoding="utf-8"),
                    source="tbs-live-contract",
                )

        board = self.adapter.identify_board(
            "https://tbs.jobs.hr.cloud.sap/search/?locale=en_US"
        )
        result = self.adapter.list_jobs(
            LocaleFetcher(),
            board,
            JobQuery(title="Industry Sales Executive"),
        )

        self.assertEqual(result.trace["locale"], "en_GB")
        self.assertTrue(result.candidates[0].url.endswith("/760-en_GB/"))

    def test_cloud_sap_rejects_missing_page_evidence_and_cross_origin_api(self):
        board = self.adapter.identify_board(
            "https://acme-careers.jobs.hr.cloud.sap/search/"
        )
        missing = self.adapter.list_jobs(StubFetcher("<html></html>"), board, JobQuery())
        self.assertEqual(missing.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

        class RedirectFetcher:
            def fetch(self, url, data=None, headers=None):
                if data is None:
                    return Page(
                        url=url,
                        final_url=url,
                        html='var CSRFToken = "token"; var appParams = {locale: "en_GB"};',
                    )
                return Page(
                    url=url,
                    final_url="https://evil.example/services/recruiting/v1/jobs",
                    html='{"jobSearchResult": [], "totalJobs": 0}',
                )

        redirected = self.adapter.list_jobs(RedirectFetcher(), board, JobQuery())
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_identifies_board_and_removes_detail_or_search_parameters(self):
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&career_ns=job_listing"
            "&career_job_req_id=987&keyword=analyst&rcm_site_locale=en_US#top"
        )

        self.assertEqual(board.identifier, "Acme")
        self.assertEqual(
            board.url,
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US",
        )
        self.assertIsNone(self.adapter.identify_board("https://careers.example.com/jobs"))

    def test_shared_legacy_root_is_typed_but_not_accepted_as_tenant_board(self):
        board = self.adapter.identify_board("https://career41.sapsf.com")

        self.assertIsNotNone(board)
        self.assertIsNone(board.identifier)
        fetcher = StubFetcher("should not be fetched")
        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Software Engineer"),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(fetcher.requested_urls, [])

    def test_page_evidence_recovers_tenant_route_from_successfactors_sso_config(self):
        page = Page(
            url="https://careers.example.com/",
            final_url="https://careers.example.com/",
            html="""
            <form method="get" action="/search/"><input name="q"></form>
            <script>
              j2w.init({
                "siteTypeId": 1,
                "ssoCompanyId": 'viacomcbsi',
                "ssoUrl": 'https://career41.sapsf.com'
              });
            </script>
            """,
            source="paramount-shaped-fixture",
        )

        board = self.adapter.identify_board_from_page(page)

        self.assertEqual(board.identifier, "custom:viacomcbsi")
        self.assertEqual(
            board.url,
            "https://careers.example.com/search/",
        )
        fetcher = StubFetcher(
            "<script>j2w.init({ssoCompanyId: 'viacomcbsi', "
            "ssoUrl: 'https://career41.sapsf.com'});</script>"
            '<a href="/job/Software-Engineer/123/">Software Engineer</a>'
        )
        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Software Engineer"),
        )

        self.assertEqual(result.reason_code, None)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            fetcher.requested_urls,
            [
                "https://careers.example.com/search/?q=Software+Engineer"
            ],
        )

    def test_custom_domain_paginates_title_search_and_binds_detail_to_tenant(self):
        def search_page(tenant, body):
            return (
                "<script>j2w.init({ssoCompanyId: '" + tenant + "', "
                "ssoUrl: 'https://career5.successfactors.eu'});</script>" + body
            )

        initial = Page(
            url="https://careers.example.com/search/",
            html=search_page("tenant_a", '<a href="?q=Data+Analyst&startrow=25">2</a>'),
        )
        board = self.adapter.identify_board_from_page(initial)
        self.assertEqual(board.identifier, "custom:tenant_a")
        self.assertEqual(board.url, "https://careers.example.com/search/")

        class PagedFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url, data=None, headers=None):
                self.requested_urls.append(url)
                pages = {
                    "https://careers.example.com/search/?q=Data+Analyst": search_page(
                        "tenant_a", '<a href="?q=Data+Analyst&startrow=25">2</a>'
                    ),
                    "https://careers.example.com/search/?q=Data+Analyst&startrow=25": search_page(
                        "tenant_a",
                        '<a href="?q=Data+Analyst&startrow=25">2</a>'
                        '<a href="?q=Data+Analyst&startrow=50">3</a>',
                    ),
                    "https://careers.example.com/search/?q=Data+Analyst&startrow=50": search_page(
                        "tenant_a", '<a href="/job/Data-Analyst/444/">Data Analyst</a>'
                    ),
                }
                return Page(url=url, final_url=url, html=pages[url], source="custom")

        fetcher = PagedFetcher()
        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        self.assertEqual([candidate.url for candidate in result.candidates], [
            "https://careers.example.com/job/Data-Analyst/444/",
        ])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(fetcher.requested_urls), 3)

    def test_custom_domain_rejects_fake_lookalike_and_cross_tenant_pagination(self):
        self.assertIsNone(self.adapter.identify_board_from_page(Page(
            url="https://careers.example.com/search/",
            html="j2w.init({ssoCompanyId: 'tenant_a', ssoUrl: 'https://career5.successfactors.eu'});",
        )))
        self.assertIsNone(self.adapter.identify_board_from_page(Page(
            url="https://careers.example.com/search/",
            html="<script>j2w.init({ssoCompanyId: 'tenant_a', ssoUrl: "
            "'https://career5.successfactors.eu.evil.example'});</script>",
        )))

        def page(tenant, links):
            return (
                "<script>j2w.init({ssoCompanyId: '" + tenant + "', "
                "ssoUrl: 'https://career5.successfactors.eu'});</script>" + links
            )

        board = self.adapter.identify_board_from_page(Page(
            url="https://careers.example.com/search/", html=page("tenant_a", "")
        ))

        class CrossTenantFetcher:
            def fetch(self, url, data=None, headers=None):
                pages = {
                    "https://careers.example.com/search/?q=Analyst": page(
                        "tenant_a", '<a href="?q=Analyst&startrow=25">2</a>'
                    ),
                    "https://careers.example.com/search/?q=Analyst&startrow=25": page(
                        "tenant_b", '<a href="/job/Analyst/7/">Analyst</a>'
                    ),
                }
                return Page(url=url, final_url=url, html=pages[url])

        result = self.adapter.list_jobs(CrossTenantFetcher(), board, JobQuery(title="Analyst"))
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["stop_reason"], "custom_tenant_identity_mismatch")

    def test_custom_domain_all_numbered_pages_visited_is_complete(self):
        def page(links):
            return (
                "<script>j2w.init({ssoCompanyId: 'tenant_a', "
                "ssoUrl: 'https://career5.successfactors.eu'});</script>" + links
            )

        board = self.adapter.identify_board_from_page(Page(
            url="https://careers.example.com/search/", html=page("")
        ))

        class CycleFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=page('<a href="?q=Analyst&startrow=25">2</a>'),
                )

        result = self.adapter.list_jobs(CycleFetcher(), board, JobQuery(title="Analyst"))
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["stop_reason"], "numbered_pagination_complete")

    def test_page_evidence_rejects_missing_ambiguous_or_unscoped_sso_identity(self):
        pages = [
            Page(
                url="https://careers.example.com/",
                html="j2w.init({ssoUrl: 'https://career41.sapsf.com'});",
            ),
            Page(
                url="https://careers.example.com/",
                html=(
                    "j2w.init({ssoCompanyId: 'alpha', ssoCompanyId: 'beta', "
                    "ssoUrl: 'https://career41.sapsf.com'});"
                ),
            ),
            Page(
                url="https://careers.example.com/",
                html=(
                    "j2w.init({ssoCompanyId: 'alpha', "
                    "ssoUrl: 'https://career41.sapsf.com/sfcareer/jobreqcareer'});"
                ),
            ),
        ]

        for page in pages:
            with self.subTest(html=page.html):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_tenant_bearing_legacy_and_cloud_routes_remain_supported(self):
        legacy_query = self.adapter.identify_board(
            "https://career41.sapsf.com/career?company=tenant_a"
        )
        legacy_host = self.adapter.identify_board(
            "https://tenant-a.jobs.sapsf.com/career"
        )
        cloud = self.adapter.identify_board(
            "https://tenant-a.jobs.hr.cloud.sap/search/?locale=en_US"
        )

        self.assertEqual(legacy_query.identifier, "tenant_a")
        self.assertEqual(legacy_host.identifier, "tenant-a.jobs.sapsf.com")
        self.assertEqual(cloud.identifier, "cloud:tenant-a.jobs.hr.cloud.sap")

    def test_identifies_legacy_jobreqcareer_jobid_as_detail_parameter(self):
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/sfcareer/jobreqcareer?"
            "jobId=662657&company=ARAMARKPROD"
        )

        self.assertEqual(board.identifier, "ARAMARKPROD")
        self.assertEqual(
            board.url,
            "https://career4.successfactors.com/sfcareer/jobreqcareer?company=ARAMARKPROD",
        )

    def test_parses_embedded_json_and_reconstructs_detail_urls(self):
        html = """
        <script type="application/json">
          {"results":[
            {"jobTitle":" Data Analyst ","jobReqId":"987","location":"New York"},
            {"title":"ML Engineer","career_job_req_id":654,
             "jobLocation":{"address":{"addressLocality":"Paris","addressCountry":"FR"}}}
          ]}
        </script>
        """
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US"
        )

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery(title="Data Analyst"))

        self.assertEqual(
            result.trace["search_urls"],
            [
                "https://career4.successfactors.com/career?company=Acme"
                "&rcm_site_locale=en_US&keyword=Data+Analyst"
            ],
        )
        self.assertEqual([candidate.title for candidate in result.candidates], ["Data Analyst", "ML Engineer"])
        self.assertIn("career_ns=job_listing", result.candidates[0].url)
        self.assertIn("career_job_req_id=987", result.candidates[0].url)
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].location, "Paris, FR")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_parses_json_inside_javascript_assignment_and_explicit_url(self):
        html = """
        <script>
          window.__JOBS__ = {"jobs":[
            {"jobTitle":"Platform Engineer","jobReqId":"123",
             "jobUrl":"/career?company=Acme&career_ns=job_listing&career_job_req_id=123"}
          ]};
        </script>
        """
        board = self.adapter.identify_board("https://acme.jobs.sapsf.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            "https://acme.jobs.sapsf.com/career?company=Acme"
            "&career_ns=job_listing&career_job_req_id=123",
        )
        self.assertEqual(result.candidates[0].raw, {"job_req_id": "123"})

    def test_extracts_job_links_and_deduplicates_embedded_records(self):
        html = """
        <a href="/career?company=Acme&amp;career_ns=job_listing&amp;career_job_req_id=987">
          Data Analyst
        </a>
        <script type="application/json">
          {"jobTitle":"Data Analyst","jobReqId":"987"}
        </script>
        """
        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertIn("career_job_req_id=987", result.candidates[0].url)

    def test_rejects_external_structured_urls(self):
        html = """
        <script type="application/json">
          {"jobTitle":"Fake Job","jobUrl":"https://evil.example/jobs/123"}
        </script>
        """
        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

    def test_parses_theme_ajax_nested_payload_and_pagination_metadata(self):
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US"
        )

        result = self.adapter.list_jobs(
            StubFetcher(FIXTURE.read_text(encoding="utf-8")),
            board,
            JobQuery(),
        )

        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Theme Platform Engineer", "AJAX Data Engineer"],
        )
        self.assertEqual(result.candidates[0].location, "Austin, TX")
        self.assertEqual(result.candidates[1].location, "Remote - US")
        self.assertTrue(all("company=Acme" in candidate.url for candidate in result.candidates))
        self.assertEqual(result.trace["pagination"], {
            "total_results": 42,
            "page_size": 10,
            "current_page": 2,
            "offset": 10,
            "has_more": True,
            "next_page": 3,
        })

    def test_rejects_other_successfactors_hosts_and_companies(self):
        html = """
        <script type="application/json">{"jobs":[
          {"jobTitle":"Other Company","jobReqId":"1",
           "jobUrl":"https://career4.successfactors.com/career?company=Other&career_job_req_id=1"},
          {"jobTitle":"Other Host","jobReqId":"2",
           "jobUrl":"https://career5.successfactors.com/career?company=Acme&career_job_req_id=2"},
          {"jobTitle":"Bad Scheme","jobReqId":"4",
           "jobUrl":"ftp://career4.successfactors.com/career?company=Acme&career_job_req_id=4"},
          {"jobTitle":"Ambiguous Company","jobReqId":"5",
           "jobUrl":"/career?company=Acme&company=Other&career_job_req_id=5"},
          {"jobTitle":"Relative Same Tenant","jobReqId":"3",
           "jobUrl":"/career?career_job_req_id=3"}
        ]}</script>
        """
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme"
        )

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual([candidate.title for candidate in result.candidates], ["Relative Same Tenant"])
        self.assertIn("company=Acme", result.candidates[0].url)

    def test_returns_retryable_fetch_failure(self):
        class FailingFetcher:
            def fetch(self, url, data=None, headers=None):
                raise FetchError("offline")

        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme"
        )

        result = self.adapter.list_jobs(FailingFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertIn("offline", result.trace["error"])

    def test_returns_structured_errors_for_missing_identifier_invalid_and_empty_data(self):
        missing = JobBoard(
            url="https://career4.successfactors.com/career",
            provider="successfactors",
        )
        unsupported = self.adapter.list_jobs(StubFetcher(""), missing, JobQuery())
        self.assertEqual(unsupported.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")
        invalid = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[}</script>'),
            board,
            JobQuery(),
        )
        empty = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[]}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(empty.candidates, [])


if __name__ == "__main__":
    unittest.main()
