import unittest

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.icims import ICIMSAdapter
from job_source_agent.web import FetchError, Page


class StubFetcher:
    def __init__(self, html: str = "", error: Exception | None = None):
        self.html = html
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error:
            raise self.error
        return Page(url=url, final_url=url, html=self.html, source="icims-fixture")


class ICIMSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ICIMSAdapter()

    def test_recognizes_only_icims_careers_job_search_and_detail_urls(self):
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/search"))
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/search?ss=1"))
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/123/data-analyst/job"))
        self.assertFalse(self.adapter.recognizes("https://careers-acme.icims.com/"))
        self.assertFalse(self.adapter.recognizes("https://jobs-acme.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://example.com/jobs/careers-acme.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://careers-.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("http://[invalid/jobs/search"))

    def test_identifies_search_page_and_canonicalizes_detail_to_board(self):
        search = self.adapter.identify_board(
            "https://careers-acme.icims.com/jobs/search-jsonld?ss=1#results"
        )
        detail = self.adapter.identify_board(
            "https://careers-acme.icims.com/jobs/123/data-analyst/job?mode=job"
        )

        self.assertEqual(search, JobBoard(
            url="https://careers-acme.icims.com/jobs/search-jsonld",
            provider="icims",
            identifier="careers-acme.icims.com",
        ))
        self.assertEqual(detail, JobBoard(
            url="https://careers-acme.icims.com/jobs/search",
            provider="icims",
            identifier="careers-acme.icims.com",
        ))

    def test_lists_json_ld_job_postings_and_normalizes_locations(self):
        fetcher = StubFetcher("""
            <script type="application/ld+json">
              {"@type":"ItemList","itemListElement":[
                {"@type":"JobPosting","title":" Data Analyst ",
                 "url":"/jobs/2345/data-analyst/job?utm_source=test",
                 "jobLocation":{"address":{"addressLocality":"New York",
                                             "addressRegion":"NY"}}},
                {"@type":"Organization","name":"Not a job","url":"/jobs/9/no/job"},
                {"@type":"JobPosting","title":"External",
                 "url":"https://example.com/jobs/1/external/job"}
              ]}
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        self.assertEqual(fetcher.requested_urls, [board.url])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers-acme.icims.com/jobs/2345/data-analyst/job",
        )
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["structured_script_count"], 1)
        self.assertEqual(result.trace["candidate_count"], 1)

    def test_lists_embedded_jobs_and_builds_missing_detail_urls(self):
        fetcher = StubFetcher("""
            <script type="application/json">
              {"jobs":[
                {"id":"3456","title":"Data Analyst","location":"New York"},
                {"jobId":4567,"jobTitle":"ML Engineer",
                 "detailUrl":"/jobs/4567/ml-engineer/job",
                 "location":{"city":"Boston","state":"MA"}},
                {"id":"no-title","location":"Remote"}
              ],"navigation":{"id":"1","title":"Careers"}}
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search-embedded")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual([candidate.title for candidate in result.candidates], [
            "Data Analyst",
            "ML Engineer",
        ])
        self.assertEqual(
            result.candidates[0].url,
            "https://careers-acme.icims.com/jobs/3456/data-analyst/job",
        )
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].location, "Boston, MA")
        self.assertEqual(result.candidates[0].raw["id"], "3456")

    def test_parses_json_wrapped_in_an_application_json_assignment(self):
        fetcher = StubFetcher("""
            <script type="application/json">
              window.ICIMS_JOBS = {"postings":[
                {"jobNumber":"7890","name":"Security Engineer"}
              ]};
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertIn("/jobs/7890/security-engineer/job", result.candidates[0].url)

    def test_deduplicates_job_postings_found_in_multiple_scripts(self):
        posting = (
            '{"@type":"JobPosting","title":"Data Analyst",'
            '"url":"/jobs/2345/data-analyst/job"}'
        )
        fetcher = StubFetcher(
            f'<script type="application/ld+json">{posting}</script>'
            f'<script type="application/ld+json">{posting}</script>'
        )
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)

    def test_returns_structured_failures(self):
        unsupported = self.adapter.list_jobs(
            StubFetcher(),
            JobBoard(url="https://careers.icims.com/jobs/search", provider="icims"),
            JobQuery(),
        )
        failed = self.adapter.list_jobs(
            StubFetcher(error=FetchError("blocked")),
            JobBoard(
                url="https://careers-acme.icims.com/jobs/search",
                provider="icims",
                identifier="careers-acme.icims.com",
            ),
            JobQuery(),
        )
        empty = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">not json</script>'),
            JobBoard(
                url="https://careers-acme.icims.com/jobs/search",
                provider="icims",
                identifier="careers-acme.icims.com",
            ),
            JobQuery(),
        )

        self.assertEqual(unsupported.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")


if __name__ == "__main__":
    unittest.main()
