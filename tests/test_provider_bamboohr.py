import json
import unittest

from job_source_agent.providers.bamboohr import ADAPTER, BambooHRAdapter
from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    def __init__(self, payload, *, final_url=None, error=None):
        self.payload = payload
        self.final_url = final_url
        self.error = error
        self.urls = []

    def fetch(self, url, data=None, headers=None):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=json.dumps(self.payload),
            source="fixture",
        )


class BambooHRAdapterTests(unittest.TestCase):
    def test_recognizes_tenant_careers_urls_only(self):
        adapter = BambooHRAdapter()

        self.assertTrue(adapter.recognizes("https://acme.bamboohr.com/careers"))
        self.assertTrue(adapter.recognizes("https://acme.bamboohr.com/careers/270"))
        self.assertFalse(adapter.recognizes("https://acme.bamboohr.com/about"))
        self.assertFalse(adapter.recognizes("https://bamboohr.com/careers"))
        self.assertFalse(adapter.recognizes("https://example.com/careers"))

    def test_rejects_unsafe_and_multi_tenant_urls(self):
        adapter = BambooHRAdapter()

        rejected = [
            "ftp://acme.bamboohr.com/careers",
            "https://acme.bamboohr.com:8443/careers",
            "http://acme.bamboohr.com:443/careers",
            "https://user@acme.bamboohr.com/careers",
            "https://team.acme.bamboohr.com/careers",
            "https://acme_bamboohr.com/careers",
            "https://[broken/careers",
        ]
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(adapter.recognizes(url))

        self.assertTrue(adapter.recognizes("http://acme.bamboohr.com:80/careers"))
        self.assertTrue(adapter.recognizes("https://acme.bamboohr.com:443/careers"))

    def test_identifies_canonical_tenant_board(self):
        board = ADAPTER.identify_board("https://Acme.bamboohr.com/careers/270?source=test")

        self.assertEqual(board.provider, "bamboohr")
        self.assertEqual(board.identifier, "acme")
        self.assertEqual(board.url, "https://acme.bamboohr.com/careers")

    def test_http_input_is_canonicalized_to_https(self):
        board = ADAPTER.identify_board("http://ACME.bamboohr.com:80/careers/270")

        self.assertEqual(board.url, "https://acme.bamboohr.com/careers")

    def test_lists_normalized_candidates_from_public_endpoint(self):
        fetcher = RecordingFetcher(
            {
                "result": [
                    {
                        "id": 270,
                        "jobOpeningName": "Data Analyst",
                        "location": {
                            "city": "Austin",
                            "state": "Texas",
                            "country": "United States",
                        },
                        "departmentLabel": "Analytics",
                    },
                    {
                        "id": "271",
                        "jobOpeningName": "Platform Engineer",
                        "location": "Remote",
                    },
                    {"id": 272, "jobOpeningName": ""},
                ]
            }
        )
        board = ADAPTER.identify_board("https://acme.bamboohr.com/careers")

        result = ADAPTER.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        self.assertEqual(fetcher.urls, ["https://acme.bamboohr.com/careers/list"])
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(result.candidates[0].url, "https://acme.bamboohr.com/careers/270")
        self.assertEqual(result.candidates[0].location, "Austin, Texas, United States")
        self.assertEqual(result.candidates[0].raw["departmentLabel"], "Analytics")
        self.assertEqual(result.candidates[1].location, "Remote")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_reports_empty_and_invalid_provider_responses(self):
        board = ADAPTER.identify_board("https://acme.bamboohr.com/careers")

        empty = ADAPTER.list_jobs(RecordingFetcher({"result": []}), board, JobQuery())
        invalid = ADAPTER.list_jobs(RecordingFetcher({"result": {}}), board, JobQuery())

        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")

    def test_fetch_failure_is_retryable(self):
        board = ADAPTER.identify_board("https://acme.bamboohr.com/careers")

        for error in (FetchError("offline"), OSError("socket"), TimeoutError("slow")):
            with self.subTest(error=type(error).__name__):
                result = ADAPTER.list_jobs(
                    RecordingFetcher({}, error=error), board, JobQuery()
                )
                self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
                self.assertTrue(result.retryable)
                self.assertEqual(result.candidates, [])

    def test_rejects_cross_tenant_api_redirect(self):
        board = ADAPTER.identify_board("https://acme.bamboohr.com/careers")
        fetcher = RecordingFetcher(
            {"result": [{"id": 270, "jobOpeningName": "Data Analyst"}]},
            final_url="https://other.bamboohr.com/careers/list",
        )

        result = ADAPTER.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertEqual(
            result.trace["rejected_final_url"],
            "https://other.bamboohr.com/careers/list",
        )

    def test_rejects_mismatched_board_before_fetch(self):
        fetcher = RecordingFetcher({"result": []})
        board = JobBoard(
            url="https://other.bamboohr.com/careers",
            provider="bamboohr",
            identifier="acme",
        )

        result = ADAPTER.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(fetcher.urls, [])

    def test_normalizes_deduplicates_and_rejects_unsafe_candidates(self):
        board = ADAPTER.identify_board("https://acme.bamboohr.com/careers")
        fetcher = RecordingFetcher(
            {
                "result": [
                    {"id": 270, "jobOpeningName": "Data Analyst"},
                    {"id": "270", "jobOpeningName": "Duplicate"},
                    {
                        "id": "271",
                        "jobOpeningName": "Relative",
                        "jobUrl": "/careers/271?source=feed#apply",
                    },
                    {
                        "id": "272",
                        "jobOpeningName": "Cross tenant",
                        "url": "https://other.bamboohr.com/careers/272",
                    },
                    {
                        "id": "273",
                        "jobOpeningName": "Credentials",
                        "url": "https://user@acme.bamboohr.com/careers/273",
                    },
                    {
                        "id": "274",
                        "jobOpeningName": "Wrong ID",
                        "url": "/careers/999",
                    },
                    {"id": "../275", "jobOpeningName": "Traversal"},
                    {"id": True, "jobOpeningName": "Boolean"},
                    {"id": 0, "jobOpeningName": "Zero"},
                ]
            }
        )

        result = ADAPTER.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(
            [(candidate.title, candidate.url) for candidate in result.candidates],
            [
                ("Data Analyst", "https://acme.bamboohr.com/careers/270"),
                ("Relative", "https://acme.bamboohr.com/careers/271"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
