import json
from pathlib import Path
import unittest
from urllib.parse import urlencode

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.healthcaresource import (
    ADAPTER,
    HealthcareSourceAdapter,
)
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "healthcaresource"
TENANT = "redlandshospital"
OTHER_TENANT = "other-hospital"
RELEASE = "f0f02cc6"
BOARD_URL = f"https://{TENANT}.hcshiring.com/jobs"
TARGET_TITLE = "New Graduate Registered Nurse (RN)"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def inventory(records, *, page=1, per_page=10, total_jobs=None, total_pages=None):
    total = len(records) if total_jobs is None else total_jobs
    pages = (total + per_page - 1) // per_page if total_pages is None else total_pages
    return json.dumps(
        {
            "jobs": records,
            "meta": {
                "page": page,
                "perPage": per_page,
                "totalJobs": total,
                "totalPages": pages,
            },
        }
    )


def job(job_id="a1RpXN2MPEOWZT1PXB5NrA", title="Opening", **overrides):
    record = {
        "id": job_id,
        "title": title,
        "isInternalOnly": False,
        "hasOpening": True,
        "organization": "Example Health - Nursing",
        "city": "Redlands",
        "state": "CA",
        "zip": "92373",
    }
    record.update(overrides)
    return record


def api_url(page=1, *, tenant=TENANT, release=RELEASE, title=None):
    params = {"page": page}
    if title:
        params["job"] = title
    return (
        f"https://{tenant}.hcshiring.com/{release}/api/jobs?"
        f"{urlencode(params)}"
    )


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        return Page(url=url, final_url=url, html=response, source="hcs-fixture")


def board_page(html=None, *, final_url=BOARD_URL):
    return Page(
        url=BOARD_URL,
        final_url=final_url,
        html=fixture("redlands_board.html") if html is None else html,
        source="redlands-board-fixture",
    )


def inventory_page(raw, *, page=1, title=None, final_url=None):
    url = api_url(page, title=title)
    return Page(
        url=url,
        final_url=final_url or url,
        html=raw,
        source="redlands-inventory-fixture",
    )


class HealthcareSourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = HealthcareSourceAdapter()
        self.board = JobBoard(
            BOARD_URL,
            "healthcaresource",
            TENANT,
            replay_safe=True,
        )

    def test_is_typed_and_canonicalizes_board_and_detail_routes(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        accepted = (
            f"https://{TENANT}.hcshiring.com",
            BOARD_URL,
            f"https://{TENANT}.hcshiring.com/jobs/"
            "a1RpXN2MPEOWZT1PXB5NrA",
            f"https://{TENANT}.hcshiring.com:443/jobs",
            "https://generic-health.hcshiring.com/jobs",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                board = self.adapter.identify_board(url)
                self.assertEqual(
                    board.url,
                    f"https://{board.identifier}.hcshiring.com/jobs",
                )
                self.assertTrue(board.replay_safe)
        self.assertEqual(self.adapter.identify_board(accepted[0]), self.board)

    def test_rejects_unsafe_ambiguous_and_non_public_routes(self):
        rejected = (
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("hcshiring.com", "hcshiring.com.evil.test"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace("/jobs", ":8443/jobs"),
            "https://hcshiring.com/jobs",
            "https://bad_tenant.hcshiring.com/jobs",
            BOARD_URL + "?token=secret",
            BOARD_URL + "#openings",
            BOARD_URL + "/short-id",
            BOARD_URL + "/a1RpXN2MPEOWZT1PXB5NrA/extra",
            BOARD_URL.replace("/jobs", "//jobs"),
            f"https://{TENANT}.hcshiring.com/login",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_searches_and_paginates_frozen_redlands_inventory_to_exact_opening(self):
        fetcher = RecordingFetcher(
            [
                board_page(),
                inventory_page(
                    fixture("redlands_search_page_1.json"),
                    title=TARGET_TITLE,
                ),
                inventory_page(
                    fixture("redlands_search_page_2.json"),
                    page=2,
                    title=TARGET_TITLE,
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=f"  {TARGET_TITLE}  "),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(fetcher.requests[0], (BOARD_URL, None, None))
        self.assertEqual(fetcher.requests[1][0], api_url(1, title=TARGET_TITLE))
        self.assertEqual(fetcher.requests[2][0], api_url(2, title=TARGET_TITLE))
        self.assertEqual(fetcher.requests[1][2]["Referer"], BOARD_URL)
        candidate = result.candidates[2]
        self.assertEqual(candidate.title, TARGET_TITLE)
        self.assertEqual(candidate.location, "Redlands, CA, 92373")
        self.assertEqual(
            candidate.url,
            f"https://{TENANT}.hcshiring.com/jobs/Y8R-Q9nnf0q7WsU8w1pP6g",
        )
        self.assertEqual(
            candidate.raw,
            {
                "job_id": "Y8R-Q9nnf0q7WsU8w1pP6g",
                "organization": "Community Hospital - Nursing Education",
            },
        )
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(
            result.trace["identity"],
            {"tenant": TENANT, "release": RELEASE},
        )

    def test_lists_full_inventory_when_title_is_absent(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(fixture("redlands_inventory_page_1.json")),
                    inventory_page(
                        fixture("redlands_inventory_page_2.json"), page=2
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 3)
        self.assertFalse(result.trace["exact_title_found"])

    def test_accepts_verified_empty_inventory_as_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        inventory([]),
                        title="No such opening",
                    ),
                ]
            ),
            self.board,
            JobQuery(title="No such opening"),
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.candidates, [])

    def test_filtered_inventory_without_exact_title_is_not_claimed_exact(self):
        title = "Registered Nurse"
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        inventory([job(title="Registered Nurse - Emergency")]),
                        title=title,
                    ),
                ]
            ),
            self.board,
            JobQuery(title=title),
        )

        self.assertIsNone(result.reason_code)
        self.assertFalse(result.trace["exact_title_found"])

    def test_rejects_closed_or_internal_filtered_openings(self):
        for record in (
            job(title=TARGET_TITLE, hasOpening=False),
            job(title=TARGET_TITLE, isInternalOnly=True),
        ):
            with self.subTest(record=record):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        [
                            board_page(),
                            inventory_page(
                                inventory([record]),
                                title=TARGET_TITLE,
                            ),
                        ]
                    ),
                    self.board,
                    JobQuery(title=TARGET_TITLE),
                )

                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.inventory_scope, "title_filtered")

    def test_requires_generic_shell_fingerprint_and_consistent_release(self):
        valid = fixture("redlands_board.html")
        invalid_shells = (
            valid.replace("var V", "var X"),
            valid.replace("applicant-cli-", "unrelated-cli-"),
            valid.replace("cdn.healthcaresource.com", "cdn.example.test"),
            valid.replace('id="rootDiv"', 'id="application"'),
            valid + "<script>var V = { version: '12345678' };</script>",
            "x" * 2_000_001,
        )
        for shell in invalid_shells:
            with self.subTest(size=len(shell)):
                result = self.adapter.list_jobs(
                    RecordingFetcher([board_page(shell)]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(
                    result.reason_code,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                )
                self.assertFalse(result.inventory_complete)

    def test_rejects_cross_tenant_board_and_inventory_redirects(self):
        board_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(
                        final_url=f"https://{OTHER_TENANT}.hcshiring.com/jobs"
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        inventory_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        inventory([]),
                        final_url=api_url(tenant=OTHER_TENANT),
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )
        search_query_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        inventory([]),
                        title=TARGET_TITLE,
                        final_url=api_url(1, title="Different title"),
                    ),
                ]
            ),
            self.board,
            JobQuery(title=TARGET_TITLE),
        )
        tampered_board = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(BOARD_URL, "healthcaresource", OTHER_TENANT),
            JobQuery(),
        )
        for result in (
            board_redirect,
            inventory_redirect,
            search_query_redirect,
            tampered_board,
        ):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
        self.assertEqual(search_query_redirect.inventory_scope, "title_filtered")

    def test_rejects_malformed_metadata_drift_duplicates_and_invalid_jobs(self):
        invalid_payloads = (
            "not json",
            json.dumps({"jobs": [], "meta": {}, "extra": True}),
            inventory([], page=2),
            inventory([job()], per_page=0, total_pages=1),
            inventory([job()], per_page=10, total_jobs=11, total_pages=2),
            inventory([job()], total_jobs=1, total_pages=2),
            inventory([job()], total_jobs=1001, total_pages=101),
            inventory([job(id="wrong")]),
            inventory([job(isInternalOnly=True)]),
            inventory([job(hasOpening=False)]),
            inventory([job(title="")]),
            inventory([job(city={"unsafe": True})]),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload[:80]):
                result = self.adapter.list_jobs(
                    RecordingFetcher([board_page(), inventory_page(payload)]),
                    self.board,
                    JobQuery(),
                )
                self.assertIn(
                    result.reason_code,
                    {"INVALID_STRUCTURED_DATA", "OPENING_DISCOVERY_INCOMPLETE"},
                )
                self.assertFalse(result.inventory_complete)

        first = inventory([job()], total_jobs=2, total_pages=2, per_page=1)
        drift = inventory(
            [job("SNV6gJWMv0qBtDueN67MeA")],
            page=2,
            total_jobs=3,
            total_pages=3,
            per_page=1,
        )
        duplicate = inventory(
            [job()],
            page=2,
            total_jobs=2,
            total_pages=2,
            per_page=1,
        )
        for second in (drift, duplicate):
            result = self.adapter.list_jobs(
                RecordingFetcher(
                    [
                        board_page(),
                        inventory_page(first),
                        inventory_page(second, page=2),
                    ]
                ),
                self.board,
                JobQuery(),
            )
            self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
            self.assertFalse(result.inventory_complete)

    def test_projects_typed_fetch_failure(self):
        error = FetchError(
            "rate limited",
            status=429,
            reason_code="HTTP_RATE_LIMITED",
            retryable=True,
        )
        result = self.adapter.list_jobs(
            RecordingFetcher(error=error),
            self.board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "HTTP_RATE_LIMITED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.trace["status"], 429)


if __name__ == "__main__":
    unittest.main()
