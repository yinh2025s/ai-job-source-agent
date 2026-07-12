import unittest

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.rippling import RipplingAdapter
from job_source_agent.web import FetchError, Page


class StubFetcher:
    def __init__(self, html="", error=None):
        self.html = html
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error is not None:
            raise self.error
        return Page(url=url, final_url=url, html=self.html, source="rippling-fixture")


class RipplingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RipplingAdapter()

    def test_recognizes_public_board_and_detail_urls_without_lookalikes(self):
        accepted = (
            "https://ats.rippling.com/embed/acme/jobs",
            "https://ats.rippling.com/acme/jobs",
            "https://ats.rippling.com/en-US/acme/jobs/12345678-abcd-1234-abcd-1234567890ab",
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

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
