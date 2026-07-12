import unittest
from pathlib import Path

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.successfactors import ADAPTER, SuccessFactorsAdapter
from job_source_agent.web import FetchError, Page


FIXTURE = (
    Path(__file__).parents[1]
    / "samples"
    / "sites"
    / "successfactors.example"
    / "ajax-theme.html"
)


class StubFetcher:
    def __init__(self, html):
        self.html = html
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        return Page(url=url, final_url=url, html=self.html, source="successfactors-fixture")


class SuccessFactorsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SuccessFactorsAdapter()

    def test_exported_adapter_satisfies_provider_contract(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertEqual(ADAPTER.name, "successfactors")
        self.assertTrue(ADAPTER.supports_listing)

    def test_recognizes_successfactors_and_sapsf_without_lookalikes(self):
        self.assertTrue(self.adapter.recognizes("https://career4.successfactors.com/career"))
        self.assertTrue(self.adapter.recognizes("https://acme.jobs.sapsf.com/career"))
        self.assertFalse(self.adapter.recognizes("https://successfactors.com.evil.example/career"))
        self.assertFalse(self.adapter.recognizes("https://example.com/successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("ftp://career4.successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("https://career4.successfactors.com:8443/career"))
        self.assertFalse(self.adapter.recognizes("https://user@career4.successfactors.com/career"))
        self.assertFalse(self.adapter.recognizes("https://[broken/career"))

    def test_identifies_board_and_removes_detail_or_search_parameters(self):
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&career_ns=job_listing"
            "&career_job_req_id=987&keyword=analyst&rcm_site_locale=en_US#top"
        )

        self.assertEqual(board.identifier, "Acme")
        self.assertEqual(
            board.url,
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US",
        )
        self.assertIsNone(self.adapter.identify_board("https://careers.example.com/jobs"))

    def test_parses_embedded_json_and_reconstructs_detail_urls(self):
        html = """
        <script type="application/json">
          {"results":[
            {"jobTitle":" Data Analyst ","jobReqId":"987","location":"New York"},
            {"title":"ML Engineer","career_job_req_id":654,
             "jobLocation":{"address":{"addressLocality":"Paris","addressCountry":"FR"}}}
          ]}
        </script>
        """
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US"
        )

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery(title="Data Analyst"))

        self.assertEqual(
            result.trace["search_urls"],
            [
                "https://career4.successfactors.com/career?company=Acme"
                "&rcm_site_locale=en_US&keyword=Data+Analyst"
            ],
        )
        self.assertEqual([candidate.title for candidate in result.candidates], ["Data Analyst", "ML Engineer"])
        self.assertIn("career_ns=job_listing", result.candidates[0].url)
        self.assertIn("career_job_req_id=987", result.candidates[0].url)
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].location, "Paris, FR")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_parses_json_inside_javascript_assignment_and_explicit_url(self):
        html = """
        <script>
          window.__JOBS__ = {"jobs":[
            {"jobTitle":"Platform Engineer","jobReqId":"123",
             "jobUrl":"/career?company=Acme&career_ns=job_listing&career_job_req_id=123"}
          ]};
        </script>
        """
        board = self.adapter.identify_board("https://acme.jobs.sapsf.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            "https://acme.jobs.sapsf.com/career?company=Acme"
            "&career_ns=job_listing&career_job_req_id=123",
        )
        self.assertEqual(result.candidates[0].raw, {"job_req_id": "123"})

    def test_extracts_job_links_and_deduplicates_embedded_records(self):
        html = """
        <a href="/career?company=Acme&amp;career_ns=job_listing&amp;career_job_req_id=987">
          Data Analyst
        </a>
        <script type="application/json">
          {"jobTitle":"Data Analyst","jobReqId":"987"}
        </script>
        """
        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertIn("career_job_req_id=987", result.candidates[0].url)

    def test_rejects_external_structured_urls(self):
        html = """
        <script type="application/json">
          {"jobTitle":"Fake Job","jobUrl":"https://evil.example/jobs/123"}
        </script>
        """
        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

    def test_parses_theme_ajax_nested_payload_and_pagination_metadata(self):
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme&rcm_site_locale=en_US"
        )

        result = self.adapter.list_jobs(
            StubFetcher(FIXTURE.read_text(encoding="utf-8")),
            board,
            JobQuery(),
        )

        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Theme Platform Engineer", "AJAX Data Engineer"],
        )
        self.assertEqual(result.candidates[0].location, "Austin, TX")
        self.assertEqual(result.candidates[1].location, "Remote - US")
        self.assertTrue(all("company=Acme" in candidate.url for candidate in result.candidates))
        self.assertEqual(result.trace["pagination"], {
            "total_results": 42,
            "page_size": 10,
            "current_page": 2,
            "offset": 10,
            "has_more": True,
            "next_page": 3,
        })

    def test_rejects_other_successfactors_hosts_and_companies(self):
        html = """
        <script type="application/json">{"jobs":[
          {"jobTitle":"Other Company","jobReqId":"1",
           "jobUrl":"https://career4.successfactors.com/career?company=Other&career_job_req_id=1"},
          {"jobTitle":"Other Host","jobReqId":"2",
           "jobUrl":"https://career5.successfactors.com/career?company=Acme&career_job_req_id=2"},
          {"jobTitle":"Bad Scheme","jobReqId":"4",
           "jobUrl":"ftp://career4.successfactors.com/career?company=Acme&career_job_req_id=4"},
          {"jobTitle":"Ambiguous Company","jobReqId":"5",
           "jobUrl":"/career?company=Acme&company=Other&career_job_req_id=5"},
          {"jobTitle":"Relative Same Tenant","jobReqId":"3",
           "jobUrl":"/career?career_job_req_id=3"}
        ]}</script>
        """
        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme"
        )

        result = self.adapter.list_jobs(StubFetcher(html), board, JobQuery())

        self.assertEqual([candidate.title for candidate in result.candidates], ["Relative Same Tenant"])
        self.assertIn("company=Acme", result.candidates[0].url)

    def test_returns_retryable_fetch_failure(self):
        class FailingFetcher:
            def fetch(self, url, data=None, headers=None):
                raise FetchError("offline")

        board = self.adapter.identify_board(
            "https://career4.successfactors.com/career?company=Acme"
        )

        result = self.adapter.list_jobs(FailingFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertIn("offline", result.trace["error"])

    def test_returns_structured_errors_for_missing_identifier_invalid_and_empty_data(self):
        missing = JobBoard(
            url="https://career4.successfactors.com/career",
            provider="successfactors",
        )
        unsupported = self.adapter.list_jobs(StubFetcher(""), missing, JobQuery())
        self.assertEqual(unsupported.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

        board = self.adapter.identify_board("https://career4.successfactors.com/career?company=Acme")
        invalid = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[}</script>'),
            board,
            JobQuery(),
        )
        empty = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">{"jobs":[]}</script>'),
            board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(empty.candidates, [])


if __name__ == "__main__":
    unittest.main()
