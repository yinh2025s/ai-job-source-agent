import json
from pathlib import Path
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.saashr import ADAPTER, SaaSHRAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "saashr"
HOST = "secure4.saashr.com"
ACCOUNT = "6052029"
BOARD_URL = f"https://{HOST}/ta/{ACCOUNT}.careers?CareersSearch="
INVENTORY_URL = (
    f"https://{HOST}/ta/rest/ui/recruitment/companies/%7C{ACCOUNT}/"
    "job-requisitions?offset=0&size=100&sort=&ein_id=&lang=en-US"
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def inventory(records=None, *, offset=0, size=100, total=None):
    if records is None:
        return fixture("stuller_inventory.json")
    return json.dumps(
        {
            "job_requisitions": records,
            "_paging": {
                "offset": offset,
                "size": size,
                "total": len(records) if total is None else total,
            },
        }
    )


def job(job_id, title="Opening", *, remote=False):
    return {
        "id": job_id,
        "job_title": title,
        "location": {"city": "Lafayette", "state": "LA", "country": "USA"},
        "is_remote_job": remote,
    }


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
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Page):
            return response
        return Page(url=url, html=response, source="fresh-100-stuller-fixture")


def board_page(html=None, *, final_url=BOARD_URL):
    return Page(
        url=BOARD_URL,
        final_url=final_url,
        html=fixture("stuller_board.html") if html is None else html,
        source="fresh-100-stuller-board",
    )


def inventory_page(raw=None, *, url=INVENTORY_URL, final_url=None):
    return Page(
        url=url,
        final_url=final_url or url,
        html=inventory() if raw is None else raw,
        source="fresh-100-stuller-inventory",
    )


class SaaSHRAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SaaSHRAdapter()
        self.board = JobBoard(BOARD_URL, "saashr", f"{HOST}|{ACCOUNT}")

    def test_is_typed_and_canonicalizes_public_listing_and_detail_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        accepted = (
            BOARD_URL,
            f"https://{HOST}:443/ta/{ACCOUNT}.careers?careerssearch=",
            f"https://{HOST}/ta/{ACCOUNT}.careers?ShowJob=990125377",
            f"https://{HOST}/ta/{ACCOUNT}.careers?showjob=990125377&lang=en-US",
            "https://secure12.saashr.com/ta/123456.careers?CareersSearch=",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                board = self.adapter.identify_board(url)
                self.assertIsNotNone(board)
                self.assertIn("?CareersSearch=", board.url)
        self.assertEqual(self.adapter.identify_board(accepted[1]), self.board)
        self.assertEqual(self.adapter.identify_board(accepted[2]), self.board)

    def test_rejects_unsafe_ambiguous_and_non_public_routes(self):
        rejected = (
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace(HOST, f"{HOST}.evil.test"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace(HOST, "secure0.saashr.com"),
            BOARD_URL.replace(ACCOUNT, "0"),
            BOARD_URL.replace(".careers", ".login"),
            BOARD_URL.replace("CareersSearch=", "CareersSearch=private"),
            BOARD_URL.replace("CareersSearch=", "ShowJob=not-an-id"),
            BOARD_URL + "&token=secret",
            BOARD_URL + "#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_fresh_100_stuller_titles_locations_and_bounded_details(self):
        fetcher = RecordingFetcher([board_page(), inventory_page()])

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" Information Security Analyst "),
        )

        self.assertEqual(fetcher.requests[0], (BOARD_URL, None, None))
        self.assertEqual(fetcher.requests[1][0], INVENTORY_URL)
        self.assertEqual(fetcher.requests[1][2]["Referer"], BOARD_URL)
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 2)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Information Security Analyst")
        self.assertEqual(candidate.location, "Lafayette, LA, USA")
        self.assertEqual(
            candidate.url,
            f"https://{HOST}/ta/{ACCOUNT}.careers?ShowJob=990125377",
        )
        self.assertEqual(
            candidate.raw,
            {"job_id": "990125377", "account_id": ACCOUNT},
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(
            result.trace["identity"],
            {"host": HOST, "account_id": ACCOUNT},
        )

    def test_paginates_until_declared_total_and_preserves_full_completeness(self):
        first = [job(index, f"Opening {index}") for index in range(1, 101)]
        second = [job(101, "Remote Analyst", remote=True)]
        second_url = INVENTORY_URL.replace("offset=0", "offset=100")
        fetcher = RecordingFetcher(
            [
                board_page(),
                inventory_page(inventory(first, total=101)),
                inventory_page(
                    inventory(second, offset=100, total=101),
                    url=second_url,
                ),
            ]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 101)
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(result.candidates[-1].location, "Remote, Lafayette, LA, USA")
        self.assertEqual(fetcher.requests[-1][0], second_url)

    def test_verified_empty_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher([board_page(), inventory_page(inventory([], total=0))]),
            self.board,
            JobQuery(title="No such role"),
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_tampered_board_redirects_and_inventory_redirects_fail_closed(self):
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(BOARD_URL, "saashr", f"{HOST}|9999999"),
            JobQuery(),
        )
        board_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [board_page(final_url=BOARD_URL.replace(ACCOUNT, "9999999"))]
            ),
            self.board,
            JobQuery(),
        )
        detail_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(
                        final_url=(
                            f"https://{HOST}/ta/{ACCOUNT}.careers?ShowJob=990125377"
                        )
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        inventory_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        final_url=INVENTORY_URL.replace(ACCOUNT, "9999999")
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )
        for result in (tampered, board_redirect, detail_redirect, inventory_redirect):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_invalid_duplicate_and_unbounded_inventory_is_typed_incomplete(self):
        duplicate = [job(1), job(1)]
        malformed_cases = (
            "not-json",
            inventory([job(1)], offset=1, total=1),
            inventory(duplicate, total=2),
            inventory([{"id": 1, "job_title": "", "location": {}}]),
            inventory([job(1)], size=101, total=1),
            inventory([{**job(1), "is_remote_job": "false"}]),
        )
        for raw in malformed_cases:
            with self.subTest(raw=raw[-100:]):
                result = self.adapter.list_jobs(
                    RecordingFetcher([board_page(), inventory_page(raw)]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

        oversized = self.adapter.list_jobs(
            RecordingFetcher(
                [board_page(), inventory_page(inventory([job(1)], total=1001))]
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(oversized.reason_code, "OPENING_DISCOVERY_INCOMPLETE")
        self.assertFalse(oversized.inventory_complete)
        self.assertEqual(oversized.trace["pagination_limit"], 1000)

    def test_invalid_shell_and_typed_fetch_failures_do_not_leak_query(self):
        invalid_shell = self.adapter.list_jobs(
            RecordingFetcher([board_page("<html><div id='_app'></div></html>")]),
            self.board,
            JobQuery(),
        )
        forbidden = self.adapter.list_jobs(
            RecordingFetcher([board_page("<h2>403 Forbidden</h2>")]),
            self.board,
            JobQuery(),
        )
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="private search terms"),
        )
        self.assertEqual(invalid_shell.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(forbidden.reason_code, "HTTP_FORBIDDEN")
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertNotIn("private", json.dumps(timeout.trace))


if __name__ == "__main__":
    unittest.main()
