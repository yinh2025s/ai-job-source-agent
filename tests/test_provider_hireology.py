import json
import unittest

from job_source_agent.providers.base import (
    JobQuery,
    PageAwareProviderAdapter,
    ProviderAdapter,
)
from job_source_agent.providers.hireology import ADAPTER, HireologyAdapter
from job_source_agent.web import FetchError, Page


ROOT = "mag"
BOARD_URL = "https://careers.hireology.com/mag"
API_URL = (
    "https://api.hireology.com/v2/public/careers/mag?page_size=100&page=1"
)
V1_URL = "https://api.hireology.com/v1/careers/mag"
CHILD = "classictoyotaofhenderson"
OPENING_ID = 2699065
DETAIL_URL = (
    "https://careers.hireology.com/"
    f"{CHILD}/{OPENING_ID}/description"
)
ROOT_DETAIL_URL = f"https://careers.hireology.com/{ROOT}/{OPENING_ID}/description"
DETAIL_API_URL = (
    f"https://api.hireology.com/v2/public/careers/jobs/{OPENING_ID}"
)


def job(
    *,
    opening_id=OPENING_ID,
    title="Automotive Sales Consultant",
    tenant=CHILD,
    status="Open",
    organization="Classic Toyota of Henderson",
    locations=None,
    remote=False,
):
    locations = (
        [{"city": "Henderson", "state": "NC", "zip_code": "27536"}]
        if locations is None
        else locations
    )
    detail_url = (
        f"https://careers.hireology.com/{tenant}/{opening_id}/description"
    )
    return {
        "id": opening_id,
        "name": title,
        "status": status,
        "locations": locations,
        "remote": remote,
        "career_site_url": detail_url,
        "career_site_path": f"/{tenant}/{opening_id}/description",
        "organization": (
            None
            if organization is None
            else {"id": 42, "name": organization, "type": "Location"}
        ),
    }


def payload(records, *, count=None, page=1, page_size=100):
    return json.dumps(
        {
            "data": records,
            "count": len(records) if count is None else count,
            "page": page,
            "page_size": page_size,
        }
    )


def v1_job(
    *,
    opening_id=OPENING_ID,
    internal_id=OPENING_ID - 1,
    title="Automotive Sales Consultant",
    tenant=CHILD,
    status="Open",
    locations=None,
    remote=False,
):
    locations = ["Henderson, NC"] if locations is None else locations
    return {
        "type": "careers",
        "id": str(internal_id),
        "attributes": {
            "id": internal_id,
            "name": title,
            "remote": remote,
            "job-description": "<p>Public description</p>",
            "locations": locations,
            "status": status,
            "career-site-url": (
                f"https://careers.hireology.com/{tenant}/"
                f"{opening_id}/description"
            ),
            "job-family": {"id": 9, "name": "General"},
            "employment-status": "Full Time",
        },
    }


def v1_payload(records):
    return json.dumps({"data": records})


class RoutingFetcher:
    def __init__(self, pages=None, *, error=None):
        self.pages = pages or {}
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        response = self.pages.get(url)
        if response is None:
            if url == V1_URL:
                raise FetchError(
                    "v1 fixture unavailable",
                    reason_code="HTTP_NOT_FOUND",
                    retryable=False,
                )
            raise AssertionError(f"unexpected URL: {url}")
        if isinstance(response, Page):
            return response
        return Page(url=url, html=response, source="hireology-fixture")


class HireologyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = HireologyAdapter()
        self.board = self.adapter.identify_board(BOARD_URL)
        self.assertIsNotNone(self.board)

    def test_is_typed_and_canonicalizes_safe_board_and_detail_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertIsInstance(ADAPTER, PageAwareProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)

        for url in (
            BOARD_URL,
            BOARD_URL + "/",
            DETAIL_URL,
            DETAIL_URL + "/",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))

        board = self.adapter.identify_board(BOARD_URL + "/")
        self.assertEqual(board.url, BOARD_URL)
        self.assertEqual(board.identifier, ROOT)
        self.assertTrue(board.replay_safe)

        detail_board = self.adapter.identify_board(DETAIL_URL)
        self.assertEqual(
            detail_board.url,
            f"https://careers.hireology.com/{CHILD}",
        )
        self.assertEqual(detail_board.identifier, CHILD)

    def test_rejects_credentials_ports_malformed_tenants_and_unsafe_routes(self):
        rejected = (
            "http://careers.hireology.com/mag",
            "https://user@careers.hireology.com/mag",
            "https://careers.hireology.com:8443/mag",
            "https://careers.hireology.com.evil.test/mag",
            "https://careers.hireology.com/MAG",
            "https://careers.hireology.com/-mag",
            "https://careers.hireology.com/mag-",
            "https://careers.hireology.com/mag/bad/description",
            "https://careers.hireology.com/mag/0/description",
            "https://careers.hireology.com/mag/123",
            "https://careers.hireology.com/mag/123/apply",
            "https://careers.hireology.com/mag/123/description/extra",
            "https://careers.hireology.com//mag",
            "https://careers.hireology.com/mag//123/description",
            "https://careers.hireology.com/%6dag",
            "https://careers.hireology.com/mag%2fother",
            "https://careers.hireology.com/mag?token=secret",
            "https://careers.hireology.com/mag#jobs",
            "https://tenant.hireology.careers/jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_custom_page_requires_unique_tenant_and_strong_owned_evidence(self):
        page = Page(
            "https://jobs.example.com/openings",
            (
                '<link rel="canonical" '
                f'href="https://careers.hireology.com/{ROOT}">'
                '<script src="https://app.hireology.com/assets/careers.js"></script>'
            ),
        )
        self.assertEqual(self.adapter.identify_board_from_page(page), self.board)

        api_bound = Page(
            "https://jobs.example.com/openings",
            (
                '<script src="https://assets.hireology.com/careers.js"></script>'
                "<script>window.endpoint="
                f'"{API_URL}"'
                "</script>"
            ),
        )
        self.assertEqual(self.adapter.identify_board_from_page(api_bound), self.board)

        v1_custom_page = Page(
            "https://jobs.example.com/openings",
            (
                "<script>fetch("
                '"https://api.hireology.com/v1/careers/millsautogroup"'
                ")</script>"
            ),
        )
        identified = self.adapter.identify_board_from_page(v1_custom_page)
        self.assertEqual(identified.identifier, "millsautogroup")
        self.assertEqual(
            identified.url,
            "https://careers.hireology.com/millsautogroup",
        )

    def test_custom_page_rejects_weak_conflicting_or_unsafe_evidence(self):
        pages = (
            Page(
                "https://tenant.hireology.careers/jobs",
                "<h1>Hireology jobs</h1>",
            ),
            Page(
                "https://jobs.example.com/openings",
                f'<link rel="canonical" href="{BOARD_URL}">',
            ),
            Page(
                "https://jobs.example.com/openings",
                '<script src="https://app.hireology.com.evil.test/widget.js"></script>'
                f'<link rel="canonical" href="{BOARD_URL}">',
            ),
            Page(
                "https://jobs.example.com/openings",
                '<script src="https://app.hireology.com/widget.js"></script>'
                f'<link rel="canonical" href="{BOARD_URL}">'
                '<script>const api="https://api.hireology.com/v2/public/'
                'careers/other?page_size=100&page=1"</script>',
            ),
            Page(
                "http://jobs.example.com/openings",
                '<script src="https://app.hireology.com/widget.js"></script>'
                f'<link rel="canonical" href="{BOARD_URL}">',
            ),
        )
        for page in pages:
            with self.subTest(html=page.html):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_lists_open_jobs_and_preserves_inventory_root_for_child_tenant(self):
        records = [
            job(),
            job(
                opening_id=2813570,
                title="Guest Experience Representative",
                tenant="sandiegopadres",
                organization="San Diego Padres",
                locations=[
                    {"city": "San Diego", "state": "CA"},
                    {"city": "Peoria", "state": "AZ"},
                ],
                remote=True,
            ),
        ]
        fetcher = RoutingFetcher({API_URL: payload(records)})

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 2)
        candidate = result.candidates[0]
        self.assertEqual(candidate.url, ROOT_DETAIL_URL)
        self.assertEqual(candidate.title, "Automotive Sales Consultant")
        self.assertEqual(candidate.location, "Henderson, NC")
        self.assertEqual(candidate.raw["status"], "Open")
        self.assertEqual(candidate.raw["inventory_root"], ROOT)
        self.assertEqual(candidate.raw["opening_tenant"], CHILD)
        self.assertEqual(
            candidate.raw["provider_returned_career_site_url"],
            DETAIL_URL,
        )
        self.assertEqual(
            candidate.raw["hiring_organization_name"],
            "Classic Toyota of Henderson",
        )
        self.assertEqual(
            result.candidates[1].location,
            "Remote; San Diego, CA; Peoria, AZ",
        )
        self.assertEqual(len(result.employer_evidence), 2)
        evidence = result.employer_evidence[0]
        self.assertEqual(evidence.employer_name, "Classic Toyota of Henderson")
        self.assertEqual(evidence.evidence_url, API_URL)
        self.assertEqual(evidence.opening_url, ROOT_DETAIL_URL)
        self.assertEqual(evidence.extraction_method, "hireology_organization")
        self.assertEqual(
            fetcher.requests,
            [
                (
                    V1_URL,
                    None,
                    {"Accept": "application/json", "Referer": BOARD_URL},
                ),
                (
                    API_URL,
                    None,
                    {"Accept": "application/json", "Referer": BOARD_URL},
                )
            ],
        )

    def test_v1_inventory_uses_targeted_v2_detail_for_employer_evidence(self):
        v1_records = [
            v1_job(
                title="General Sales Manager",
                locations=["Henderson, NC"],
            ),
            v1_job(
                opening_id=2442950,
                internal_id=2442949,
                title="General Sales Manager",
                tenant="classicfordlincoln-columbia",
                locations=["Columbia, SC"],
            ),
            v1_job(
                opening_id=2,
                internal_id=1,
                title="Service Advisor",
                tenant="other",
                locations=["Charlotte, NC"],
            ),
        ]
        other_detail_api = (
            "https://api.hireology.com/v2/public/careers/jobs/2442950"
        )
        fetcher = RoutingFetcher(
            {
                V1_URL: v1_payload(v1_records),
                DETAIL_API_URL: json.dumps({"data": job()}),
                other_detail_api: json.dumps(
                    {
                        "data": job(
                            opening_id=2442950,
                            title="General Sales Manager",
                            tenant="classicfordlincoln-columbia",
                            organization="Classic Ford Lincoln - Columbia",
                            locations=[{"city": "Columbia", "state": "SC"}],
                        )
                    }
                ),
            }
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(
                title="General Sales Manager",
                location="Henderson, NC",
            ),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(result.candidates[0].url, ROOT_DETAIL_URL)
        self.assertEqual(result.candidates[0].location, "Henderson, NC")
        self.assertEqual(len(result.employer_evidence), 2)
        self.assertEqual(
            result.employer_evidence[0].employer_name,
            "Classic Toyota of Henderson",
        )
        self.assertEqual(
            result.trace["variant"],
            "public_careers_api_v1_with_v2_detail",
        )
        self.assertEqual(
            result.trace["api_urls"],
            [V1_URL, DETAIL_API_URL, other_detail_api],
        )

    def test_pages_until_complete_and_retains_parent_child_continuity(self):
        page_1 = [
            job(
                opening_id=index,
                title=f"Role {index}",
                tenant=f"child-{index}",
                organization=f"Employer {index}",
            )
            for index in range(1, 101)
        ]
        page_2 = [
            job(
                opening_id=101,
                title="Final Role",
                tenant="last-child",
                organization="Last Employer",
            )
        ]
        api_2 = (
            "https://api.hireology.com/v2/public/careers/"
            "mag?page_size=100&page=2"
        )
        fetcher = RoutingFetcher(
            {
                API_URL: payload(page_1, count=101),
                api_2: payload(page_2, count=101, page=2),
            }
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 101)
        self.assertEqual(result.trace["stop_reason"], "complete")
        self.assertEqual(result.trace["api_urls"], [API_URL, api_2])

    def test_exact_title_does_not_short_circuit_declared_inventory(self):
        api_2 = (
            "https://api.hireology.com/v2/public/careers/"
            "mag?page_size=100&page=2"
        )
        fetcher = RoutingFetcher(
            {
                API_URL: payload(
                    [
                        job(
                            title="Parts Runner",
                            tenant="goschhyundai",
                            opening_id=2724843,
                        )
                    ],
                    count=2,
                ),
                api_2: payload(
                    [job(opening_id=2, title="Other")],
                    count=2,
                    page=2,
                ),
            }
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Parts Runner"),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["stop_reason"], "complete")
        self.assertEqual(len(fetcher.requests), 3)

    def test_does_not_short_circuit_on_fuzzy_title(self):
        api_2 = (
            "https://api.hireology.com/v2/public/careers/"
            "mag?page_size=100&page=2"
        )
        fetcher = RoutingFetcher(
            {
                API_URL: payload(
                    [job(title="Senior Parts Runner")],
                    count=2,
                ),
                api_2: payload(
                    [job(opening_id=2, title="Other")],
                    count=2,
                    page=2,
                ),
            }
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Parts Runner"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(fetcher.requests), 3)

    def test_exact_title_with_target_location_continues_to_later_pages(self):
        page_1 = [
            job(
                opening_id=index,
                title=(
                    "General Sales Manager"
                    if index == 1
                    else f"Role {index}"
                ),
                tenant=f"child-{index}",
                organization=f"Employer {index}",
                locations=[{"city": "Columbia", "state": "SC"}],
            )
            for index in range(1, 101)
        ]
        page_2 = [
            job(
                opening_id=101,
                title="General Sales Manager",
                tenant="target-child",
                organization="Target Employer",
                locations=[{"city": "Henderson", "state": "NC"}],
            )
        ]
        api_2 = (
            "https://api.hireology.com/v2/public/careers/"
            "mag?page_size=100&page=2"
        )
        fetcher = RoutingFetcher(
            {
                API_URL: payload(page_1, count=101),
                api_2: payload(page_2, count=101, page=2),
            }
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(
                title="General Sales Manager",
                location="Henderson, NC",
            ),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(
            result.candidates[-1].url,
            "https://careers.hireology.com/mag/101/description",
        )

    def test_transport_error_uses_typed_provider_fetch_reason(self):
        result = self.adapter.list_jobs(
            RoutingFetcher(
                error=FetchError(
                    "timed out",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                )
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["stop_reason"], "fetch_failed")

    def test_rejects_api_final_url_drift(self):
        bad_final_urls = (
            API_URL.replace("/mag?", "/other?"),
            API_URL.replace("api.hireology.com", "api.hireology.com.evil.test"),
            API_URL.replace("page=1", "page=2"),
            API_URL + "&token=secret",
        )
        for final_url in bad_final_urls:
            with self.subTest(final_url=final_url):
                result = self.adapter.list_jobs(
                    RoutingFetcher(
                        {
                            API_URL: Page(
                                API_URL,
                                payload([job()]),
                                final_url=final_url,
                            )
                        }
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(
                    result.reason_code,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                )
                self.assertFalse(result.inventory_complete)

    def test_rejects_cross_host_wrong_id_and_wrong_detail_shapes(self):
        malformed_records = (
            job(),
            job(),
            job(),
            job(),
            job(),
        )
        malformed_records[0]["career_site_url"] = (
            f"https://evil.test/{CHILD}/{OPENING_ID}/description"
        )
        malformed_records[1]["career_site_url"] = (
            f"https://careers.hireology.com/{CHILD}/999/description"
        )
        malformed_records[2]["career_site_url"] = (
            f"https://careers.hireology.com/{CHILD}/{OPENING_ID}/apply"
        )
        malformed_records[3]["career_site_url"] += "?token=secret"
        malformed_records[4]["career_site_path"] = (
            f"/other/{OPENING_ID}/description"
        )

        for record in malformed_records:
            with self.subTest(record=record):
                result = self.adapter.list_jobs(
                    RoutingFetcher({API_URL: payload([record])}),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.trace["stop_reason"], "invalid_job_record")

    def test_rejects_non_open_and_malformed_location_or_organization(self):
        records = (
            job(status="Closed"),
            job(locations="Henderson, NC"),
            job(remote="true"),
            job(),
        )
        records[3]["organization"] = "Classic Toyota"
        for record in records:
            with self.subTest(record=record):
                result = self.adapter.list_jobs(
                    RoutingFetcher({API_URL: payload([record])}),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_malformed_envelopes_pagination_drift_and_duplicates(self):
        envelopes = (
            "{}",
            json.dumps(
                {
                    "data": [job()],
                    "count": 1,
                    "page": 1,
                    "page_size": 100,
                    "token": "secret",
                }
            ),
            payload([job()], count=0),
            payload([job()], page=2),
            payload([job()], page_size=50),
            payload([job(), job()], count=2),
        )
        for body in envelopes:
            with self.subTest(body=body[:100]):
                result = self.adapter.list_jobs(
                    RoutingFetcher({API_URL: body}),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_count_drift_and_bounds_declared_inventory(self):
        api_2 = (
            "https://api.hireology.com/v2/public/careers/"
            "mag?page_size=100&page=2"
        )
        count_drift = self.adapter.list_jobs(
            RoutingFetcher(
                {
                    API_URL: payload([job()], count=2),
                    api_2: payload(
                        [job(opening_id=2)],
                        count=3,
                        page=2,
                    ),
                }
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(count_drift.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(count_drift.trace["stop_reason"], "count_drift")
        self.assertFalse(count_drift.inventory_complete)

        bounded = self.adapter.list_jobs(
            RoutingFetcher({API_URL: payload([job()], count=1001)}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(bounded.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(bounded.retryable)
        self.assertFalse(bounded.inventory_complete)
        self.assertEqual(bounded.trace["stop_reason"], "record_cap_exceeded")

    def test_rejects_invalid_board_locator(self):
        board = self.board.__class__(
            "https://careers.hireology.com/other",
            "hireology",
            ROOT,
        )
        result = self.adapter.list_jobs(
            RoutingFetcher(),
            board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["api_urls"], [])


if __name__ == "__main__":
    unittest.main()
