import unittest
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.rippling import RipplingAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parents[1] / "samples" / "sites" / "ats.rippling.com"


class StubFetcher:
    def __init__(self, html="", error=None, final_url=None):
        self.html = html
        self.error = error
        self.final_url = final_url
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error is not None:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="rippling-fixture",
        )


class RipplingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RipplingAdapter()

    def test_recognizes_public_board_and_detail_urls_without_lookalikes(self):
        accepted = (
            "https://ats.rippling.com/embed/acme/jobs",
            "https://ats.rippling.com/acme/jobs",
            "https://ats.rippling.com/en-US/acme/jobs/12345678-abcd-1234-abcd-1234567890ab",
            "https://ats.rippling.com/es-419/acme/jobs/12345678-abcd-1234-abcd-1234567890ab",
        )
        rejected = (
            "https://rippling.com/acme/jobs",
            "https://ats.rippling.com.example.com/embed/acme/jobs",
            "https://evil.example/embed/acme/jobs",
            "https://ats.rippling.com/embed/acme",
            "https://ats.rippling.com/embed/acme/jobs/not-a-job",
            "https://ats.rippling.com:bad/embed/acme/jobs",
        )

        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))

    def test_identifies_canonical_embed_board_from_supported_variants(self):
        expected = JobBoard(
            url="https://ats.rippling.com/embed/acme-inc/jobs",
            provider="rippling",
            identifier="acme-inc",
        )

        self.assertEqual(
            self.adapter.identify_board("https://ats.rippling.com/acme-inc/jobs?ref=site"),
            expected,
        )
        self.assertEqual(
            self.adapter.identify_board(
                "https://ats.rippling.com/en-US/acme-inc/jobs/12345678-abcd-1234-abcd-1234567890ab?source=test"
            ),
            expected,
        )
        self.assertEqual(
            self.adapter.identify_board(
                "https://ats.rippling.com/es-419/acme-inc/jobs/12345678-abcd-1234-abcd-1234567890ab"
            ),
            expected,
        )
        self.assertIsNone(self.adapter.identify_board("https://ats.rippling.com/embed/bad.slug/jobs"))

    def test_lists_only_verified_same_board_job_links_and_deduplicates(self):
        fetcher = StubFetcher(
            """
            <main>
              <a href="/en-US/acme/jobs/12345678-aaaa-1234-abcd-1234567890ab"
                 data-job-location=" New York, NY "><span> Data Analyst </span></a>
              <a href="https://ats.rippling.com/en-US/acme/jobs/12345678-aaaa-1234-abcd-1234567890ab">
                Duplicate title
              </a>
              <a href="/acme/jobs/87654321-bbbb-1234-abcd-1234567890ab" data-location="Remote"
                 aria-label="Software Engineer"></a>
              <a href="/en-US/other/jobs/11111111-cccc-1234-abcd-1234567890ab">Other company</a>
              <a href="https://evil.example/en-US/acme/jobs/22222222-dddd-1234-abcd-1234567890ab">External</a>
              <a href="/en-US/acme/jobs">Jobs home</a>
            </main>
            """
        )
        board = self.adapter.identify_board("https://ats.rippling.com/embed/acme/jobs")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        self.assertEqual(fetcher.requested_urls, ["https://ats.rippling.com/embed/acme/jobs"])
        self.assertEqual([item.title for item in result.candidates], ["Data Analyst", "Software Engineer"])
        self.assertEqual(
            result.candidates[0].url,
            "https://ats.rippling.com/en-US/acme/jobs/12345678-aaaa-1234-abcd-1234567890ab",
        )
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(
            result.candidates[0].raw,
            {"job_id": "12345678-aaaa-1234-abcd-1234567890ab"},
        )
        self.assertEqual(result.candidates[1].location, "Remote")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.trace["response_source"], "rippling-fixture")

    def test_missing_identifier_returns_structured_failure(self):
        board = JobBoard(url="https://ats.rippling.com/embed/jobs", provider="rippling")

        result = self.adapter.list_jobs(StubFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])

    def test_fetch_failure_is_retryable_and_structured(self):
        board = self.adapter.identify_board("https://ats.rippling.com/embed/acme/jobs")

        result = self.adapter.list_jobs(
            StubFetcher(error=FetchError("network unavailable")),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertIn("network unavailable", result.trace["error"])

    def test_page_without_verified_jobs_returns_empty_provider_response(self):
        board = self.adapter.identify_board("https://ats.rippling.com/embed/acme/jobs")

        result = self.adapter.list_jobs(
            StubFetcher(
                '<a href="/en-US/other/jobs/12345678-abcd-1234-abcd-1234567890ab">Not ours</a>'
            ),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 0)

    def test_live_shape_next_data_enriches_anchor_candidates(self):
        html = (FIXTURES / "allvoices" / "jobs" / "index.html").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://ats.rippling.com/allvoices/jobs")

        result = self.adapter.list_jobs(
            StubFetcher(html),
            board,
            JobQuery(title="Technical QA Automation Specialist"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 2)
        candidate = result.candidates[1]
        self.assertEqual(candidate.title, "Technical QA Automation Specialist")
        self.assertEqual(
            candidate.url,
            "https://ats.rippling.com/allvoices/jobs/33b302b3-3a5e-4603-9a5a-79ec67793e73",
        )
        self.assertEqual(candidate.location, "Santa Monica, CA; Remote (United States)")
        self.assertEqual(
            candidate.raw,
            {
                "job_id": "33b302b3-3a5e-4603-9a5a-79ec67793e73",
                "department": "Technology",
                "language": "en-US",
            },
        )
        self.assertEqual(result.trace["structured_state"], "present")
        self.assertEqual(result.trace["structured_record_count"], 2)
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_live_shape_explicit_empty_jobs_state_is_not_a_js_shell_failure(self):
        html = (FIXTURES / "swiftcomply" / "jobs" / "index.html").read_text(encoding="utf-8")
        board = self.adapter.identify_board("https://ats.rippling.com/swiftcomply/jobs")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["structured_state"], "empty")

    def test_malformed_next_data_is_reported_but_valid_anchors_survive(self):
        html = """
        <a href="/acme/jobs/12345678-aaaa-1234-abcd-1234567890ab">Data Analyst</a>
        <script id="__NEXT_DATA__" type="application/json">{broken</script>
        """
        board = self.adapter.identify_board("https://ats.rippling.com/acme/jobs")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertIsNone(result.reason_code)
        self.assertEqual([candidate.title for candidate in result.candidates], ["Data Analyst"])
        self.assertEqual(result.trace["structured_state"], "invalid")
        self.assertIn("structured_error", result.trace)

    def test_js_shell_without_jobs_state_is_classified_as_unsupported_variant(self):
        board = self.adapter.identify_board("https://ats.rippling.com/acme/jobs")

        result = self.adapter.list_jobs(
            StubFetcher('<div id="__next"></div>'),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.trace["structured_state"], "missing")

    def test_rejects_cross_tenant_board_redirect(self):
        board = self.adapter.identify_board("https://ats.rippling.com/acme/jobs")

        result = self.adapter.list_jobs(
            StubFetcher(
                "<html></html>",
                final_url="https://ats.rippling.com/other/jobs",
            ),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertIn("outside the expected tenant", result.trace["error"])

    def test_structured_state_rejects_cross_tenant_and_mismatched_job_urls(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"dehydratedState":{"queries":[{"state":{"data":{"items":[
          {"id":"12345678-aaaa-1234-abcd-1234567890ab","name":"Other tenant","url":"https://ats.rippling.com/other/jobs/12345678-aaaa-1234-abcd-1234567890ab"},
          {"id":"87654321-bbbb-1234-abcd-1234567890ab","name":"Wrong id","url":"https://ats.rippling.com/acme/jobs/12345678-aaaa-1234-abcd-1234567890ab"}
        ]}}}]}}}}
        </script>
        """
        board = self.adapter.identify_board("https://ats.rippling.com/acme/jobs")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
