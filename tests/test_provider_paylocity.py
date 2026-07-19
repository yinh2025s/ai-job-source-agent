import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.paylocity import ADAPTER, PaylocityAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


TENANT = "df0b4c2b-4424-4d43-97ac-9cf7f31153d5"
OTHER_TENANT = "11111111-1111-4111-8111-111111111111"
SLUG = "Actabl"
BOARD_URL = f"https://recruiting.paylocity.com/recruiting/jobs/All/{TENANT}/{SLUG}"
SLUGLESS_URL = f"https://recruiting.paylocity.com/recruiting/jobs/All/{TENANT}"


def job(job_id=4284086, title="Financial Analyst", location="Actabl Denver"):
    return {
        "JobId": job_id,
        "JobTitle": title,
        "LocationName": location,
        "IsInternal": False,
        "IsRemote": True,
        "JobLocation": {
            "ModuleId": 26098,
            "City": "Denver",
            "State": "CO",
            "Country": "USA",
        },
    }


def board_html(
    jobs=None,
    *,
    tenant=TENANT,
    module_id="26098",
    lead_join_url=None,
):
    return (
        "<html><script>window.pageData = "
        + json.dumps(
            {
                "ModuleId": module_id,
                "ModuleTitle": "Example employer",
                "LeadJoinUrl": lead_join_url
                or f"/Recruiting/PublicLeads/New/{tenant}",
                "Jobs": jobs if jobs is not None else [job()],
            }
        )
        + ";</script></html>"
    )


class RecordingFetcher:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        if self.response is None:
            raise FetchError("unexpected URL")
        return self.response


class PaylocityAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PaylocityAdapter()
        self.board = JobBoard(BOARD_URL, "paylocity", f"{TENANT}|actabl")

    def test_native_adapter_is_discovered_and_canonicalizes_public_board(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        self.assertIs(native["paylocity"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        variant = BOARD_URL.replace("/recruiting/jobs/All/", "/Recruiting/Jobs/All/")
        self.assertTrue(self.adapter.recognizes(variant))
        self.assertEqual(self.adapter.identify_board(variant), self.board)

    def test_recognizes_and_reads_official_slugless_tenant_board(self):
        board = JobBoard(SLUGLESS_URL, "paylocity", TENANT)
        self.assertTrue(self.adapter.recognizes(SLUGLESS_URL))
        self.assertEqual(self.adapter.identify_board(SLUGLESS_URL), board)

        result = self.adapter.list_jobs(
            RecordingFetcher(Page(SLUGLESS_URL, board_html())),
            board,
            JobQuery(),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.trace["records_seen"], 1)

    def test_slugless_board_allows_same_tenant_canonical_slug_redirect(self):
        board = JobBoard(SLUGLESS_URL, "paylocity", TENANT)
        result = self.adapter.list_jobs(
            RecordingFetcher(
                Page(
                    SLUGLESS_URL,
                    board_html(
                        lead_join_url=(
                            "https://recruiting.paylocity.com/Recruiting/"
                            f"PublicLeads/New/{TENANT}"
                        )
                    ),
                    final_url=BOARD_URL,
                )
            ),
            board,
            JobQuery(),
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)

    def test_rejects_malformed_cross_tenant_and_non_board_urls(self):
        rejected = (
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("recruiting.paylocity.com", "recruiting.paylocity.com.evil.test"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace(TENANT, "not-a-uuid"),
            SLUGLESS_URL.replace(TENANT, "not-a-uuid"),
            SLUGLESS_URL.replace(f"/{TENANT}", f"//{TENANT}"),
            BOARD_URL.replace(SLUG, "bad slug"),
            BOARD_URL + "?token=secret",
            f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/4284086",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_slugless_board_rejects_unsafe_or_cross_tenant_evidence(self):
        board = JobBoard(SLUGLESS_URL, "paylocity", TENANT)
        unsafe_urls = (
            f"https://evil.test/Recruiting/PublicLeads/New/{TENANT}",
            f"//evil.test/Recruiting/PublicLeads/New/{TENANT}",
            f"/Recruiting/PublicLeads/New/{TENANT}?token=secret",
            f"/Recruiting/PublicLeads/New/{TENANT}%2fextra",
            f"/Recruiting/PublicLeads/New/{OTHER_TENANT}",
        )
        for lead_join_url in unsafe_urls:
            with self.subTest(lead_join_url=lead_join_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        Page(
                            SLUGLESS_URL,
                            board_html(lead_join_url=lead_join_url),
                        )
                    ),
                    board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

        cross_tenant_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                Page(
                    SLUGLESS_URL,
                    board_html(),
                    final_url=SLUGLESS_URL.replace(TENANT, OTHER_TENANT),
                )
            ),
            board,
            JobQuery(),
        )
        self.assertEqual(
            cross_tenant_redirect.reason_code,
            "PROVIDER_VARIANT_UNSUPPORTED",
        )
        self.assertFalse(cross_tenant_redirect.inventory_complete)

    def test_rejects_malformed_truncated_duplicate_and_overlarge_page_data(self):
        malformed = board_html().replace(";</script>", " trailing;</script>")
        truncated = board_html()[:-20]
        duplicate = board_html() + board_html()
        overlarge = board_html().replace(
            '"ModuleTitle": "Example employer"',
            '"ModuleTitle": "' + ("x" * 1_000_001) + '"',
        )
        for html in (malformed, truncated, duplicate, overlarge):
            with self.subTest(size=len(html)):
                result = self.adapter.list_jobs(
                    RecordingFetcher(Page(BOARD_URL, html)),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_missing_or_invalid_module_identity_evidence(self):
        for module_id in (None, "0", "not-an-id", 26098):
            with self.subTest(module_id=module_id):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        Page(BOARD_URL, board_html(module_id=module_id))
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_reads_official_inventory_titles_locations_and_details(self):
        response = Page(
            BOARD_URL,
            board_html([job(), job(3932631, "Accounting Manager", "Remote")]),
            source="fixture",
        )
        result = self.adapter.list_jobs(
            RecordingFetcher(response),
            self.board,
            JobQuery(title="Financial Analyst"),
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(
            [(item.title, item.location) for item in result.candidates],
            [("Financial Analyst", "Remote; Actabl Denver; Denver, CO, USA")],
        )
        self.assertEqual(
            result.candidates[0].url,
            "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4284086",
        )
        self.assertEqual(
            result.candidates[0].raw,
            {"job_id": "4284086", "module_id": "26098", "is_remote": True},
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.trace["pages_fetched"], 1)

    def test_location_is_deferred_to_identity_matcher_and_internal_jobs_are_hidden(self):
        internal = job(5000000, "Financial Analyst", "New York")
        internal["IsInternal"] = True
        response = Page(
            BOARD_URL,
            board_html([job(), internal]),
            source="fixture",
        )

        result = self.adapter.list_jobs(
            RecordingFetcher(response),
            self.board,
            JobQuery(title="Financial Analyst", location="United States"),
        )

        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ["https://recruiting.paylocity.com/Recruiting/Jobs/Details/4284086"],
        )

    def test_uses_job_location_fallback_and_bounds_oversized_inventory(self):
        fallback = job(location=None)
        fallback["LocationName"] = ""
        oversized = [job(index + 1, f"Opening {index}") for index in range(1001)]
        fallback_result = self.adapter.list_jobs(
            RecordingFetcher(Page(BOARD_URL, board_html([fallback]))),
            self.board,
            JobQuery(),
        )
        bounded = self.adapter.list_jobs(
            RecordingFetcher(Page(BOARD_URL, board_html(oversized))),
            self.board,
            JobQuery(),
        )
        self.assertEqual(
            fallback_result.candidates[0].location,
            "Remote; Denver, CO, USA",
        )
        self.assertEqual(len(bounded.candidates), 1000)
        self.assertFalse(bounded.inventory_complete)
        self.assertEqual(bounded.trace["pagination_limit"], 1000)

    def test_fails_closed_for_redirects_tenant_conflicts_and_invalid_records(self):
        redirect = self.adapter.list_jobs(
            RecordingFetcher(
                Page(
                    BOARD_URL,
                    board_html(),
                    final_url=BOARD_URL.replace(TENANT, OTHER_TENANT),
                )
            ),
            self.board,
            JobQuery(),
        )
        conflict = self.adapter.list_jobs(
            RecordingFetcher(Page(BOARD_URL, board_html(tenant=OTHER_TENANT))),
            self.board,
            JobQuery(),
        )
        bad_record = job()
        bad_record["JobLocation"]["ModuleId"] = 99
        invalid = self.adapter.list_jobs(
            RecordingFetcher(Page(BOARD_URL, board_html([bad_record]))),
            self.board,
            JobQuery(),
        )
        for result in (redirect, conflict, invalid):
            self.assertIn(result.reason_code, {"PROVIDER_VARIANT_UNSUPPORTED", "INVALID_STRUCTURED_DATA"})
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_preserves_typed_network_failure(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)


if __name__ == "__main__":
    unittest.main()
