import json
import unittest

from job_source_agent.job_board import JobBoard
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.peoplesoft import ADAPTER, PeopleSoftAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


HOST = "www.cnd.nd.gov"
BASE = f"https://{HOST}/psc/recruit/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
SEARCH = f"{BASE}?Page=HRS_APP_SCHJOB_FL&Action=U&SiteId=11000&FOCUS=Applicant"
DETAIL = (
    f"{BASE}?Page=HRS_APP_JBPST_FL&Action=U&SiteId=11000&FOCUS=Applicant"
    "&JobOpeningId=3033305&PostingSeq=1"
)


def detail_html(*, site="11000", opening="3033305", title="Cybersecurity Analyst"):
    return f"""
      <html>
        <input type="hidden" name="SiteId" value="{site}">
        <input type="hidden" name="JobOpeningId" value="{opening}">
        <h1 id="HRS_APP_JBPST_FL_POSTING_TITLE">{title}</h1>
        <span id="HRS_APP_JBPST_FL_LOCATION">Statewide</span>
      </html>
    """


def search_html(*links, total=None):
    count = "" if total is None else f"<script>TotalJobs = {total};</script>"
    return count + "".join(f'<a class="job-link" href="{url}">{title}</a>' for url, title in links)


class StubFetcher:
    def __init__(self, html="", *, final_url=None, error=None):
        self.html = html
        self.final_url = final_url
        self.error = error
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if self.error is not None:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="peoplesoft-fixture",
        )


class PeopleSoftAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PeopleSoftAdapter()

    def test_exported_adapter_is_auto_discovered_and_satisfies_contract(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertIs(
            {adapter.name: adapter for adapter in discover_native_adapters()}["peoplesoft"],
            ADAPTER,
        )

    def test_accepts_only_exact_public_search_and_detail_routes(self):
        for url in (SEARCH, DETAIL, SEARCH.replace("https://", "https://user@")):
            with self.subTest(url=url):
                expected = "user@" not in url
                self.assertEqual(self.adapter.recognizes(url), expected)

        rejected = (
            SEARCH.replace("https://", "http://"),
            SEARCH.replace(HOST, HOST + ":8443"),
            SEARCH.replace(_public_component(), "HRS_HRAM_FL.HRS_EMPLOYEE_FL.GBL"),
            SEARCH.replace("HRS_APP_SCHJOB_FL", "HRS_APP_LOGIN_FL"),
            SEARCH.replace("FOCUS=Applicant", "FOCUS=Employee"),
            SEARCH + "&UserId=alice",
            SEARCH + "&SiteId=12000",
            SEARCH.replace("SiteId=11000", "SiteId=bad"),
            DETAIL.replace("JobOpeningId=3033305", "JobOpeningId=bad"),
            DETAIL + "&JobOpeningId=9",
            SEARCH + "&bad=%ZZ",
            SEARCH + "#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_detail_identity_is_not_rewritten_into_an_invented_search_board(self):
        detail = self.adapter.identify_board(DETAIL)
        search = self.adapter.identify_board(SEARCH)

        self.assertEqual(detail.url, DETAIL)
        self.assertEqual(search.url, SEARCH)
        self.assertEqual(json.loads(detail.identifier)["kind"], "detail")
        self.assertEqual(json.loads(detail.identifier)["job_opening_id"], "3033305")
        self.assertEqual(json.loads(search.identifier)["kind"], "search")
        self.assertIsNone(json.loads(search.identifier)["job_opening_id"])
        # A central job_board replay policy is intentionally outside this task's ownership.
        self.assertFalse(detail.replay_safe)

    def test_page_evidence_promotes_only_one_explicit_same_host_search_route(self):
        page = Page(
            url="https://www.ndit.nd.gov/about-us/careers",
            html=f'<a href="{SEARCH}">View all careers</a><a href="{DETAIL}">Role</a>',
        )
        board = self.adapter.identify_board_from_page(page)
        self.assertEqual(board.url, SEARCH)

        ambiguous = Page(
            url=page.url,
            html=f'<a href="{SEARCH}">One</a><a href="{SEARCH.replace("SiteId=11000", "SiteId=12000")}">Two</a>',
        )
        detail_only = Page(url=page.url, html=f'<a href="{DETAIL}">Role</a>')
        self.assertIsNone(self.adapter.identify_board_from_page(ambiguous))
        self.assertIsNone(self.adapter.identify_board_from_page(detail_only))

    def test_fetches_ndit_style_exact_detail_with_observed_identity(self):
        board = self.adapter.identify_board(DETAIL)
        fetcher = StubFetcher(detail_html())
        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Cyber Security Analyst", location="Fargo, ND"),
        )

        self.assertEqual(fetcher.calls, [(DETAIL, None, None)])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.candidates[0].title, "Cybersecurity Analyst")
        self.assertEqual(result.candidates[0].location, "Statewide")
        self.assertEqual(result.candidates[0].url, DETAIL)
        self.assertEqual(result.candidates[0].raw["site_id"], "11000")
        self.assertEqual(result.candidates[0].raw["job_opening_id"], "3033305")

    def test_exact_detail_rejects_cross_site_opening_and_redirect_identity(self):
        board = self.adapter.identify_board(DETAIL)
        cases = (
            StubFetcher(detail_html(site="12000")),
            StubFetcher(detail_html(opening="9999999")),
            StubFetcher(detail_html(), final_url=DETAIL.replace(HOST, "jobs.other.gov")),
            StubFetcher(detail_html(), final_url=DETAIL.replace("SiteId=11000", "SiteId=12000")),
            StubFetcher(detail_html(), final_url=DETAIL.replace("3033305", "3033306")),
        )
        for fetcher in cases:
            with self.subTest(final_url=fetcher.final_url, html=fetcher.html):
                result = self.adapter.list_jobs(fetcher, board, JobQuery())
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertIn(
                    result.reason_code,
                    {"INVALID_STRUCTURED_DATA", "PROVIDER_VARIANT_UNSUPPORTED"},
                )

    def test_employee_login_flow_is_not_accepted_as_public_inventory(self):
        board = self.adapter.identify_board(DETAIL)
        html = '<h1>Employee Login</h1><input type="password" name="pwd">'
        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())
        self.assertEqual(result.reason_code, "LOGIN_REQUIRED")
        self.assertFalse(result.inventory_complete)

    def test_public_search_preserves_exact_same_tenant_details(self):
        second = DETAIL.replace("3033305", "3033306")
        board = self.adapter.identify_board(SEARCH)
        result = self.adapter.list_jobs(
            StubFetcher(
                search_html(
                    (DETAIL, "Cybersecurity Analyst"),
                    (second, "Network Analyst"),
                    total=2,
                )
            ),
            board,
            JobQuery(title="Cybersecurity"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual([candidate.url for candidate in result.candidates], [DETAIL])
        self.assertEqual(result.trace["records_seen"], 2)

    def test_public_search_fails_closed_on_cross_tenant_or_malformed_detail(self):
        board = self.adapter.identify_board(SEARCH)
        cases = (
            DETAIL.replace(HOST, "jobs.other.gov"),
            DETAIL.replace("SiteId=11000", "SiteId=12000"),
            DETAIL.replace("/psc/recruit/", "/psc/other/"),
            DETAIL + "&JobOpeningId=9",
        )
        for bad_detail in cases:
            with self.subTest(bad_detail=bad_detail):
                result = self.adapter.list_jobs(
                    StubFetcher(search_html((bad_detail, "Wrong role"), total=1)),
                    board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_unproven_empty_search_is_incomplete_and_fetch_errors_are_typed(self):
        board = self.adapter.identify_board(SEARCH)
        empty = self.adapter.list_jobs(StubFetcher("<main>No rows</main>"), board, JobQuery())
        failed = self.adapter.list_jobs(
            StubFetcher(error=FetchError("read operation timed out")), board, JobQuery()
        )
        tampered = self.adapter.list_jobs(
            StubFetcher(""),
            JobBoard(SEARCH, "peoplesoft", "tampered", replay_safe=True),
            JobQuery(),
        )

        self.assertEqual(empty.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(empty.inventory_complete)
        self.assertEqual(failed.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(failed.retryable)
        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")


def _public_component():
    return "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"


if __name__ == "__main__":
    unittest.main()
