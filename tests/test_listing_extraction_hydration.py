import json
import unittest
from unittest.mock import patch

from job_source_agent.listing_extraction import (
    MAX_JSON_DEPTH,
    extract_detail_page_candidates,
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
        self.assertEqual(candidates[0].location, "San Francisco, CA, USA")

    def test_preserves_icon_labeled_location_from_semantic_uuid_card(self):
        html = """
            <a class="job-card" href="/jobs/123e4567-e89b-42d3-a456-426614174000">
              <h3>Junior Data Analyst</h3>
              <p><svg class="lucide lucide-building-2"></svg>Example Company</p>
              <span><svg class="lucide lucide-map-pin"></svg>Tampa, FL</span>
            </a>
        """

        candidates = extract_listing_candidates(html, SOURCE_URL)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Junior Data Analyst")
        self.assertEqual(candidates[0].location, "Tampa, FL")
        self.assertEqual(candidates[0].as_raw_link().location, "Tampa, FL")

    def test_extracts_page_bound_detail_with_nested_locations(self):
        detail_url = "https://careers.example.com/job-search/abc123def456"
        payload = {
            "props": {
                "job": {
                    "wdId": "abc123def456",
                    "title": "National Account Manager - Hotels",
                    "locations": [
                        {"city": "Chicago, IL", "country": "United States"},
                        {"city": "New York, NY", "country": "United States"},
                    ],
                }
            }
        }
        html = _flight_script("5:" + json.dumps(payload, separators=(",", ":")) + "\n")

        candidates = extract_detail_page_candidates(html, detail_url)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "National Account Manager - Hotels")
        self.assertEqual(candidates[0].url, detail_url)
        self.assertEqual(candidates[0].location, "Chicago, IL; New York, NY")

    def test_page_bound_detail_rejects_cross_identity_record(self):
        detail_url = "https://careers.example.com/job-search/abc123def456"
        payload = {
            "props": {
                "job": {
                    "wdId": "foreign987654",
                    "title": "National Account Manager - Hotels",
                    "locations": [{"city": "New York, NY"}],
                }
            }
        }
        html = _flight_script("5:" + json.dumps(payload, separators=(",", ":")) + "\n")

        self.assertEqual(extract_detail_page_candidates(html, detail_url), [])

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
