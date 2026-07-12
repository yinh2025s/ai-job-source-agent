import json
import unittest

from job_source_agent.providers.bamboohr import ADAPTER, BambooHRAdapter
from job_source_agent.providers.base import JobQuery
from job_source_agent.web import Page


class RecordingFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def fetch(self, url, data=None, headers=None):
        self.urls.append(url)
        return Page(url=url, html=json.dumps(self.payload), source="fixture")


class BambooHRAdapterTests(unittest.TestCase):
    def test_recognizes_tenant_careers_urls_only(self):
        adapter = BambooHRAdapter()

        self.assertTrue(adapter.recognizes("https://acme.bamboohr.com/careers"))
        self.assertTrue(adapter.recognizes("https://acme.bamboohr.com/careers/270"))
        self.assertFalse(adapter.recognizes("https://acme.bamboohr.com/about"))
        self.assertFalse(adapter.recognizes("https://bamboohr.com/careers"))
        self.assertFalse(adapter.recognizes("https://example.com/careers"))

    def test_identifies_canonical_tenant_board(self):
        board = ADAPTER.identify_board("https://Acme.bamboohr.com/careers/270?source=test")

        self.assertEqual(board.provider, "bamboohr")
        self.assertEqual(board.identifier, "acme")
        self.assertEqual(board.url, "https://Acme.bamboohr.com/careers")

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


if __name__ == "__main__":
    unittest.main()
