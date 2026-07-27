import unittest
import json
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery, PageAwareProviderAdapter
from job_source_agent.providers.workable import WorkableAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parents[1] / "samples" / "sites" / "apply.workable.com" / "acme"
LIVE_SHAPE_FIXTURES = (
    Path(__file__).parents[1] / "samples" / "sites" / "apply.workable.com" / "huzzle"
)
CUSTOM_DOMAIN_FIXTURES = (
    Path(__file__).parents[1]
    / "samples"
    / "sites"
    / "apply.workable.com"
    / "custom-domain"
)
WIDGET_API_URL = (
    "https://apply.workable.com/api/v1/widget/accounts/149632"
    "?origin=embed&callback=whrcallback"
)


def widget_page(
    *,
    page_url="https://www.example.com/careers",
    account_id="149632",
    asset_url="https://www.workable.com/assets/embed.js",
):
    return Page(
        url=page_url,
        final_url=page_url,
        html=f"""
            <script src="/assets/site.js"></script>
            <script src="{asset_url}"></script>
            <script>whr_embed({account_id}, {{detail: "titles"}});</script>
            <div id="whr_embed_hook"></div>
        """,
        source="career-page-fixture",
    )


def widget_response(*, employer="Mention Me", jobs=None):
    if jobs is None:
        jobs = [
            {
                "title": "Product Growth Marketing Manager",
                "shortcode": "EA1650B1D6",
                "published_on": "2026-07-21",
                "url": "https://apply.workable.com/j/EA1650B1D6",
                "shortlink": "https://apply.workable.com/j/EA1650B1D6",
                "application_url": "https://apply.workable.com/j/EA1650B1D6/apply",
                "telecommuting": False,
                "locations": [
                    {
                        "city": "London",
                        "region": "England",
                        "country": "United Kingdom",
                        "hidden": False,
                    }
                ],
            },
            {
                "title": "Remote Product Designer",
                "shortcode": "REMOTE123",
                "published_on": "2026-07-20",
                "url": "https://apply.workable.com/j/REMOTE123",
                "shortlink": "https://apply.workable.com/j/REMOTE123",
                "application_url": "https://apply.workable.com/j/REMOTE123/apply",
                "telecommuting": True,
                "locations": [],
            },
        ]
    return "/**/whrcallback(" + json.dumps({"name": employer, "jobs": jobs}) + ")"


class StubFetcher:
    def __init__(self, html="", error=None):
        self.html = html
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error:
            raise self.error
        return Page(url=url, final_url=url, html=self.html, source="workable-fixture")


class RoutingFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append(
            {
                "url": url,
                "data": json.loads(data.decode("utf-8")) if data else None,
                "headers": headers,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        return Page(url=url, final_url=url, html=response, source="workable-fixture")


class WorkableAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WorkableAdapter()

    def test_recognizes_only_public_workable_host(self):
        self.assertTrue(self.adapter.recognizes("https://apply.workable.com/acme/"))
        self.assertTrue(self.adapter.recognizes("https://APPLY.WORKABLE.COM/acme/j/ABC123/"))
        self.assertTrue(self.adapter.recognizes("http://apply.workable.com:80/acme/"))
        self.assertFalse(self.adapter.recognizes("https://workable.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com.example.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com:bad/acme"))
        self.assertFalse(self.adapter.recognizes("ftp://apply.workable.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://user@apply.workable.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com:8443/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com:80/acme"))
        self.assertFalse(self.adapter.recognizes("http://apply.workable.com:443/acme"))

    def test_identifies_canonical_account_board_from_list_or_detail_url(self):
        board = self.adapter.identify_board(
            "https://apply.workable.com/acme-inc/j/ABC123/?utm_source=test"
        )

        self.assertEqual(
            board,
            JobBoard(
                url="https://apply.workable.com/acme-inc/",
                provider="workable",
                identifier="acme-inc",
            ),
        )
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com/"))
        self.assertIsNone(
            self.adapter.identify_board("https://apply.workable.com/j/EA1650B1D6")
        )
        self.assertIsNone(
            self.adapter.identify_board(
                "https://apply.workable.com/api/v1/widget/accounts/149632"
            )
        )
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com/bad.slug"))
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com:bad/acme"))

    def test_identifies_runtime_only_numeric_widget_from_first_party_page(self):
        board = self.adapter.identify_board_from_page(widget_page())

        self.assertIsInstance(self.adapter, PageAwareProviderAdapter)
        self.assertEqual(
            board,
            JobBoard(
                url="https://www.example.com/careers",
                provider="workable",
                identifier="widget:149632",
                replay_safe=False,
            ),
        )

    def test_numeric_widget_page_requires_exact_unambiguous_evidence(self):
        valid_protocol_relative = widget_page(
            asset_url="//www.workable.com/assets/embed.js"
        )
        self.assertIsNotNone(
            self.adapter.identify_board_from_page(valid_protocol_relative)
        )

        invalid_pages = [
            widget_page(asset_url="https://www.workable.com.evil.test/assets/embed.js"),
            widget_page(asset_url="https://www.workable.com/assets/embed.js?account=149632"),
            Page(
                url="https://www.example.com/careers",
                html=(
                    '<script src="https://www.workable.com/assets/embed.js"></script>'
                    "<script>whr_embed(149632);whr_embed(149633);</script>"
                    '<div id="whr_embed_hook"></div>'
                ),
            ),
            Page(
                url="https://www.example.com/careers",
                html=(
                    '<script src="https://www.workable.com/assets/embed.js"></script>'
                    "<script>whr_embed(149632);</script>"
                ),
            ),
            widget_page(page_url="http://www.example.com/careers"),
            widget_page(page_url="https://user@www.example.com/careers"),
            widget_page(page_url="https://127.0.0.1/careers"),
        ]
        for page in invalid_pages:
            with self.subTest(page=page.url, html=page.html[:100]):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

        non_executable_ids = Page(
            url="https://www.example.com/careers",
            html=(
                '<script src="https://www.workable.com/assets/embed.js"></script>'
                "<script>"
                "// whr_embed(111111);\n"
                "const old = 'whr_embed(222222)';"
                "/* whr_embed(333333); */"
                "whr_embed(149632, {detail: 'titles'});"
                "</script>"
                '<div id="whr_embed_hook"></div>'
            ),
        )
        self.assertEqual(
            self.adapter.identify_board_from_page(non_executable_ids).identifier,
            "widget:149632",
        )

    def test_numeric_widget_lists_complete_official_inventory(self):
        board = self.adapter.identify_board_from_page(widget_page())
        fetcher = RoutingFetcher([widget_response()])

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Product Growth Marketing Manager", location="London"),
        )

        self.assertEqual(fetcher.requests[0]["url"], WIDGET_API_URL)
        self.assertEqual(
            fetcher.requests[0]["headers"],
            {
                "Accept": "application/javascript, application/json",
                "Referer": "https://www.example.com/careers",
            },
        )
        self.assertEqual(
            [
                (candidate.title, candidate.url, candidate.location)
                for candidate in result.candidates
            ],
            [
                (
                    "Product Growth Marketing Manager",
                    "https://apply.workable.com/j/EA1650B1D6",
                    "London, England, United Kingdom",
                ),
                (
                    "Remote Product Designer",
                    "https://apply.workable.com/j/REMOTE123",
                    "Remote",
                ),
            ],
        )
        self.assertEqual(result.reason_code, None)
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["employer_name"], "Mention Me")
        self.assertEqual(len(result.employer_evidence), 2)
        self.assertEqual(
            result.employer_evidence[0].to_trace_payload(),
            {
                "employer_name": "Mention Me",
                "descriptor_terms": [],
                "evidence_url": WIDGET_API_URL,
                "opening_url": "https://apply.workable.com/j/EA1650B1D6",
                "extraction_method": "workable_widget_employer",
            },
        )

    def test_numeric_widget_fetch_and_response_fail_closed(self):
        board = self.adapter.identify_board_from_page(widget_page())
        failed = self.adapter.list_jobs(
            RoutingFetcher([FetchError("offline")]),
            board,
            JobQuery(),
        )
        redirected = self.adapter.list_jobs(
            RoutingFetcher(
                [
                    Page(
                        url=WIDGET_API_URL,
                        final_url=(
                            "https://apply.workable.com/api/v1/widget/accounts/149633"
                            "?origin=embed&callback=whrcallback"
                        ),
                        html=widget_response(),
                    )
                ]
            ),
            board,
            JobQuery(),
        )
        malformed = self.adapter.list_jobs(
            RoutingFetcher(['whrcallback({"name":"Acme","name":"Other","jobs":[]})']),
            board,
            JobQuery(),
        )
        downgraded = self.adapter.list_jobs(
            RoutingFetcher(
                [
                    Page(
                        url=WIDGET_API_URL,
                        final_url=WIDGET_API_URL.replace("https://", "http://"),
                        html=widget_response(),
                    )
                ]
            ),
            board,
            JobQuery(),
        )

        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(downgraded.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(redirected.inventory_complete)
        self.assertFalse(malformed.inventory_complete)

    def test_numeric_widget_rejects_cross_url_and_duplicate_openings(self):
        board = self.adapter.identify_board_from_page(widget_page())
        valid_job = json.loads(
            widget_response().removeprefix("/**/whrcallback(").removesuffix(")")
        )["jobs"][0]
        cross_url = dict(valid_job)
        cross_url["url"] = "https://apply.workable.com/j/OTHER"
        duplicate = [valid_job, dict(valid_job)]

        cross_result = self.adapter.list_jobs(
            RoutingFetcher([widget_response(jobs=[cross_url])]),
            board,
            JobQuery(),
        )
        duplicate_result = self.adapter.list_jobs(
            RoutingFetcher([widget_response(jobs=duplicate)]),
            board,
            JobQuery(),
        )

        self.assertEqual(cross_result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(duplicate_result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(cross_result.inventory_complete)
        self.assertFalse(duplicate_result.inventory_complete)

    def test_numeric_widget_empty_inventory_is_verified_and_complete(self):
        board = self.adapter.identify_board_from_page(widget_page())

        result = self.adapter.list_jobs(
            RoutingFetcher([widget_response(jobs=[])]),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.inventory_complete)

    def test_lists_nested_embedded_json_jobs_and_normalizes_detail_urls(self):
        fetcher = StubFetcher(
            """
            <script id="__NEXT_DATA__" type="application/json">
              {"props":{"pageProps":{"jobs":[
                {"title":"  AI Engineer  ","shortcode":"ABC123","location":" New York "},
                {"name":"Product Manager","shortCode":"PM-456", "location":{
                  "city":"Paris","region":"Ile-de-France","country":"FR"
                }},
                {"title":"Incomplete"}
              ]}}}
            </script>
            """
        )
        board = self.adapter.identify_board("https://apply.workable.com/acme/")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="AI Engineer"))

        self.assertEqual(fetcher.requested_urls, ["https://apply.workable.com/acme/"])
        self.assertEqual([item.title for item in result.candidates], ["AI Engineer", "Product Manager"])
        self.assertEqual(result.candidates[0].url, "https://apply.workable.com/acme/j/ABC123/")
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].url, "https://apply.workable.com/acme/j/PM-456/")
        self.assertEqual(result.candidates[1].location, "Paris, Ile-de-France, FR")
        self.assertEqual(result.candidates[0].raw, {"shortcode": "ABC123"})
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.trace["response_source"], "workable-fixture")

    def test_accepts_script_assignment_urls_and_deduplicates_candidates(self):
        fetcher = StubFetcher(
            """
            <script>
              window.__INITIAL_STATE__ = {"jobs":[
                {"title":"Data Analyst","url":"/acme/j/DATA_1/"},
                {"title":"Data Analyst duplicate","applicationUrl":
                  "https://apply.workable.com/acme/j/DATA_1/"},
                {"title":"External","url":"https://evil.example/jobs/NOPE"}
              ]};
            </script>
            """
        )
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://apply.workable.com/acme/j/DATA_1/")

    def test_parses_public_links_nested_payload_and_pagination_metadata(self):
        html = (FIXTURES / "public-nested.html").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(
            [(item.title, item.url, item.location) for item in result.candidates],
            [
                (
                    "Machine Learning Engineer",
                    "https://apply.workable.com/acme/j/ML-100/",
                    None,
                ),
                (
                    "Platform Engineer",
                    "https://apply.workable.com/acme/j/PLAT_2/",
                    "Remote, US",
                ),
            ],
        )
        self.assertEqual(result.trace["pagination"]["currentPage"], 1)
        self.assertEqual(result.trace["pagination"]["totalPages"], 3)
        self.assertEqual(result.trace["pagination"]["hasNextPage"], True)
        self.assertEqual(result.trace["public_link_count"], 3)
        self.assertFalse(result.inventory_complete)

    def test_rejects_cross_account_and_unsafe_explicit_urls_without_shortcode_bypass(self):
        fetcher = StubFetcher(
            """
            <script type="application/json">{"jobs":[
              {"title":"Other account","shortcode":"SAFE1",
               "url":"https://apply.workable.com/other/j/EVIL1/"},
              {"title":"Credentials","url":"https://user@apply.workable.com/acme/j/EVIL2/"},
              {"title":"Port","url":"https://apply.workable.com:8443/acme/j/EVIL3/"},
              {"title":"Query","url":"/acme/j/EVIL4/?redirect=other"},
              {"title":"Malformed IPv6","url":"https://[broken/j/EVIL5/"},
              {"title":"Valid relative","url":"/acme/j/GOOD5/"}
            ]}</script>
            <a href="/other/j/NOPE/">Cross account anchor</a>
            """
        )
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual([item.title for item in result.candidates], ["Valid relative"])
        self.assertEqual(
            result.candidates[0].url,
            "https://apply.workable.com/acme/j/GOOD5/",
        )

    def test_fetch_failure_has_retryable_provider_reason(self):
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(
            StubFetcher(error=FetchError("offline")),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.candidates, [])

    def test_missing_identifier_returns_structured_failure(self):
        board = JobBoard(url="https://apply.workable.com/", provider="workable")

        result = self.adapter.list_jobs(StubFetcher(""), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])

    def test_invalid_or_unrelated_payload_returns_invalid_structured_data(self):
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        missing_json = self.adapter.list_jobs(StubFetcher("<html>no jobs</html>"), board, JobQuery())
        malformed = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{bad json}</script>'), board, JobQuery()
        )
        unrelated = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"company":"Acme"}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(missing_json.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(unrelated.reason_code, "INVALID_STRUCTURED_DATA")

    def test_empty_jobs_returns_empty_provider_response(self):
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[]}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 0)
        self.assertTrue(result.inventory_complete)

    def test_client_rendered_shell_uses_public_cursor_api_and_stops_on_exact_title(self):
        shell = (LIVE_SHAPE_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        page_1 = (LIVE_SHAPE_FIXTURES / "jobs-page-1.json").read_text(encoding="utf-8")
        page_2 = (LIVE_SHAPE_FIXTURES / "jobs-page-2.json").read_text(encoding="utf-8")
        fetcher = RoutingFetcher([shell, page_1, page_2])
        board = self.adapter.identify_board("https://apply.workable.com/huzzle/")

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Graphic Designer Remote"),
        )

        self.assertEqual(
            [request["url"] for request in fetcher.requests],
            [
                "https://apply.workable.com/huzzle/",
                "https://apply.workable.com/api/v3/accounts/huzzle/jobs",
                "https://apply.workable.com/api/v3/accounts/huzzle/jobs",
            ],
        )
        self.assertEqual(fetcher.requests[1]["data"]["query"], "Graphic Designer Remote")
        self.assertNotIn("token", fetcher.requests[1]["data"])
        self.assertEqual(fetcher.requests[2]["data"]["token"], "opaque-page-token-2")
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Operations Associate", "Graphic Designer - Remote"],
        )
        self.assertEqual(
            result.candidates[1].url,
            "https://apply.workable.com/huzzle/j/TARGET1002/",
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["api_page_count"], 2)
        self.assertEqual(result.trace["total_found"], 516)
        self.assertFalse(result.inventory_complete)

    def test_verified_custom_domain_uses_account_api_and_closes_pagination(self):
        shell = (CUSTOM_DOMAIN_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        page_1 = (CUSTOM_DOMAIN_FIXTURES / "jobs-page-1.json").read_text(encoding="utf-8")
        page_2 = (CUSTOM_DOMAIN_FIXTURES / "jobs-page-2.json").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://apply.workable.com/customco/")
        fetcher = RoutingFetcher(
            [
                Page(
                    url=board.url,
                    final_url="https://careers.customco.example/",
                    html=shell,
                    source="workable-custom-domain-fixture",
                ),
                page_1,
                page_2,
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Clinical Systems Engineer"),
        )

        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(
            fetcher.requests[1]["url"],
            "https://apply.workable.com/api/v3/accounts/customco/jobs",
        )
        self.assertEqual(fetcher.requests[1]["data"]["query"], "Clinical Systems Engineer")
        self.assertEqual(fetcher.requests[2]["data"]["token"], "custom-domain-page-2")
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [
                "https://apply.workable.com/customco/j/FIRST2001/",
                "https://apply.workable.com/customco/j/TARGET2002/",
            ],
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(
            result.trace["account_uid"],
            "12345678-1234-4123-8123-123456789abc",
        )

    def test_custom_domain_requires_matching_workable_tenant_metadata(self):
        shell = (CUSTOM_DOMAIN_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://apply.workable.com/customco/")
        mismatched_shell = shell.replace(
            'content="customco"',
            'content="another-account"',
        )
        fetcher = RoutingFetcher(
            [
                Page(
                    url=board.url,
                    final_url="https://careers.customco.example/",
                    html=mismatched_shell,
                )
            ]
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertEqual(len(fetcher.requests), 1)

    def test_custom_domain_rejects_api_records_from_another_account(self):
        shell = (CUSTOM_DOMAIN_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://apply.workable.com/customco/")
        foreign_page = json.dumps(
            {
                "total": 1,
                "results": [
                    {
                        "title": "Foreign Role",
                        "shortcode": "FOREIGN1",
                        "accountUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    }
                ],
                "nextPage": None,
            }
        )
        fetcher = RoutingFetcher(
            [
                Page(
                    url=board.url,
                    final_url="https://careers.customco.example/",
                    html=shell,
                ),
                foreign_page,
            ]
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_cursor_api_is_bounded_and_repeated_token_stops_pagination(self):
        shell = (LIVE_SHAPE_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        response = json.dumps(
            {
                "total": 100,
                "results": [{"title": "Another Role", "shortcode": "ROLE1"}],
                "nextPage": "same-token",
            }
        )
        fetcher = RoutingFetcher([shell, response, response])
        board = self.adapter.identify_board("https://apply.workable.com/huzzle/")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Missing Role"))

        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(result.trace["api_page_count"], 2)
        self.assertEqual(len(result.candidates), 1)

    def test_cursor_api_never_fetches_more_than_five_pages(self):
        shell = (LIVE_SHAPE_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        pages = [
            json.dumps(
                {
                    "total": 100,
                    "results": [{"title": f"Role {index}", "shortcode": f"ROLE{index}"}],
                    "nextPage": f"token-{index + 1}",
                }
            )
            for index in range(1, 7)
        ]
        fetcher = RoutingFetcher([shell, *pages])
        board = self.adapter.identify_board("https://apply.workable.com/huzzle/")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Missing Role"))

        self.assertEqual(len(fetcher.requests), 6)
        self.assertEqual(result.trace["api_page_count"], 5)
        self.assertEqual(len(result.candidates), 5)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_rejects_cross_account_board_and_api_redirects(self):
        board = self.adapter.identify_board("https://apply.workable.com/huzzle/")
        cross_board = RoutingFetcher(
            [
                Page(
                    url=board.url,
                    final_url="https://apply.workable.com/other/",
                    html="",
                )
            ]
        )

        board_result = self.adapter.list_jobs(cross_board, board, JobQuery())

        shell = (LIVE_SHAPE_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        cross_api = RoutingFetcher(
            [
                shell,
                Page(
                    url="https://apply.workable.com/api/v3/accounts/huzzle/jobs",
                    final_url="https://apply.workable.com/api/v3/accounts/other/jobs",
                    html='{"total":0,"results":[]}',
                ),
            ]
        )
        api_result = self.adapter.list_jobs(cross_api, board, JobQuery())

        self.assertEqual(board_result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(api_result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_partial_cursor_failure_keeps_candidates_and_records_error(self):
        shell = (LIVE_SHAPE_FIXTURES / "public-shell.html").read_text(encoding="utf-8")
        page_1 = (LIVE_SHAPE_FIXTURES / "jobs-page-1.json").read_text(encoding="utf-8")
        fetcher = RoutingFetcher([shell, page_1, FetchError("page two unavailable")])
        board = self.adapter.identify_board("https://apply.workable.com/huzzle/")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Missing Role"))

        self.assertIsNone(result.reason_code)
        self.assertFalse(result.retryable)
        self.assertEqual([candidate.title for candidate in result.candidates], ["Operations Associate"])
        self.assertEqual(result.trace["api_page_count"], 1)
        self.assertEqual(len(result.trace["errors"]), 1)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])


if __name__ == "__main__":
    unittest.main()
