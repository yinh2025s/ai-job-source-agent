import json
from pathlib import Path
import unittest
from urllib.parse import parse_qsl, urlparse

from job_source_agent.job_board import JobBoard
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.cws import ADAPTER, CWSAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/job-search-results/"
API_URL = "https://jobsapi-google.m-cloud.io/api/"
ORG_ID = "companies/12c4c7c9-29cb-4cff-a16c-4ae0d974c00a"


def config_html(
    *,
    api=API_URL,
    org_id=ORG_ID,
    detail_path="/job-description",
    limit=2,
):
    return f"""
      <script>console.log('unrelated');</script>
      <script>
        CWS.jobs.set_api({json.dumps(api)});
        CWS.jobs.set_options({{
          org_id: {json.dumps(org_id)},
          jobdetail_path: {json.dumps(detail_path)},
          limit: {limit}
        }});
      </script>
    """


def job(job_id, title, *, org_id=None, city="New Hyde Park", state="NY", **extra):
    value = {
        "id": job_id,
        "title": title,
        "primary_city": city,
        "primary_state": state,
        **extra,
    }
    if org_id is not None:
        value["organization"] = org_id
    return value


def inventory(rows, total, *, org_id=ORG_ID):
    return {"totalHits": total, "queryResult": rows, "organization": org_id}


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        return Page(url=url, final_url=url, html=response, source="synthetic-cws-api")


def response(payload, *, jsonp=False, url=None, final_url=None):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    if jsonp:
        body = f"CWS.jobs.jobCallback({body});"
    request_url = url or request_for(offset=1)
    return Page(url=request_url, final_url=final_url, html=body, source="synthetic-cws-api")


def request_for(*, offset, title="Platform Engineer", org_id=ORG_ID, limit=2):
    from urllib.parse import urlencode

    return API_URL + "job?" + urlencode(
        (
            ("SearchText", title),
            ("Limit", str(limit)),
            ("Organization", org_id),
            ("offset", str(offset)),
            ("callback", "CWS.jobs.jobCallback"),
        )
    )


class CWSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CWSAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL + "?source=test#jobs", html=config_html())
        )
        self.assertIsNotNone(self.board)

    def test_page_aware_adapter_is_auto_discovered_and_locator_is_canonical(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["cws"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertFalse(ADAPTER.recognizes(API_URL))
        self.assertIsNone(ADAPTER.identify_board(API_URL))
        self.assertEqual(self.board.url, BOARD_URL)
        self.assertTrue(self.board.replay_safe)
        self.assertEqual(
            json.loads(self.board.identifier),
            {
                "api_url": API_URL,
                "board_url": BOARD_URL,
                "detail_path": "/job-description",
                "limit": 2,
                "org_id": ORG_ID,
            },
        )

    def test_detects_frozen_northwell_snapshot_without_company_special_case(self):
        snapshot = Path(
            "/private/tmp/observed40-career91-snapshots/sites/jobs.northwell.edu/"
            "job-search-results/index.html"
        )
        if not snapshot.exists():
            self.skipTest("observed40 read-only snapshot unavailable")

        board = self.adapter.identify_board_from_page(
            Page(
                url="https://jobs.northwell.edu/job-search-results/",
                html=snapshot.read_text(encoding="utf-8"),
            )
        )

        self.assertIsNotNone(board)
        identity = json.loads(board.identifier)
        self.assertEqual(identity["api_url"], API_URL)
        self.assertEqual(identity["org_id"], ORG_ID)
        self.assertEqual(identity["detail_path"], "/job-3")
        self.assertEqual(identity["limit"], 10)

    def test_requires_static_https_m_cloud_api_org_and_safe_customer_page(self):
        cases = (
            (BOARD_URL, "CWS jobs on m-cloud"),
            (BOARD_URL, config_html(api="http://jobsapi-google.m-cloud.io/api/")),
            (BOARD_URL, config_html(api="https://m-cloud.io.evil.test/api/")),
            (BOARD_URL, config_html(api="https://jobsapi-google.m-cloud.io/v2/")),
            (BOARD_URL, config_html(org_id="../other")),
            (BOARD_URL, config_html(detail_path="https://evil.test/job")),
            ("http://careers.example.com/jobs", config_html()),
            ("https://user@careers.example.com/jobs", config_html()),
            (BOARD_URL, f"<!-- {config_html()} -->"),
            (
                BOARD_URL,
                '<script>// CWS.jobs.set_api("https://jobsapi-google.m-cloud.io/api/");\n'
                '// CWS.jobs.set_options({org_id: "companies/commented", '
                'jobdetail_path: "/job"});</script>',
            ),
        )
        for url, html in cases:
            with self.subTest(url=url, html=html[:80]):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=url, html=html))
                )

    def test_rejects_cross_org_or_cross_api_declarations(self):
        for extra in (
            'CWS.jobs.set_options({org_id: "companies/other"});',
            'CWS.jobs.set_api("https://jobsapi-internal.m-cloud.io/api/");',
        ):
            with self.subTest(extra=extra):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=BOARD_URL, html=config_html() + f"<script>{extra}</script>")
                    )
                )

    def test_builds_strict_title_query_and_parses_json_and_jsonp_pages(self):
        fetcher = RecordingFetcher(
            [
                response(
                    inventory(
                        [
                            job("100", "Platform Engineer", org_id=ORG_ID),
                            job(
                                "101",
                                "Senior Platform Engineer",
                                state=None,
                                primary_country="USA",
                            ),
                        ],
                        3,
                    ),
                    jsonp=True,
                    url=request_for(offset=1),
                ),
                response(
                    inventory([job("102", "Staff Platform Engineer")], 3),
                    url=request_for(offset=3),
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="  Platform   Engineer  ")
        )

        first_url, data, headers = fetcher.requests[0]
        self.assertEqual(
            parse_qsl(urlparse(first_url).query),
            [
                ("SearchText", "Platform Engineer"),
                ("Limit", "2"),
                ("Organization", ORG_ID),
                ("offset", "1"),
                ("callback", "CWS.jobs.jobCallback"),
            ],
        )
        self.assertIsNone(data)
        self.assertEqual(headers, {"Accept": "application/javascript, application/json"})
        self.assertEqual(
            [urlparse(item[0]).query for item in fetcher.requests],
            [
                urlparse(request_for(offset=1)).query,
                urlparse(request_for(offset=3)).query,
            ],
        )
        self.assertEqual(
            [item.title for item in result.candidates],
            [
                "Platform Engineer",
                "Senior Platform Engineer",
                "Staff Platform Engineer",
            ],
        )
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/job-description/100/"
            "platform-engineer-new-hyde-park-ny",
        )
        self.assertEqual(result.candidates[0].location, "New Hyde Park, NY")
        self.assertTrue(result.inventory_complete)
        self.assertIsNone(result.reason_code)

    def test_valid_filtered_empty_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    response(
                        inventory([], 0),
                        url=request_for(offset=1, title="No Such Role"),
                    )
                ]
            ),
            self.board,
            JobQuery(title="No Such Role"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")

    def test_cross_org_cross_host_and_tampered_locator_fail_closed(self):
        wrong_org = self.adapter.list_jobs(
            RecordingFetcher([response(inventory([], 0, org_id="companies/other"))]),
            self.board,
            JobQuery(title="Platform Engineer"),
        )
        cross_host = self.adapter.list_jobs(
            RecordingFetcher(
                [response(inventory([], 0), final_url="https://evil.test/api/job")]
            ),
            self.board,
            JobQuery(title="Platform Engineer"),
        )
        value = json.loads(self.board.identifier)
        value["org_id"] = "companies/other"
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(
                url=self.board.url,
                provider="cws",
                identifier=json.dumps(value),
                replay_safe=True,
            ),
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(wrong_org.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(wrong_org.trace["stop_reason"], "cross_tenant_response")
        self.assertEqual(cross_host.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(wrong_org.inventory_complete)

    def test_malformed_pagination_duplicate_and_record_org_fail_closed(self):
        cases = (
            (
                [
                    response(
                        "CWS.jobs.jobCallback({broken);",
                        url=request_for(offset=1),
                    )
                ],
                "invalid_response_schema",
            ),
            ([response(inventory([job("1", "One")], 2))], "pagination_count_mismatch"),
            (
                [
                    response(inventory([job("1", "One"), job("2", "Two")], 3)),
                    response(
                        inventory([job("2", "Duplicate")], 3),
                        url=request_for(offset=3),
                    ),
                ],
                "duplicate_job_id",
            ),
            (
                [response(inventory([job("1", "Wrong", org_id="companies/other")], 1))],
                "cross_tenant_response",
            ),
        )
        for pages, stop_reason in cases:
            with self.subTest(stop_reason=stop_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(pages), self.board, JobQuery(title="Platform Engineer")
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.trace["stop_reason"], stop_reason)
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.trace["exposed_candidate_count"], 0)

    def test_timeout_is_retryable_and_query_is_not_stored_in_trace(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="Private Search Terms"),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertNotIn("Private", json.dumps(result.trace))

    def test_requires_bounded_title_without_fetch(self):
        for title in (None, "", "x" * 201, "Data\x00Scientist"):
            with self.subTest(title=title):
                fetcher = RecordingFetcher()
                result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title=title))
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertEqual(fetcher.requests, [])

    def test_page_cap_is_typed_incomplete(self):
        board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL, html=config_html(limit=1))
        )
        pages = [
            response(
                inventory([job(str(index), f"Role {index}")], 11),
                url=request_for(offset=index, limit=1),
            )
            for index in range(1, 11)
        ]

        result = self.adapter.list_jobs(
            RecordingFetcher(pages), board, JobQuery(title="Platform Engineer")
        )

        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.trace["page_count"], 10)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["exposed_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
