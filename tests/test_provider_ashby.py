import json
from pathlib import Path
import unittest

from job_source_agent.providers.ashby import AshbyAdapter
from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parents[1] / "samples" / "sites" / "jobs.ashbyhq.com"


class StubFetcher:
    def __init__(self, responses):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        html = response if isinstance(response, str) else json.dumps(response)
        return Page(url=url, final_url=url, html=html, source="ashby-fixture")


class AshbyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AshbyAdapter()

    def test_recognizes_only_public_ashby_job_boards(self):
        self.assertTrue(self.adapter.recognizes("https://jobs.ashbyhq.com/acme"))
        self.assertTrue(self.adapter.recognizes("https://JOBS.ASHBYHQ.COM:443/acme/"))
        self.assertTrue(self.adapter.recognizes(
            "https://jobs.ashbyhq.com/acme?utm_source=careers"
        ))
        self.assertTrue(self.adapter.recognizes(
            "https://api.ashbyhq.com/posting-api/job-board/acme"
        ))
        self.assertTrue(self.adapter.recognizes(
            "https://jobs.ashbyhq.com/acme/embed?version=2"
        ))
        self.assertTrue(self.adapter.recognizes(
            "https://jobs.ashbyhq.com/acme/job-id?embed=true"
        ))

        invalid_urls = (
            "https://example.com/jobs.ashbyhq.com/acme",
            "https://jobs.ashbyhq.com.evil.example/acme",
            "https://user@jobs.ashbyhq.com/acme",
            "https://jobs.ashbyhq.com:8443/acme",
            "https://jobs.ashbyhq.com:80/acme",
            "http://jobs.ashbyhq.com:443/acme",
            "https://jobs.ashbyhq.com/acme?token=secret",
            "https://jobs.ashbyhq.com/acme/embed?version=two",
            "https://jobs.ashbyhq.com/acme/job-id/extra",
            "https://jobs.ashbyhq.com/acme#jobs",
            "https://jobs.ashbyhq.com/acme%2Fevil",
            "https://jobs.ashbyhq.com/acme//",
            "https://jobs.ashbyhq.com/.invalid",
            f"https://jobs.ashbyhq.com/{'a' * 129}",
            "https://api.ashbyhq.com/acme",
            "https://api.ashbyhq.com/posting-api/job-board/acme/extra",
            "https://api.ashbyhq.com/posting-api/job-board/acme?embed=true",
            "https://api.ashbyhq.com/posting-api/job-board/acme#jobs",
            "https://api.ashbyhq.com/posting-api/job-board/acme%2Fevil",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))

    def test_identifies_and_canonicalizes_jobs_and_api_board_urls(self):
        jobs_board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")
        api_board = self.adapter.identify_board(
            "https://api.ashbyhq.com/posting-api/job-board/acme"
        )

        expected = JobBoard(
            url="https://jobs.ashbyhq.com/acme",
            provider="ashby",
            identifier="acme",
        )
        self.assertEqual(jobs_board, expected)
        self.assertEqual(api_board, expected)
        self.assertIsNone(self.adapter.identify_board("https://jobs.ashbyhq.com/"))
        self.assertIsNone(self.adapter.identify_board("https://jobs.ashbyhq.com/acme%2Fevil"))
        self.assertIsNone(self.adapter.identify_board("https://api.ashbyhq.com/acme"))

    def test_lists_normalized_candidates_from_posting_api_without_fallback(self):
        fetcher = StubFetcher({
            "jobs": [
                {
                    "id": "job-1",
                    "title": "  AI Engineer  ",
                    "jobUrl": " https://jobs.ashbyhq.com/acme/job-1?utm_source=test ",
                    "location": "  New York, NY  ",
                },
                {
                    "id": "job-2",
                    "title": "Product Manager",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/job-2",
                    "location": {"name": "Remote"},
                },
                {"id": "cross-board", "title": "Wrong", "jobUrl": "https://jobs.ashbyhq.com/other/job-3"},
                {"id": "cross-host", "title": "Wrong", "jobUrl": "https://evil.example/acme/job-4"},
                {"id": "missing-url", "title": "Incomplete"},
            ]
        })
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="AI Engineer"))

        self.assertEqual(fetcher.requested_urls, [
            "https://api.ashbyhq.com/posting-api/job-board/acme",
        ])
        self.assertEqual([candidate.title for candidate in result.candidates], [
            "AI Engineer",
            "Product Manager",
        ])
        self.assertEqual(result.candidates[0].url, "https://jobs.ashbyhq.com/acme/job-1")
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(result.candidates[1].location, "Remote")
        self.assertEqual(result.candidates[0].raw, {"id": "job-1"})
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["response_mode"], "api")
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_merges_primary_and_secondary_api_locations_in_stable_order(self):
        fetcher = StubFetcher({
            "jobs": [{
                "id": "middesk-job",
                "title": "Software Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/middesk/middesk-job",
                "location": "San Francisco",
                "secondaryLocations": [
                    {
                        "location": "New York",
                        "address": {
                            "postalAddress": {
                                "addressLocality": "New York",
                                "addressRegion": "New York",
                                "addressCountry": "USA",
                            }
                        },
                    },
                    {"name": "  Remote   US  "},
                    {
                        "address": {
                            "postalAddress": {
                                "addressLocality": "Austin",
                                "addressRegion": "TX",
                                "addressCountry": "USA",
                            }
                        }
                    },
                    {"location": " san   francisco "},
                    {"name": "New York"},
                ],
            }]
        })

        result = self.adapter.list_jobs(
            fetcher,
            self.adapter.identify_board("https://jobs.ashbyhq.com/middesk"),
            JobQuery(),
        )

        self.assertEqual(
            result.candidates[0].location,
            "San Francisco; New York; Remote US; Austin, TX, USA",
        )

    def test_ignores_malformed_secondary_api_locations(self):
        malformed_values = (
            None,
            "New York",
            {"location": "New York"},
            [None, 42, [], {}, {"location": 42}, {"name": []}],
            [
                {"address": None},
                {"address": {"postalAddress": "New York"}},
                {"address": {"postalAddress": {"addressLocality": []}}},
            ],
        )
        for secondary_locations in malformed_values:
            with self.subTest(secondary_locations=secondary_locations):
                result = self.adapter.list_jobs(
                    StubFetcher({
                        "jobs": [{
                            "id": "job-1",
                            "title": "AI Engineer",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/job-1",
                            "location": {"name": "Remote"},
                            "secondaryLocations": secondary_locations,
                        }]
                    }),
                    self.adapter.identify_board("https://jobs.ashbyhq.com/acme"),
                    JobQuery(),
                )

                self.assertEqual(result.candidates[0].location, "Remote")

    def test_api_failure_falls_back_to_embedded_payload_fixture(self):
        html = (FIXTURES / "embedded-acme" / "index.html").read_text(encoding="utf-8")
        fetcher = StubFetcher([FetchError("API unavailable"), html])
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/embedded-acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(fetcher.requested_urls, [
            "https://api.ashbyhq.com/posting-api/job-board/embedded-acme",
            "https://jobs.ashbyhq.com/embedded-acme",
        ])
        self.assertEqual([candidate.title for candidate in result.candidates], [
            "Machine Learning Engineer",
            "Product Designer",
        ])
        self.assertEqual(result.candidates[0].url, "https://jobs.ashbyhq.com/embedded-acme/ml-123")
        self.assertEqual(result.candidates[0].location, "San Francisco, CA")
        self.assertEqual(result.trace["response_mode"], "embedded_json")
        self.assertEqual(result.trace["fallback_reason"], "api_fetch_failed")
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_invalid_api_json_uses_assigned_javascript_payload(self):
        html = """
            <script>
              window.__ASHBY_STATE__ = {"jobBoard":{"jobs":[
                {"id":"one","title":"Data Engineer","jobUrl":"/acme/one","location":{"name":"Remote"}}
              ]}};
            </script>
        """
        result = self.adapter.list_jobs(
            StubFetcher(["not-json", html]),
            self.adapter.identify_board("https://jobs.ashbyhq.com/acme"),
            JobQuery(),
        )

        self.assertEqual([candidate.url for candidate in result.candidates], [
            "https://jobs.ashbyhq.com/acme/one",
        ])
        self.assertEqual(result.trace["fallback_reason"], "invalid_api_json")

    def test_fallback_rejects_cross_board_cross_host_and_non_detail_urls(self):
        payload = {
            "jobs": [
                {"title": "Good", "jobUrl": "/acme/good"},
                {"title": "Other board", "jobUrl": "https://jobs.ashbyhq.com/other/bad"},
                {"title": "Wrong tenant case", "jobUrl": "https://jobs.ashbyhq.com/ACME/bad"},
                {"title": "Other host", "jobUrl": "https://evil.example/acme/bad"},
                {"title": "Board page", "jobUrl": "https://jobs.ashbyhq.com/acme"},
                {"title": "Deep path", "jobUrl": "https://jobs.ashbyhq.com/acme/job/apply"},
                {"title": "Bad port", "jobUrl": "https://jobs.ashbyhq.com:444/acme/bad"},
                {"title": "HTTP", "jobUrl": "http://jobs.ashbyhq.com/acme/http"},
                {"title": "Credentials", "jobUrl": "https://user@jobs.ashbyhq.com/acme/creds"},
                {"title": "Fragment", "jobUrl": "https://jobs.ashbyhq.com/acme/fragment#apply"},
                {"title": "Unsafe query", "jobUrl": "https://jobs.ashbyhq.com/acme/query?token=secret"},
                {"title": "Encoded slash", "jobUrl": "https://jobs.ashbyhq.com/acme%2Fother/job"},
                {"title": "Cross tenant API", "jobUrl": "https://api.ashbyhq.com/posting-api/job-board/other"},
                {"title": "Duplicate", "jobUrl": "/acme/good"},
            ]
        }
        html = f'<script type="application/json">{json.dumps(payload)}</script>'
        result = self.adapter.list_jobs(
            StubFetcher([{"jobs": []}, html]),
            self.adapter.identify_board("https://jobs.ashbyhq.com/acme"),
            JobQuery(),
        )

        self.assertEqual([candidate.title for candidate in result.candidates], ["Good"])
        self.assertEqual(result.trace["fallback_reason"], "empty_api_response")

    def test_missing_identifier_returns_structured_failure(self):
        board = JobBoard(url="https://jobs.ashbyhq.com", provider="ashby")

        result = self.adapter.list_jobs(StubFetcher({"jobs": []}), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_invalid_api_and_html_payload_returns_structured_parser_failure(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        result = self.adapter.list_jobs(StubFetcher(["not-json", "<html>no state</html>"]), board, JobQuery())

        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["fallback_reason"], "invalid_api_json")

    def test_empty_api_and_embedded_jobs_returns_empty_provider_response(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")
        html = '<script type="application/json">{"props":{"jobs":[]}}</script>'

        result = self.adapter.list_jobs(StubFetcher([{"jobs": []}, html]), board, JobQuery())

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["candidate_count"], 0)

    def test_api_failure_and_empty_embedded_container_remains_incomplete(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")
        html = '<script type="application/json">{"props":{"jobs":[]}}</script>'

        result = self.adapter.list_jobs(
            StubFetcher([FetchError("api timeout"), html]),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_api_and_fallback_fetch_failures_are_retryable(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        result = self.adapter.list_jobs(
            StubFetcher([FetchError("api timeout"), FetchError("board timeout")]),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["fallback_reason"], "api_fetch_failed")


if __name__ == "__main__":
    unittest.main()
