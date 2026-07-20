"""Contract tests for first-party literal JSON POST job inventories.

Fixtures are deliberately company-neutral.  They model a bounded public listing page
whose only job transport is declared by a same-origin static asset.
"""

import json
import unittest
from pathlib import Path

from job_source_agent.js_declared_inventory import (
    discover_js_declared_inventory,
    inspect_js_declared_inventory_transport,
)
from job_source_agent.listing_extraction import validate_output_url
from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.web import FetchError, Page


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "js_declared_json_post"
LISTING_URL = "https://careers.example.test/openings"
ASSET_URL = "https://careers.example.test/assets/career-inventory.js?build123"
ENDPOINT_URL = "https://careers.example.test/api/jobs/JobListing"


def fixture(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, values):
        self.values = values
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.values.get(url)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FetchError(f"missing fixture: {url}")
        return value


def listing(*, asset_url: str = ASSET_URL, html: str | None = None) -> Page:
    return Page(
        LISTING_URL,
        (html if html is not None else fixture("listing.html")).replace(
            "/assets/career-inventory.js", asset_url
        ),
        final_url=LISTING_URL,
    )


def asset(source: str | None = None, *, final_url: str | None = None) -> Page:
    return Page(
        ASSET_URL,
        source if source is not None else fixture("career-inventory.js"),
        final_url=final_url if final_url is not None else ASSET_URL + "=",
    )


def response(payload=None, *, final_url: str = ENDPOINT_URL) -> Page:
    if payload is None:
        payload = fixture("response.json")
    elif not isinstance(payload, str):
        payload = json.dumps(payload)
    return Page(ENDPOINT_URL, payload, final_url=final_url)


class JSDeclaredJSONPostInventoryContractTests(unittest.TestCase):
    def test_executes_single_same_origin_literal_json_post_complete_inventory(self):
        fetcher = RecordingFetcher({
            ASSET_URL: asset(),
            ENDPOINT_URL: response(),
        })

        result = discover_js_declared_inventory(fetcher, listing(), "Platform Engineer")

        self.assertEqual(result.trace.status, "verified")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace.endpoint_url, ENDPOINT_URL)
        self.assertEqual(
            [(item.title, item.location, item.url) for item in result.candidates],
            [
                (
                    "Platform Engineer",
                    "North Harbor, NA",
                    "https://careers.example.test/Jobdetails?reqNumber=REQ-1042",
                ),
                (
                    "Data Analyst",
                    "Remote, NA",
                    "https://careers.example.test/Jobdetails?reqNumber=REQ-1043",
                ),
            ],
        )
        self.assertEqual(len(fetcher.requests), 2)
        request_url, body, headers = fetcher.requests[1]
        self.assertEqual(request_url, ENDPOINT_URL)
        self.assertEqual(
            json.loads(body.decode("utf-8")),
            {
                "Categories": [1],
                "HideFacets": False,
                "Locations": [1],
                "UseWorkDay": "True",
            },
        )
        self.assertEqual(headers, {
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        trace = inspect_js_declared_inventory_transport(
            RecordingFetcher({ASSET_URL: asset()}), listing()
        )
        self.assertEqual(trace.status, "declared")
        self.assertEqual(trace.endpoint_url, ENDPOINT_URL)

    def test_opening_matcher_uses_title_and_location_without_bypassing_identity(self):
        fetcher = RecordingFetcher({
            LISTING_URL: listing(),
            ASSET_URL: asset(),
            ENDPOINT_URL: response(),
        })

        match, trace = JobOpeningMatcher(fetcher).match(
            LISTING_URL,
            "Platform Engineer",
            "North Harbor, NA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(
            match.url,
            "https://careers.example.test/Jobdetails?reqNumber=REQ-1042",
        )
        self.assertEqual(match.location, "North Harbor, NA")
        self.assertEqual(trace["js_declared_inventory"][0]["status"], "verified")
        self.assertEqual(
            trace["provider_api"]["inventory"]["scope"],
            "full",
        )

    def test_declared_inventory_query_detail_gate_is_strict(self):
        opening = "https://careers.example.test/Jobdetails?reqNumber=REQ-1042"
        self.assertEqual(
            validate_output_url(
                opening,
                LISTING_URL,
                title="Platform Engineer",
                origin="verified_declared_inventory",
            ),
            opening,
        )

        rejected = (
            "https://unrelated.example.test/Jobdetails?reqNumber=REQ-1042",
            "http://careers.example.test/Jobdetails?reqNumber=REQ-1042",
            "https://careers.example.test/other?reqNumber=REQ-1042",
            "https://careers.example.test/Jobdetails?reqNumber=REQ-1042&next=evil",
            "https://careers.example.test/Jobdetails?reqNumber=../../secret",
            "https://careers.example.test/Jobdetails?reqNumber=REQ-1042#fragment",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(
                    validate_output_url(
                        url,
                        LISTING_URL,
                        title="Platform Engineer",
                        origin="verified_declared_inventory",
                    )
                )

    def test_rejects_unsafe_or_ambiguous_declared_json_post_transports(self):
        cases = {
            "cross_origin": fixture("career-inventory.js").replace(
                "'/api/jobs/JobListing'", "'https://unrelated.example.test/api/jobs/JobListing'"
            ),
            "multiple_endpoint": fixture("career-inventory.js") + "\n" + fixture("career-inventory.js").replace(
                "'/api/jobs/JobListing'", "'/api/jobs/other'"
            ),
            "secret_bearing_header": fixture("career-inventory.js").replace(
                "'Accept': 'application/json'",
                "'Accept': 'application/json', 'Authorization': 'Bearer secret'",
            ),
            "secret_bearing_body": fixture("career-inventory.js").replace(
                "UseWorkDay: useWorkDay",
                "UseWorkDay: useWorkDay, apiKey: 'secret'",
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(source)})
                result = discover_js_declared_inventory(fetcher, listing(), "Platform Engineer")
                self.assertEqual(result.candidates, ())
                self.assertNotEqual(result.trace.status, "verified")
                self.assertEqual(len(fetcher.requests), 1)

    def test_rejects_redirecting_post_and_invalid_inventory_payloads(self):
        cases = {
            "redirecting_post": response(final_url=ENDPOINT_URL + "?redirect=1"),
            "malformed_total": response({
                "Jobs": [{"Reqnumber": "REQ-1042", "JobTitle": "Platform Engineer"}],
                "Categories": [], "Locations": [], "TotalJobCount": "one",
            }),
            "duplicate_ids": response({
                "Jobs": [
                    {"Reqnumber": "REQ-1042", "JobTitle": "Platform Engineer"},
                    {"Reqnumber": "REQ-1042", "JobTitle": "Data Analyst"},
                ],
                "Categories": [], "Locations": [], "TotalJobCount": 2,
            }),
            "missing_facets": response({
                "Jobs": [{"Reqnumber": "REQ-1042", "JobTitle": "Platform Engineer"}],
                "TotalJobCount": 1,
            }),
        }
        for name, endpoint_response in cases.items():
            with self.subTest(name=name):
                fetcher = RecordingFetcher({ASSET_URL: asset(), ENDPOINT_URL: endpoint_response})
                result = discover_js_declared_inventory(fetcher, listing(), "Platform Engineer")
                self.assertEqual(result.candidates, ())
                self.assertFalse(result.inventory_complete)
                self.assertIn(
                    result.trace.status,
                    {"transport_redirect_rejected", "invalid_job_postings_payload"},
                )

    def test_asset_query_normalization_is_bounded(self):
        accepted = inspect_js_declared_inventory_transport(
            RecordingFetcher({ASSET_URL: asset()}),
            listing(),
        )
        self.assertEqual(accepted.status, "declared")

        for final_url in (
            "https://careers.example.test/assets/career-inventory.js?build124=",
            "https://careers.example.test/assets/other.js?build123=",
            "https://cdn.example.test/assets/career-inventory.js?build123=",
        ):
            with self.subTest(final_url=final_url):
                trace = inspect_js_declared_inventory_transport(
                    RecordingFetcher({ASSET_URL: asset(final_url=final_url)}),
                    listing(),
                )
                self.assertEqual(trace.status, "asset_redirect_rejected")

    def test_rejects_unsafe_or_cross_origin_detail_template(self):
        pages = (
            fixture("listing.html").replace(
                "'/Jobdetails'", "'https://unrelated.example.test/Jobdetails'"
            ),
            fixture("listing.html").replace(
                "'/Jobdetails'", "'http://careers.example.test/Jobdetails'"
            ),
        )
        for page_html in pages:
            with self.subTest(page_html=page_html):
                fetcher = RecordingFetcher({ASSET_URL: asset()})
                result = discover_js_declared_inventory(
                    fetcher,
                    listing(html=page_html),
                    "Platform Engineer",
                )
                self.assertEqual(result.candidates, ())
                self.assertNotEqual(result.trace.status, "verified")
                self.assertEqual(len(fetcher.requests), 1)


if __name__ == "__main__":
    unittest.main()
