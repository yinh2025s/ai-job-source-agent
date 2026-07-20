from pathlib import Path
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.hrsmart import ADAPTER, HRSmartAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "hrsmart"
TENANT = "ignitenow"
BOARD_URL = f"https://{TENANT}.hua.hrsmart.com/hr/ats/JobSearch/viewAll"


def fixture(name="ignitenow_view_all.html"):
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if self.page is None:
            raise FetchError(f"unexpected URL: {url}")
        return self.page


def inventory_page(html=None, *, final_url=BOARD_URL):
    return Page(
        url=BOARD_URL,
        final_url=final_url,
        html=fixture() if html is None else html,
        source="frozen-ignitenow-hrsmart",
    )


class HRSmartAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = HRSmartAdapter()
        self.board = JobBoard(BOARD_URL, "hrsmart", TENANT)

    def test_typed_adapter_canonicalizes_observed_public_routes(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        accepted = (
            BOARD_URL,
            BOARD_URL + "/?source=careers",
            f"https://{TENANT}.hua.hrsmart.com/hr/ats/JobSearch/index",
            f"https://{TENANT}.hua.hrsmart.com/hr/ats/JobSearch/index/searchType:quick",
            f"https://{TENANT}.hua.hrsmart.com/hr/ats/JobSearch/index/searchType:advanced",
            f"https://{TENANT}.hua.hrsmart.com/hr/ats/Posting/view/779",
            f"https://{TENANT}.hua.hrsmart.com:443/hr/ats/Posting/view/779/",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_unsafe_ambiguous_and_cross_tenant_urls(self):
        rejected = (
            "http://ignitenow.hua.hrsmart.com/hr/ats/JobSearch/viewAll",
            "https://hua.hrsmart.com/hr/ats/JobSearch/viewAll",
            "https://division.ignitenow.hua.hrsmart.com/hr/ats/JobSearch/viewAll",
            "https://bad_tenant.hua.hrsmart.com/hr/ats/JobSearch/viewAll",
            "https://user@ignitenow.hua.hrsmart.com/hr/ats/JobSearch/viewAll",
            "https://ignitenow.hua.hrsmart.com:8443/hr/ats/JobSearch/viewAll",
            "https://ignitenow.hua.hrsmart.com/hr/ats/JobSearch/viewAll#jobs",
            "https://ignitenow.hua.hrsmart.com/hr/ats/Posting/view/not-numeric",
            "https://ignitenow.hua.hrsmart.com/hr/ats/Posting/view/779/apply",
            "https://ignitenow.hua.hrsmart.com/hr/ats/JobSeeker/createAccount",
            "https://ignitenow.hua.hrsmart.com.evil.test/hr/ats/JobSearch/viewAll",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_complete_frozen_inventory_with_title_and_location(self):
        fetcher = RecordingFetcher(inventory_page())
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer"),
        )

        self.assertEqual(fetcher.requests, [(BOARD_URL, None, None)])
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 3)
        candidate = result.candidates[2]
        self.assertEqual(candidate.title, "AI ENGINEER")
        self.assertEqual(candidate.location, "Huntsville, AL, US 35806")
        self.assertEqual(
            candidate.url,
            "https://ignitenow.hua.hrsmart.com/hr/ats/Posting/view/779",
        )
        self.assertEqual(candidate.raw, {"job_id": "779", "requisition": "779"})
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["records_seen"], 3)
        self.assertEqual(result.trace["variant"], "public_view_all_html")

    def test_rejects_cross_tenant_redirect_and_cross_tenant_inventory_link(self):
        redirect = self.adapter.list_jobs(
            RecordingFetcher(
                inventory_page(
                    final_url="https://other.hua.hrsmart.com/hr/ats/JobSearch/viewAll"
                )
            ),
            self.board,
            JobQuery(),
        )
        cross_tenant_html = fixture().replace(
            "https://ignitenow.hua.hrsmart.com/hr/ats/Posting/view/779",
            "https://other.hua.hrsmart.com/hr/ats/Posting/view/779",
        )
        cross_tenant = self.adapter.list_jobs(
            RecordingFetcher(inventory_page(cross_tenant_html)),
            self.board,
            JobQuery(),
        )

        self.assertEqual(redirect.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(redirect.inventory_complete)
        self.assertEqual(cross_tenant.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(cross_tenant.inventory_complete)
        self.assertEqual(cross_tenant.candidates, [])

    def test_rejects_query_bearing_inventory_response(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(inventory_page(final_url=f"{BOARD_URL}?page=1")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_requires_complete_bounded_inventory_and_consistent_rows(self):
        valid = fixture()
        invalid_pages = (
            valid.replace("Displaying 1 - 3 of 3", "Displaying 1 - 3 of 4"),
            valid.replace("<td>789</td>", "<td>999</td>"),
            valid.replace("/hr/ats/Posting/view/789", "/hr/ats/Posting/view/790"),
            valid.replace("Deltek Talent Management", "Unrelated Careers"),
            valid.replace("jobSearchResultsGrid_table", "other_table"),
            valid.replace("Job Title", "Position"),
            valid.replace("Displaying 1 - 3 of 3", "Displaying 1 - 3 of 2001"),
            "x" * 2_000_001,
        )
        for html in invalid_pages:
            with self.subTest(size=len(html)):
                result = self.adapter.list_jobs(
                    RecordingFetcher(inventory_page(html)),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_rejects_tampered_board_and_preserves_typed_fetch_failure(self):
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(BOARD_URL, "hrsmart", "other"),
            JobQuery(),
        )
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="private search terms"),
        )

        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(tampered.inventory_complete)
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertNotIn("private", str(timeout.trace))


if __name__ == "__main__":
    unittest.main()
