import unittest
import json
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.workable import WorkableAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parents[1] / "samples" / "sites" / "apply.workable.com" / "acme"
LIVE_SHAPE_FIXTURES = (
    Path(__file__).parents[1] / "samples" / "sites" / "apply.workable.com" / "huzzle"
)


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
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com/bad.slug"))
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com:bad/acme"))

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


if __name__ == "__main__":
    unittest.main()
