import unittest

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.workable import WorkableAdapter
from job_source_agent.web import Page


class StubFetcher:
    def __init__(self, html):
        self.html = html
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        return Page(url=url, final_url=url, html=self.html, source="workable-fixture")


class WorkableAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WorkableAdapter()

    def test_recognizes_only_public_workable_host(self):
        self.assertTrue(self.adapter.recognizes("https://apply.workable.com/acme/"))
        self.assertTrue(self.adapter.recognizes("https://APPLY.WORKABLE.COM/acme/j/ABC123/"))
        self.assertFalse(self.adapter.recognizes("https://workable.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com.example.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://apply.workable.com:bad/acme"))

    def test_identifies_canonical_account_board_from_list_or_detail_url(self):
        board = self.adapter.identify_board(
            "https://apply.workable.com/acme-inc/j/ABC123/?utm_source=test"
        )

        self.assertEqual(
            board,
            JobBoard(
                url="https://apply.workable.com/acme-inc/",
                provider="workable",
                identifier="acme-inc",
            ),
        )
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com/"))
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com/bad.slug"))
        self.assertIsNone(self.adapter.identify_board("https://apply.workable.com:bad/acme"))

    def test_lists_nested_embedded_json_jobs_and_normalizes_detail_urls(self):
        fetcher = StubFetcher(
            """
            <script id="__NEXT_DATA__" type="application/json">
              {"props":{"pageProps":{"jobs":[
                {"title":"  AI Engineer  ","shortcode":"ABC123","location":" New York "},
                {"name":"Product Manager","shortCode":"PM-456", "location":{
                  "city":"Paris","region":"Ile-de-France","country":"FR"
                }},
                {"title":"Incomplete"}
              ]}}}
            </script>
            """
        )
        board = self.adapter.identify_board("https://apply.workable.com/acme/")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="AI Engineer"))

        self.assertEqual(fetcher.requested_urls, ["https://apply.workable.com/acme/"])
        self.assertEqual([item.title for item in result.candidates], ["AI Engineer", "Product Manager"])
        self.assertEqual(result.candidates[0].url, "https://apply.workable.com/acme/j/ABC123/")
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].url, "https://apply.workable.com/acme/j/PM-456/")
        self.assertEqual(result.candidates[1].location, "Paris, Ile-de-France, FR")
        self.assertEqual(result.candidates[0].raw, {"shortcode": "ABC123"})
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.trace["response_source"], "workable-fixture")

    def test_accepts_script_assignment_urls_and_deduplicates_candidates(self):
        fetcher = StubFetcher(
            """
            <script>
              window.__INITIAL_STATE__ = {"jobs":[
                {"title":"Data Analyst","url":"/acme/j/DATA_1/"},
                {"title":"Data Analyst duplicate","applicationUrl":
                  "https://apply.workable.com/acme/j/DATA_1/"},
                {"title":"External","url":"https://evil.example/jobs/NOPE"}
              ]};
            </script>
            """
        )
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://apply.workable.com/acme/j/DATA_1/")

    def test_missing_identifier_returns_structured_failure(self):
        board = JobBoard(url="https://apply.workable.com/", provider="workable")

        result = self.adapter.list_jobs(StubFetcher(""), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])

    def test_invalid_or_unrelated_payload_returns_invalid_structured_data(self):
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        missing_json = self.adapter.list_jobs(StubFetcher("<html>no jobs</html>"), board, JobQuery())
        malformed = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{bad json}</script>'), board, JobQuery()
        )
        unrelated = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"company":"Acme"}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(missing_json.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(unrelated.reason_code, "INVALID_STRUCTURED_DATA")

    def test_empty_jobs_returns_empty_provider_response(self):
        board = self.adapter.identify_board("https://apply.workable.com/acme")

        result = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[]}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
