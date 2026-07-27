import copy
import json
import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.job_board import (
    DiscoveredJobBoard,
    JobBoardPortfolio,
    is_replay_safe_job_board,
)
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.haley_marketing import (
    ADAPTER,
    HaleyMarketingAdapter,
)
from job_source_agent.snapshot import sanitize_snapshot_body
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://jobs.example.test/"
INVENTORY_PATH = "/json/index.smpl"
DETAIL_URL = BOARD_URL + "jb/platform-engineer/101"


def board_html(
    *,
    script_url="/js/combobo.js",
    endpoint=INVENTORY_PATH,
    form_action="/index.smpl",
    arg="list_posts",
    include_result_contract=True,
    include_detail_contract=True,
):
    result_contract = (
        "var list = r.ResultSet.list;"
        "var total = r.ResultSet.list_meta.total;"
        "var first = r.ResultSet.list_meta.first;"
        "var pp = r.ResultSet.list_meta.pp;"
        if include_result_contract
        else ""
    )
    detail_contract = (
        "return '/jb/' + this.SEO_PERMALINK + '/' + this.POST_ID;"
        if include_detail_contract
        else ""
    )
    return f"""
        <html>
          <head>
            <link rel="stylesheet" href="/css/hmg-jb.css?v=4.3.77">
          </head>
          <body>
            <form action="{form_action}" id="JBSearchList_form">
              <input type="hidden" name="arg" value="{arg}">
              <input type="hidden" name="pp" value="5">
              <input type="hidden" name="pid" value="gwt">
              <input type="hidden" name="h" value="0123456789abcdef0123456789abcdef">
              <input type="hidden" name="t" value="1700000000">
              <input type="hidden" name="first" value="0">
              <input type="text" name="keywords" id="keywords" value="">
            </form>
            <script>
              var jsonp_url = "{endpoint}";
              jQuery.getJSON(
                jsonp_url,
                jQuery("#JBSearchList_form").serializeArray(),
                renderJBQuery
              );
              {result_contract}
              {detail_contract}
            </script>
            <script src="{script_url}"></script>
          </body>
        </html>
    """


def record(
    post_id="101",
    *,
    title="Platform Engineer",
    location="Boston, MA",
    slug="platform-engineer",
):
    return {
        "POST_ID": post_id,
        "POST_TITLE": title,
        "POST_LOCATION": location,
        "POST_JOB_NUMBER": f"JOB-{post_id}",
        "SEO_PERMALINK": slug,
        "POST_SEO_URL": BOARD_URL + f"jb/{slug}/{post_id}",
        "POST_ARCHIVED": "",
        "POST_EXPIRATION_DATE": "2026-08-26",
    }


def inventory(records, *, total=None, first=-1, pp=1, ticket=None):
    result_set = {
        "list_meta": {
            "total": len(records) if total is None else total,
            "first": first,
            "pp": pp,
        },
        "list": records,
    }
    if ticket is not None:
        result_set["ticket"] = ticket
    return {
        "ResultSet": {
            **result_set,
        }
    }


def search_entry_html():
    return """
        <html>
          <head>
            <link rel="stylesheet" href="/css/hmg-jb.css?v=4.3.77">
          </head>
          <body>
            <form name="searchform" action="/index.smpl" method="POST" id="jb_search">
              <input type="hidden" name="arg" value="jb_search_results">
              <input type="hidden" name="t" value="1700000000">
              <input type="hidden" name="action" value="1">
              <input type="hidden" name="proximity" value="">
              <input type="hidden" name="view" value="">
              <input type="text" name="keywords" id="keywords" value="">
            </form>
            <script src="/js/combobo.js"></script>
          </body>
        </html>
    """


def response(url, html, *, final_url=None):
    return Page(
        url=url,
        final_url=url if final_url is None else final_url,
        html=html,
        source="haley-marketing-memory-fixture",
    )


class RecordingFetcher:
    def __init__(self, responses=(), *, error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        item = self.responses.pop(0)
        if isinstance(item, Page):
            return item
        return response(url, item)


class HaleyMarketingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = HaleyMarketingAdapter()
        self.page = Page(BOARD_URL, board_html(), source="hmg-board")
        self.board = self.adapter.identify_board_from_page(self.page)

    def list_jobs(self, payload, query=None, *, board_page=None):
        fetcher = RecordingFetcher(
            [
                board_page
                or response(BOARD_URL, board_html(), final_url=BOARD_URL),
                payload
                if isinstance(payload, Page)
                else response(
                    BOARD_URL.rstrip("/") + INVENTORY_PATH,
                    payload if isinstance(payload, str) else json.dumps(payload),
                ),
            ]
        )
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            query or JobQuery(title="Platform Engineer", location="Boston, MA"),
        )
        return result, fetcher

    def assert_invalid_inventory(self, payload):
        result, _ = self.list_jobs(payload)
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_is_typed_page_aware_provider_and_binds_tenant_to_hostname(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertEqual(
            self.board,
            JobBoard(
                BOARD_URL,
                "haley_marketing",
                "custom:jobs.example.test",
                replay_safe=True,
            ),
        )
        self.assertFalse(self.adapter.recognizes(BOARD_URL))
        self.assertIsNone(self.adapter.identify_board(BOARD_URL))
        self.assertTrue(is_replay_safe_job_board(self.board))
        portfolio = JobBoardPortfolio(
            (
                DiscoveredJobBoard(
                    board=self.board,
                    detection_method="page_evidence",
                    evidence_url=BOARD_URL,
                ),
            ),
            eligible_set_complete=True,
        )
        self.assertIsNotNone(portfolio.to_checkpoint_payload())

    def test_page_detection_requires_the_complete_hmg_contract(self):
        weak_pages = (
            board_html(script_url=""),
            board_html(form_action="/jobs"),
            board_html(arg="search"),
            board_html(endpoint="/api/jobs"),
            board_html(include_result_contract=False),
            board_html(include_detail_contract=False),
            board_html().replace('name="keywords"', 'name="query"'),
            board_html().replace('id="JBSearchList_form"', 'id="job-search"'),
            '<script src="/js/combobo.js"></script>',
            '<form action="/index.smpl"><input name="arg" value="list_posts"></form>',
        )
        for html in weak_pages:
            with self.subTest(html=html[-160:]):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(BOARD_URL, html))
                )

    def test_page_detection_rejects_unsafe_urls_and_cross_origin_contracts(self):
        cases = (
            Page("http://jobs.example.test/", board_html()),
            Page("https://user@jobs.example.test/", board_html()),
            Page("https://jobs.example.test:8443/", board_html()),
            Page(
                BOARD_URL,
                board_html(script_url="https://cdn.evil.test/js/combobo.js"),
            ),
            Page(
                BOARD_URL,
                board_html(endpoint="https://api.evil.test/json/index.smpl"),
            ),
            Page(
                BOARD_URL,
                board_html(form_action="https://jobs.evil.test/index.smpl"),
            ),
        )
        for page in cases:
            with self.subTest(url=page.url, html=page.html[-120:]):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_lists_title_filtered_exact_candidate_from_official_inventory(self):
        result, fetcher = self.list_jobs(inventory([record()], pp=5))

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Platform Engineer")
        self.assertEqual(candidate.location, "Boston, MA")
        self.assertEqual(candidate.url, DETAIL_URL)
        self.assertEqual(candidate.provider, "haley_marketing")
        self.assertEqual(
            candidate.raw,
            {
                "post_id": "101",
                "job_number": "JOB-101",
                "expiration_date": "2026-08-26",
            },
        )

        self.assertEqual(fetcher.requests[0], (BOARD_URL, None, None))
        inventory_request = fetcher.requests[1]
        parsed = urlparse(inventory_request[0])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "jobs.example.test")
        self.assertEqual(parsed.path, INVENTORY_PATH)
        self.assertIsNone(inventory_request[1])
        self.assertEqual(query["arg"], ["list_posts"])
        self.assertEqual(query["pid"], ["gwt"])
        self.assertEqual(query["h"], ["0123456789abcdef0123456789abcdef"])
        self.assertEqual(query["t"], ["1700000000"])
        self.assertEqual(query["first"], ["0"])
        self.assertEqual(query["keywords"], ["Platform Engineer"])
        self.assertNotIn("location", query)
        self.assertNotIn("0123456789abcdef", json.dumps(result.trace))
        self.assertIn("%5Bredacted%5D", result.trace["api_urls"][0])

    def test_verified_empty_title_filtered_inventory_is_complete(self):
        result, _ = self.list_jobs(inventory([], total=0, pp=5))

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.candidates, [])

    def test_collects_all_pages_before_marking_inventory_complete(self):
        refreshed_ticket = {
            "h": "fedcba9876543210fedcba9876543210",
            "t": "1700000001",
        }
        page_one = inventory(
            [
                record(
                    str(100 + index),
                    title=f"Related Engineer {index}",
                    slug=f"related-engineer-{index}",
                )
                for index in range(1, 6)
            ],
            total=6,
            first=5,
            pp=5,
            ticket=refreshed_ticket,
        )
        page_two = inventory(
            [
                record(
                    "106",
                    title="Senior Platform Engineer",
                    location="Cambridge, MA",
                    slug="senior-platform-engineer",
                )
            ],
            total=6,
            first=-1,
            pp=5,
        )
        fetcher = RecordingFetcher(
            [
                response(BOARD_URL, board_html()),
                json.dumps(page_one),
                json.dumps(page_two),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Missing Exact Target"),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(
            [item.raw["post_id"] for item in result.candidates],
            ["101", "102", "103", "104", "105", "106"],
        )
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(
            parse_qs(urlparse(fetcher.requests[2][0]).query)["first"],
            ["5"],
        )
        refreshed_query = parse_qs(urlparse(fetcher.requests[2][0]).query)
        self.assertEqual(refreshed_query["h"], [refreshed_ticket["h"]])
        self.assertEqual(refreshed_query["t"], [refreshed_ticket["t"]])

    def test_sanitized_snapshot_tickets_remain_replayable_across_pages(self):
        page_one = inventory(
            [
                record(
                    str(300 + index),
                    title=f"Related Engineer {index}",
                    slug=f"related-engineer-replay-{index}",
                )
                for index in range(1, 6)
            ],
            total=6,
            first=5,
            pp=5,
            ticket={
                "h": "fedcba9876543210fedcba9876543210",
                "t": "1700000001",
            },
        )
        page_two = inventory(
            [
                record(
                    "306",
                    title="Platform Engineer",
                    location="Boston, MA",
                    slug="platform-engineer-replay",
                )
            ],
            total=6,
            first=-1,
            pp=5,
        )
        sanitized_board = sanitize_snapshot_body(board_html())
        sanitized_page_one = sanitize_snapshot_body(json.dumps(page_one))
        sanitized_page_two = sanitize_snapshot_body(json.dumps(page_two))
        replay_board = self.adapter.identify_board_from_page(
            Page(BOARD_URL, sanitized_board, source="snapshot_replay")
        )
        fetcher = RecordingFetcher(
            [sanitized_board, sanitized_page_one, sanitized_page_two]
        )

        result = self.adapter.list_jobs(
            fetcher,
            replay_board,
            JobQuery(title="Platform Engineer", location="Boston, MA"),
        )

        self.assertEqual(len(result.candidates), 6)
        self.assertTrue(result.inventory_complete)
        first_query = parse_qs(urlparse(fetcher.requests[1][0]).query)
        second_query = parse_qs(urlparse(fetcher.requests[2][0]).query)
        self.assertEqual(
            first_query["h"],
            ["00000000000000000000000000000000"],
        )
        self.assertEqual(first_query["t"], ["1000000000"])
        self.assertEqual(
            second_query["h"],
            ["00000000000000000000000000000000"],
        )
        self.assertEqual(second_query["t"], ["1000000000"])
        self.assertNotIn(
            "fedcba9876543210fedcba9876543210",
            sanitized_page_one,
        )

    def test_same_title_wrong_location_does_not_hide_later_correct_location(self):
        page_one_records = [
            record(
                "201",
                title="Platform Engineer",
                location="Austin, TX",
                slug="platform-engineer-austin",
            ),
            *[
                record(
                    str(201 + index),
                    title=f"Related Engineer {index}",
                    location="Remote",
                    slug=f"related-engineer-location-{index}",
                )
                for index in range(1, 5)
            ],
        ]
        page_two_record = record(
            "206",
            title="Platform Engineer",
            location="Boston, MA",
            slug="platform-engineer-boston",
        )
        fetcher = RecordingFetcher(
            [
                response(BOARD_URL, board_html()),
                json.dumps(
                    inventory(
                        page_one_records,
                        total=6,
                        first=5,
                        pp=5,
                    )
                ),
                json.dumps(
                    inventory(
                        [page_two_record],
                        total=6,
                        first=-1,
                        pp=5,
                    )
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Platform Engineer", location="Boston, MA"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(fetcher.requests), 3)
        self.assertIn(
            BOARD_URL + "jb/platform-engineer-boston/206",
            [candidate.url for candidate in result.candidates],
        )
        self.assertTrue(result.trace["exact_target_found"])

    def test_search_entry_posts_title_before_inventory_request(self):
        entry_board = self.adapter.identify_board_from_page(
            Page(BOARD_URL, search_entry_html())
        )
        fetcher = RecordingFetcher(
            [
                response(BOARD_URL, search_entry_html()),
                response(
                    BOARD_URL + "index.smpl",
                    board_html(),
                    final_url=BOARD_URL + "index.smpl",
                ),
                json.dumps(inventory([record()], pp=5)),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            entry_board,
            JobQuery(title="Platform Engineer", location="Boston, MA"),
        )

        self.assertEqual(len(result.candidates), 1)
        search_url, data, headers = fetcher.requests[1]
        self.assertEqual(search_url, BOARD_URL + "index.smpl")
        self.assertEqual(
            parse_qs(data.decode("utf-8"))["keywords"],
            ["Platform Engineer"],
        )
        self.assertEqual(
            headers["Content-Type"],
            "application/x-www-form-urlencoded",
        )

    def test_repairs_only_known_nonempty_hmg_response_missing_two_final_braces(self):
        payload = json.dumps(inventory([record()], pp=5))
        self.assertTrue(payload.endswith("}}"))

        result, _ = self.list_jobs(payload[:-2])

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.url for item in result.candidates], [DETAIL_URL])
        self.assertEqual(result.trace["structured_data_repair"], "missing_two_final_braces")

    def test_rejects_all_other_malformed_or_inconsistent_inventory(self):
        valid = json.dumps(inventory([record()]))
        malformed = (
            "not-json",
            "[]",
            valid[:-1],
            valid[:-3],
            json.dumps(inventory([], total=0))[:-2],
            valid[:-2] + ",",
            json.dumps({"ResultSet": {"list": [record()]}}),
            json.dumps(
                {"ResultSet": {"list": [record()], "list_meta": {"total": 1}}}
            ),
            json.dumps(inventory([record()], total=2, first=-1, pp=5)),
            json.dumps(inventory([record(post_id="../101")])),
            json.dumps(inventory([record(slug="../platform-engineer")])),
            json.dumps(
                inventory(
                    [
                        {
                            **record(),
                            "POST_SEO_URL": "https://jobs.evil.test/jb/platform-engineer/101",
                        }
                    ],
                    pp=5,
                )
            ),
            json.dumps(
                inventory(
                    [
                        {
                            **record(),
                            "POST_SEO_URL": BOARD_URL + "jb/other-slug/101",
                        }
                    ],
                    pp=5,
                )
            ),
        )
        for payload in malformed:
            with self.subTest(payload=payload[-140:]):
                self.assert_invalid_inventory(payload)

    def test_cross_host_final_urls_fail_closed(self):
        cross_board = response(
            BOARD_URL,
            board_html(),
            final_url="https://jobs.evil.test/",
        )
        board_result, _ = self.list_jobs(
            json.dumps(inventory([record()], pp=5)),
            board_page=cross_board,
        )
        cross_inventory = response(
            BOARD_URL.rstrip("/") + INVENTORY_PATH,
            json.dumps(inventory([record()], pp=5)),
            final_url="https://jobs.evil.test/json/index.smpl",
        )
        inventory_result, _ = self.list_jobs(cross_inventory)

        for result in (board_result, inventory_result):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_duplicate_ids_and_duplicate_detail_urls_fail_closed(self):
        duplicate_id = [
            record(),
            record("101", title="Other Role", slug="other-role"),
        ]
        duplicate_url = [record(), copy.deepcopy(record())]

        for records in (duplicate_id, duplicate_url):
            with self.subTest(records=records):
                self.assert_invalid_inventory(
                    json.dumps(inventory(records, total=2, pp=5))
                )

    def test_fetch_failures_preserve_transport_taxonomy(self):
        cases = (
            (
                FetchError("HTTP Error 403: Forbidden"),
                "HTTP_FORBIDDEN",
                False,
            ),
            (
                FetchError("The read operation timed out"),
                "NETWORK_TIMEOUT",
                True,
            ),
            (
                FetchError(
                    "company budget exhausted",
                    reason_code="COMPANY_TIME_BUDGET_EXHAUSTED",
                    retryable=True,
                ),
                "COMPANY_TIME_BUDGET_EXHAUSTED",
                True,
            ),
            (
                FetchError("unclassified provider transport failure"),
                "PROVIDER_FETCH_FAILED",
                True,
            ),
        )
        for error, expected_reason, retryable in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(error=error),
                    self.board,
                    JobQuery(title="private search phrase"),
                )
                self.assertEqual(result.reason_code, expected_reason)
                self.assertEqual(result.retryable, retryable)
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertNotIn("private", json.dumps(result.trace))

    def test_explicitly_archived_record_is_not_exposed(self):
        archived = {**record(), "POST_ARCHIVED": "1"}
        result, _ = self.list_jobs(inventory([archived], pp=5))

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["archived_records"], 1)


if __name__ == "__main__":
    unittest.main()
