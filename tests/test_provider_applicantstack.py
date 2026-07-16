import unittest

from job_source_agent.providers.applicantstack import ADAPTER, ApplicantStackAdapter
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://aarris.applicantstack.com/x/openings"


def board_html(rows="", *, tenant="aarris", branded=True):
    branding = '<div id="asbranding">ApplicantStack</div>' if branded else ""
    return (
        f'<form id="mainform" action="https://{tenant}.applicantstack.com/x/openings"></form>'
        '<table id="data-table"><thead><tr><th>Title</th><th>Location</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>{branding}"
    )


def job_row(title, href, location="Remote"):
    return (
        '<tr class="oddrow"><td>'
        f'<a href="{href}">{title}</a></td><td>{location}</td><td>Operations</td></tr>'
    )


class RecordingFetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        return self.page


class ApplicantStackAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ApplicantStackAdapter()
        self.board = JobBoard(BOARD_URL, "applicantstack", "aarris")

    def test_native_adapter_recognizes_and_canonicalizes_public_routes(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["applicantstack"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            BOARD_URL,
            BOARD_URL + "/",
            "https://aarris.applicantstack.com/x/detail/a27xztr5mziq",
            "https://AARRIS.APPLICANTSTACK.COM/x/detail/A27XZTR5MZIQ?source=careers",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

        second = self.adapter.identify_board(
            "https://second-tenant.applicantstack.com/x/detail/abc12345"
        )
        self.assertEqual(
            second,
            JobBoard(
                "https://second-tenant.applicantstack.com/x/openings",
                "applicantstack",
                "second-tenant",
            ),
        )

    def test_rejects_unsafe_hosts_routes_and_detail_ids(self):
        for url in (
            "http://aarris.applicantstack.com/x/openings",
            "https://applicantstack.com/x/openings",
            "https://aarris.other.applicantstack.com/x/openings",
            "https://user@aarris.applicantstack.com/x/openings",
            "https://aarris.applicantstack.com:8443/x/openings",
            "https://aarris.applicantstack.com.evil.example/x/openings",
            "https://aarris.applicantstack.com/x/about",
            "https://aarris.applicantstack.com/x/detail/short",
            "https://aarris.applicantstack.com/x/detail/abc12345/extra",
            "https://aarris.applicantstack.com/x/detail/abc123%2F45",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_jobs_with_location_and_canonical_detail_urls(self):
        html = board_html(
            job_row(
                " (9A) RN &amp; Case Manager ",
                "https://aarris.applicantstack.com/x/detail/a27xztr5mziq?source=careers",
                " Los Angeles, CA ",
            )
            + job_row(
                "Caregiver",
                "/x/detail/a27xztrypyyj/",
                "San Diego, CA",
            )
        )
        fetcher = RecordingFetcher(Page(url=BOARD_URL, html=html, source="offline-fixture"))

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="9A RN & Case Manager")
        )

        self.assertEqual(fetcher.requests, [(BOARD_URL, None, None)])
        self.assertIsNone(result.reason_code)
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["(9A) RN & Case Manager", "Caregiver"],
        )
        self.assertEqual(
            result.candidates[0].url,
            "https://aarris.applicantstack.com/x/detail/a27xztr5mziq",
        )
        self.assertEqual(result.candidates[0].location, "Los Angeles, CA")
        self.assertEqual(result.candidates[0].raw, {"job_id": "a27xztr5mziq"})
        self.assertTrue(result.trace["exact_title_found"])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")

    def test_rejects_cross_tenant_invalid_and_duplicate_rows_as_partial(self):
        html = board_html(
            job_row("Valid", "/x/detail/abc12345")
            + job_row("Other tenant", "https://other.applicantstack.com/x/detail/def67890")
            + job_row("Bad route", "/x/openings")
            + job_row("Duplicate", "/x/detail/abc12345")
            + job_row("", "/x/detail/ghi12345")
            + (
                '<tr><td><a href="/x/detail/jkl12345">Ambiguous one</a>'
                '<a href="/x/detail/mno12345">Ambiguous two</a></td>'
                '<td>Remote</td></tr>'
            )
        )

        result = self.adapter.list_jobs(
            RecordingFetcher(Page(url=BOARD_URL, html=html)), self.board, JobQuery()
        )

        self.assertEqual([candidate.title for candidate in result.candidates], ["Valid"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "partial")
        self.assertEqual(result.trace["public_row_count"], 6)
        self.assertEqual(result.trace["rejected_row_count"], 5)

    def test_fingerprinted_empty_board_is_complete_and_nonretryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(Page(url=BOARD_URL, html=board_html())),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.retryable)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")

    def test_invalid_board_redirect_and_weak_html_fail_closed(self):
        invalid_board = JobBoard(BOARD_URL, "applicantstack", "other")
        self.assertEqual(
            self.adapter.list_jobs(RecordingFetcher(), invalid_board, JobQuery()).reason_code,
            "PROVIDER_VARIANT_UNSUPPORTED",
        )

        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                Page(
                    url=BOARD_URL,
                    final_url="https://other.applicantstack.com/x/openings",
                    html=board_html(),
                )
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(redirected.retryable)

        for html in (
            "<html>ApplicantStack</html>",
            board_html(branded=False),
            board_html(tenant="other"),
        ):
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(Page(url=BOARD_URL, html=html)),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.retryable)

    def test_fetch_failure_is_retryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")), self.board, JobQuery()
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
