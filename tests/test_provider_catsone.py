import copy
import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.catsone import ADAPTER, CatsoneAdapter
from job_source_agent.job_board import DiscoveredJobBoard, JobBoardPortfolio
from job_source_agent.web import FetchError, Page


PORTAL_ID = "100910"
SITE_ID = 89996
API_URL = "https://app.catsone.com/portal?id=100910"
BOARD_URL = (
    "https://jobs.alaskacommercial.com/"
    "careers/100910-Alaska-Commercial-Company/"
)
DETAIL_URL = BOARD_URL + "jobs/16833769-Department-Manager-Produce/"


def widget_html(
    *,
    portal_id=100910,
    domain="catsone.com",
    target="#cats-portal-widget",
    script_url="https://app.catsone.com/resources/entry-jobwidget.js",
    extra_config=None,
):
    config = {"id": portal_id, "domain": domain, "target": target}
    config.update(extra_config or {})
    return (
        '<div id="cats-portal-widget"></div>'
        "<script>"
        "window.cjw=window.cjw||function(){"
        "(cjw.instance=cjw.instance||[]).push(arguments[0])};"
        f"cjw({json.dumps(config)});"
        "</script>"
        f'<script async src="{script_url}"></script>'
    )


def alaska_payload():
    return {
        "id": 100910,
        "name": "Alaska Commercial Company",
        "site_id": SITE_ID,
        "site_name": "The North West Company",
        "host": "jobs.alaskacommercial.com",
        "internalHost": "northwest.catsone.com",
        "job_listings_ids": [100910],
        "jobs": [
            {
                "id": 16833769,
                "url": DETAIL_URL,
                "site_id": SITE_ID,
                "title": "Department Manager, Produce - (Relocation and Housing)",
                "location": {"city": "Utqiagvik", "state": "AK"},
                "is_hidden": False,
            },
            {
                "id": 16340130,
                "url": BOARD_URL + "jobs/16340130-Invite-to-Apply-ACC-Positions/",
                "site_id": SITE_ID,
                "title": "Invite to Apply - ACC Positions",
                "location": {"city": ".", "state": "."},
                "is_hidden": True,
            },
        ],
    }


class RecordingFetcher:
    def __init__(self, payload=None, *, final_url=API_URL, error=None):
        self.payload = alaska_payload() if payload is None else payload
        self.final_url = final_url
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        raw = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return Page(
            url=url,
            final_url=self.final_url,
            html=raw,
            source="fresh100-alaska-cats-widget",
        )


class CatsoneAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CatsoneAdapter()
        self.page = Page(
            url="https://acc.careers/join-us/",
            html=widget_html(),
        )
        self.locator = self.adapter.identify_board_from_page(self.page)

    def test_is_typed_and_discovers_verified_generic_alaska_widget(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertEqual(
            self.locator,
            JobBoard(
                API_URL,
                "catsone",
                '{"domain":"catsone.com","portal_id":"100910"}',
                replay_safe=True,
            ),
        )
        self.assertTrue(self.adapter.recognizes(API_URL))
        self.assertEqual(self.adapter.identify_board(API_URL), self.locator)

        portfolio = JobBoardPortfolio(
            (
                DiscoveredJobBoard(
                    board=self.locator,
                    detection_method="page_evidence",
                    evidence_url=API_URL,
                ),
            ),
            eligible_set_complete=True,
        )
        self.assertIsNotNone(portfolio.to_checkpoint_payload())

    def test_widget_identity_requires_active_official_config_and_target(self):
        invalid = (
            widget_html(script_url="https://evil.example/entry-jobwidget.js"),
            widget_html(script_url="http://app.catsone.com/resources/entry-jobwidget.js"),
            widget_html(target="#missing"),
            widget_html(domain="catsone.com.evil.example"),
            widget_html(portal_id="Alaska Commercial Company"),
            widget_html(extra_config={"token": "secret"}),
            f"<!-- {widget_html()} -->",
            '<div id="cats-portal-widget"></div><script>cjw({bad json})</script>',
            widget_html() + widget_html(portal_id=100911),
        )
        for html in invalid:
            with self.subTest(html=html[-120:]):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page("https://acc.careers/join-us/", html)
                    )
                )

        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page("http://acc.careers/join-us/", widget_html())
            )
        )
        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page("https://127.0.0.1/join-us/", widget_html())
            )
        )

    def test_canonicalizes_provider_owned_board_and_detail_routes(self):
        hosted_board = self.adapter.identify_board(
            "https://northwest.catsone.com/careers/100910-Alaska-Commercial-Company/"
        )
        hosted_detail = self.adapter.identify_board(
            "https://northwest.catsone.com/careers/100910-Alaska-Commercial-Company/"
            "jobs/16833769-Department-Manager-Produce/?source=widget"
        )
        self.assertEqual(hosted_detail, hosted_board)
        self.assertEqual(
            hosted_board.url,
            "https://northwest.catsone.com/careers/100910-Alaska-Commercial-Company/",
        )

    def test_rejects_fake_unsafe_and_auth_bearing_urls(self):
        rejected = (
            "http://app.catsone.com/portal?id=100910",
            "https://user@app.catsone.com/portal?id=100910",
            "https://app.catsone.com:8443/portal?id=100910",
            "https://app.catsone.com.evil.example/portal?id=100910",
            "https://app.catsone.com/portal?id=100910&token=secret",
            "https://app.catsone.com/login?id=100910",
            "https://www.catsone.com/careers/100910-Alaska/",
            "https://jobs.example.com/careers/100910-Alaska/",
            "https://northwest.catsone.com/careers/100910-Alaska/jobs/other-role/",
            "https://northwest.catsone.com/careers/100910-Alaska/#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_visible_alaska_jobs_anonymously_and_canonicalizes_board(self):
        fetcher = RecordingFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.locator,
            JobQuery(
                title="Department Manager Produce Relocation and Housing",
                location="Utqiagvik, AK",
            ),
        )

        self.assertEqual(fetcher.requests, [(API_URL, None, {"Accept": "application/json"})])
        self.assertNotIn("Authorization", fetcher.requests[0][2])
        self.assertNotIn("Cookie", fetcher.requests[0][2])
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(result.board.url, BOARD_URL)
        self.assertTrue(result.board.replay_safe)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, DETAIL_URL)
        self.assertEqual(result.candidates[0].location, "Utqiagvik, AK")
        self.assertEqual(result.candidates[0].raw["portal_id"], PORTAL_ID)
        self.assertEqual(result.trace["records_seen"], 2)
        self.assertEqual(result.trace["hidden_records"], 1)
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["request_count"], 1)
        self.assertEqual(result.trace["page_count"], 1)

    def test_response_identity_is_bound_to_config_and_verified_account(self):
        mutations = []
        for key, value in (
            ("id", 100911),
            ("site_id", False),
            ("host", "jobs.alaskacommercial.com.evil.example"),
            ("host", "127.0.0.1"),
            ("internalHost", "northwest.catsone.com.evil.example"),
            ("internalHost", "app.catsone.com"),
            ("job_listings_ids", [100911]),
        ):
            payload = alaska_payload()
            payload[key] = value
            mutations.append(payload)
        for payload in mutations:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(
                    RecordingFetcher(payload), self.locator, JobQuery()
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_rejects_cross_host_tenant_site_and_board_details(self):
        payloads = []
        for mutation in ("host", "portal", "site", "board"):
            payload = alaska_payload()
            record = payload["jobs"][0]
            if mutation == "host":
                record["url"] = record["url"].replace(
                    "jobs.alaskacommercial.com", "evil.example"
                )
            elif mutation == "portal":
                record["url"] = record["url"].replace("100910-", "100911-")
            elif mutation == "site":
                record["site_id"] = SITE_ID + 1
            else:
                record["url"] = record["url"].replace(
                    "100910-Alaska-Commercial-Company",
                    "100910-Other-Account",
                )
            payloads.append(payload)
        for payload in payloads:
            result = self.adapter.list_jobs(
                RecordingFetcher(payload), self.locator, JobQuery()
            )
            self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_rejects_redirects_to_auth_cross_host_and_other_portals(self):
        final_urls = (
            "https://app.catsone.com/login?id=100910",
            "https://app.catsone.com/portal?id=100911",
            "https://evil.example/portal?id=100910",
            "https://app.catsone.com/portal?id=100910&token=secret",
        )
        for final_url in final_urls:
            result = self.adapter.list_jobs(
                RecordingFetcher(final_url=final_url), self.locator, JobQuery()
            )
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)

    def test_rejects_malformed_duplicate_and_oversized_inventory(self):
        malformed = []
        for key, value in (
            ("id", True),
            ("title", ""),
            ("is_hidden", "false"),
            ("location", []),
            ("url", DETAIL_URL + "?token=secret"),
        ):
            payload = alaska_payload()
            payload["jobs"][0][key] = value
            malformed.append(payload)
        duplicate = alaska_payload()
        duplicate["jobs"].append(copy.deepcopy(duplicate["jobs"][0]))
        malformed.extend((duplicate, "not-json", json.dumps([])))

        for payload in malformed:
            result = self.adapter.list_jobs(
                RecordingFetcher(payload), self.locator, JobQuery()
            )
            self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

        oversized = "{}" + (" " * 8_000_000)
        capped = alaska_payload()
        capped["jobs"] = [copy.deepcopy(capped["jobs"][0]) for _ in range(2001)]
        for payload, stop_reason in (
            (oversized, "response_cap_exceeded"),
            (capped, "record_cap_exceeded"),
        ):
            result = self.adapter.list_jobs(
                RecordingFetcher(payload), self.locator, JobQuery()
            )
            self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
            self.assertTrue(result.retryable)
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.trace["stop_reason"], stop_reason)

    def test_incomplete_or_inconsistent_pagination_fails_closed(self):
        pagination_values = (
            {"has_more": True},
            {"next_page": 2},
            {"total": 3},
            {"pagination": {"page": 1, "pages": 2, "next_page": 2}},
            {"pagination": {"page": 1, "pages": 1, "total": 99}},
        )
        for extra in pagination_values:
            payload = alaska_payload()
            payload.update(extra)
            result = self.adapter.list_jobs(
                RecordingFetcher(payload), self.locator, JobQuery()
            )
            self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
            self.assertEqual(result.trace["stop_reason"], "incomplete_pagination")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_verified_empty_inventory_is_complete(self):
        payload = alaska_payload()
        payload["jobs"] = []
        result = self.adapter.list_jobs(
            RecordingFetcher(payload), self.locator, JobQuery(title="Missing role")
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(
            result.board.url,
            "https://jobs.alaskacommercial.com/careers/100910/",
        )

    def test_tampered_public_board_is_reverified_against_response_host(self):
        identifier = json.dumps(
            {
                "domain": "catsone.com",
                "portal_id": 100910,
                "public_host": "jobs.fake-example.com",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        board = JobBoard(
            "https://jobs.fake-example.com/careers/100910-Fake/",
            "catsone",
            identifier,
            replay_safe=True,
        )
        result = self.adapter.list_jobs(RecordingFetcher(), board, JobQuery())
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(result.inventory_complete)

    def test_fetch_failures_preserve_auth_and_transport_classification(self):
        unauthorized = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 401: Unauthorized")),
            self.locator,
            JobQuery(),
        )
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.locator,
            JobQuery(title="private title"),
        )
        self.assertEqual(unauthorized.reason_code, "LOGIN_REQUIRED")
        self.assertFalse(unauthorized.inventory_complete)
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertNotIn("private", json.dumps(timeout.trace))


if __name__ == "__main__":
    unittest.main()
