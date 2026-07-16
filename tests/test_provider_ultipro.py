import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.providers.ultipro import ADAPTER, UltiProAdapter
from job_source_agent.web import FetchError, Page


TENANT = "COM1101CMHS"
BOARD_ID = "8de41890-2fe6-4347-b1ad-f3043de88a1a"
OPPORTUNITY_ID = "f1c29f47-1981-4593-9d7d-99d8b20fb1ef"
BOARD_URL = f"https://recruiting2.ultipro.com/{TENANT}/JobBoard/{BOARD_ID}/"
LOAD_URL = BOARD_URL + "JobBoardView/LoadSearchResults"
DETAIL_URL = BOARD_URL + f"OpportunityDetail?opportunityId={OPPORTUNITY_ID}"


def board_html(*, board_id=BOARD_ID, load_board_id=None, page_size=50):
    load_board_id = load_board_id or board_id
    base = f"/{TENANT}/JobBoard/{load_board_id}"
    return f"""
    <script>
    var opportunityModel = new US.Opportunity.OpportunitiesViewModel({{
      pageSize: {page_size},
      loadUrl: "{base}/JobBoardView/LoadSearchResults",
      opportunityLinkUrl: "{base}/OpportunityDetail?opportunityId=00000000-0000-0000-0000-000000000000",
      jobBoard: {json.dumps({'Id': board_id, 'Name': 'Careers'})},
      registerRedirectUrl: "{base}/Account/Register",
      jobSearchAgentUrl: "{base}/JobSearchAgent"
    }});
    </script>
    """


def opportunity(opportunity_id=OPPORTUNITY_ID, title="Registered Nurse - Clinical"):
    return {
        "Id": opportunity_id,
        "Title": title,
        "RequisitionNumber": "REGIS001936",
        "Locations": [
            {
                "LocalizedName": None,
                "LocalizedDescription": "Everett-Central Clinic",
                "Address": {"City": "Everett", "State": {"Name": "Washington"}},
            }
        ],
    }


def inventory(records, total_count=None):
    return json.dumps(
        {
            "opportunities": records,
            "locations": [],
            "totalCount": len(records) if total_count is None else total_count,
        }
    )


class RecordingFetcher:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append({"url": url, "data": data, "headers": headers or {}})
        if self.error:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        return Page(url=url, final_url=url, html=response, source="ultipro-contract")


class UltiProAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = UltiProAdapter()
        self.board = JobBoard(
            BOARD_URL,
            "ultipro",
            f"{TENANT}/{BOARD_ID}",
        )

    def test_native_adapter_is_discovered_and_canonicalizes_public_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["ultipro"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            BOARD_URL,
            BOARD_URL.rstrip("/"),
            DETAIL_URL,
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_unsafe_hosts_routes_tenants_boards_and_detail_ids(self):
        rejected = (
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("recruiting2.ultipro.com", "recruiting2.ultipro.com.evil.test"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace(".com/", ".com:8443/"),
            BOARD_URL.replace(TENANT, "bad tenant"),
            BOARD_URL.replace(BOARD_ID, "not-a-uuid"),
            BOARD_URL + "Account/Register",
            BOARD_URL + "AnonymousSessionCheck",
            BOARD_URL + "JobSearchAgent",
            BOARD_URL + "OpportunityDetail?opportunityId=not-a-uuid",
            BOARD_URL + f"OpportunityDetail?opportunityId={OPPORTUNITY_ID}&Account=true",
        )

        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_target_opening_from_public_inventory_contract(self):
        fetcher = RecordingFetcher(
            [
                Page(url=BOARD_URL, html=board_html(), source="captured-board"),
                inventory([opportunity()]),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Registered Nurse - Clinical")
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Registered Nurse - Clinical")
        self.assertEqual(candidate.url, DETAIL_URL)
        self.assertEqual(candidate.provider, "ultipro")
        self.assertEqual(candidate.location, "Everett-Central Clinic")
        self.assertEqual(
            candidate.raw,
            {
                "opportunity_id": OPPORTUNITY_ID,
                "requisition_number": "REGIS001936",
            },
        )

        self.assertEqual([request["url"] for request in fetcher.requests], [BOARD_URL, LOAD_URL])
        request = json.loads(fetcher.requests[1]["data"])
        search = request["opportunitySearch"]
        self.assertEqual(search["Top"], 50)
        self.assertEqual(search["Skip"], 0)
        self.assertEqual(search["QueryString"], "Registered Nurse - Clinical")
        self.assertEqual(fetcher.requests[1]["headers"]["Referer"], BOARD_URL)

    def test_paginates_until_total_count_is_complete(self):
        first = [
            opportunity(
                f"00000000-0000-4000-8000-{index:012d}",
                f"Registered Nurse {index}",
            )
            for index in range(50)
        ]
        second = [opportunity()]
        fetcher = RecordingFetcher(
            [board_html(), inventory(first, 51), inventory(second, 51)]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        skips = [
            json.loads(request["data"])["opportunitySearch"]["Skip"]
            for request in fetcher.requests[1:]
        ]
        self.assertEqual(skips, [0, 50])
        self.assertEqual(len(result.candidates), 51)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(result.trace["total_count"], 51)

    def test_marks_bounded_pagination_incomplete(self):
        pages = []
        for page_index in range(20):
            records = [
                opportunity(
                    f"{page_index:08x}-0000-4000-8000-{index:012d}",
                    f"Nurse {page_index}-{index}",
                )
                for index in range(50)
            ]
            pages.append(inventory(records, 1001))
        result = self.adapter.list_jobs(
            RecordingFetcher([board_html(), *pages]), self.board, JobQuery()
        )

        self.assertEqual(len(result.candidates), 1000)
        self.assertFalse(result.inventory_complete)
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["pages_fetched"], 20)

    def test_rejects_mismatched_config_and_non_public_redirects(self):
        other_board = "11111111-1111-4111-8111-111111111111"
        mismatch = self.adapter.list_jobs(
            RecordingFetcher([board_html(load_board_id=other_board)]),
            self.board,
            JobQuery(),
        )
        board_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    Page(
                        url=BOARD_URL,
                        final_url=BOARD_URL + "Account/Login",
                        html=board_html(),
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        inventory_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_html(),
                    Page(
                        url=LOAD_URL,
                        final_url=BOARD_URL + "AnonymousSessionCheck",
                        html=inventory([]),
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(mismatch.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(mismatch.inventory_complete)
        self.assertEqual(board_redirect.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(inventory_redirect.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_rejects_invalid_uuid_truncated_pages_and_total_count_changes(self):
        invalid_uuid = self.adapter.list_jobs(
            RecordingFetcher([board_html(), inventory([opportunity("not-a-uuid")])]),
            self.board,
            JobQuery(),
        )
        truncated = self.adapter.list_jobs(
            RecordingFetcher([board_html(), inventory([opportunity()], 2)]),
            self.board,
            JobQuery(),
        )
        first = [
            opportunity(f"00000000-0000-4000-8000-{index:012d}", f"Nurse {index}")
            for index in range(50)
        ]
        changed_total = self.adapter.list_jobs(
            RecordingFetcher(
                [board_html(), inventory(first, 51), inventory([opportunity()], 52)]
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(invalid_uuid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(truncated.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(changed_total.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(invalid_uuid.inventory_complete)
        self.assertFalse(truncated.inventory_complete)
        self.assertFalse(changed_total.inventory_complete)

    def test_reports_a_complete_empty_inventory(self):
        result = self.adapter.list_jobs(
            RecordingFetcher([board_html(), inventory([])]),
            self.board,
            JobQuery(title="No Such Opening"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.inventory_complete)

    def test_reports_fetch_failures_and_rejects_invalid_board_locator(self):
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("blocked")), self.board, JobQuery()
        )
        invalid = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(BOARD_URL, "ultipro", f"OTHER/{BOARD_ID}"),
            JobQuery(),
        )

        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertFalse(failed.inventory_complete)
        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
