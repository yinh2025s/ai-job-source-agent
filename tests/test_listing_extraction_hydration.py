import json
import unittest
from unittest.mock import patch

from job_source_agent.listing_extraction import (
    MAX_JSON_DEPTH,
    extract_listing_candidates,
)


SOURCE_URL = "https://unity.com/careers/positions"


def _record(index: int = 1, *, url: str | None = None) -> dict:
    return {
        "title": f"Staff Software Engineer {index}",
        "location": "San Francisco, CA, USA",
        "id": f"JOBREQ-{index}",
        "externalUrl": url
        or (
            "https://unitytech.wd1.myworkdayjobs.com/Unity/job/"
            f"San-Francisco-CA-USA/Staff-Software-Engineer_JOBREQ-{index}"
        ),
    }


def _job_state(*records: dict) -> dict:
    return {"customMappingData": {"departments": {"Engineering": list(records)}}}


def _flight_script(frame: str) -> str:
    argument = json.dumps([1, frame], separators=(",", ":"))
    return f"<script>self.__next_f.push({argument})</script>"


class ListingExtractionHydrationTests(unittest.TestCase):
    def test_extracts_unity_like_record_to_workday_url(self):
        record = _record(2515085)
        html = _flight_script("5:" + json.dumps(_job_state(record), separators=(",", ":")) + "\n")

        candidates = extract_listing_candidates(html, SOURCE_URL)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, record["title"])
        self.assertEqual(candidates[0].url, record["externalUrl"])
        self.assertEqual(candidates[0].origin, "structured_state")

    def test_reassembles_multiple_next_f_chunks(self):
        encoded_frame = "5:" + json.dumps(_job_state(_record(2)), separators=(",", ":")) + "\n"
        midpoint = len(encoded_frame) // 2
        html = _flight_script(encoded_frame[:midpoint]) + _flight_script(encoded_frame[midpoint:])

        candidates = extract_listing_candidates(html, SOURCE_URL)

        self.assertEqual([candidate.title for candidate in candidates], ["Staff Software Engineer 2"])

    def test_malformed_oversized_and_deep_payloads_fail_closed(self):
        malformed = '<script>self.__next_f.push([1,"5:{not-json}\\n"])</script>'
        with patch("job_source_agent.listing_extraction.MAX_SCRIPT_CHARS", 40):
            oversized = _flight_script("5:" + json.dumps(_job_state(_record(3))))
            self.assertEqual(extract_listing_candidates(oversized, SOURCE_URL), [])

        nested = _job_state(_record(4))
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = {"wrapper": nested}

        self.assertEqual(extract_listing_candidates(malformed, SOURCE_URL), [])
        self.assertEqual(
            extract_listing_candidates(_flight_script("5:" + json.dumps(nested)), SOURCE_URL),
            [],
        )

    def test_rejects_cross_site_non_ats_url(self):
        html = _flight_script(
            "5:" + json.dumps(_job_state(_record(5, url="https://example.net/jobs/JOBREQ-5")))
        )

        self.assertEqual(extract_listing_candidates(html, SOURCE_URL), [])

    def test_rejects_records_outside_job_containers(self):
        html = _flight_script("5:" + json.dumps({"navigation": _record(6)}))

        self.assertEqual(extract_listing_candidates(html, SOURCE_URL), [])

    def test_honors_candidate_cap(self):
        html = _flight_script("5:" + json.dumps(_job_state(*(_record(i) for i in range(10)))))

        with patch("job_source_agent.listing_extraction.MAX_CANDIDATES", 3):
            candidates = extract_listing_candidates(html, SOURCE_URL)

        self.assertEqual(len(candidates), 3)
        self.assertEqual([candidate.title for candidate in candidates], [
            "Staff Software Engineer 0",
            "Staff Software Engineer 1",
            "Staff Software Engineer 2",
        ])


if __name__ == "__main__":
    unittest.main()
