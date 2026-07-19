import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.isolved import ADAPTER, ISolvedAdapter
from job_source_agent.web import FetchError, Page


TENANT = "westpace"
BOARD_URL = "https://westpace.isolvedhire.com/jobs/"


def board_html(
    *,
    tenant=TENANT,
    career_site_name="Gary and Mary West PACE",
    organization_id="2730",
    domain_id="3495",
    component_organization_id=None,
    component_domain_id=None,
    include_route=True,
    include_component=True,
):
    route = ""
    if include_route:
        route = f"""
        <script>
        mountingData.courierCurrentRouteData = {{
          "domain_id": "{domain_id}",
          "career_site_name": "{career_site_name}",
          "organization_id": "{organization_id}",
          "user_id": 0
        }};
        </script>
        """
    component = ""
    if include_component:
        component_organization_id = component_organization_id or organization_id
        component_domain_id = component_domain_id or domain_id
        component = f"""
        <script>
        window.bootstrapVue("#job_listings", [ 'JobListings' ], {{
          componentData: {{ organizationId: {component_organization_id},
            domainId: {component_domain_id},
            domainName: "isolvedhire.com", subdomainName: "{tenant}" }}
        }});
        </script>
        """
    return route + component


class RecordingFetcher:
    def __init__(self, page=None, error=None, pages=None):
        self.pages = list(pages) if pages is not None else ([page] if page else [])
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.pages:
            raise FetchError(f"unexpected URL: {url}")
        return self.pages.pop(0)


def inventory_page(jobs, *, count=None, **data_overrides):
    data = {
        "jobs": jobs,
        "jobCount": len(jobs) if count is None else count,
        "domainId": 3495,
        "subdomainName": TENANT,
        **data_overrides,
    }
    return Page(
        url=(
            "https://westpace.isolvedhire.com/core/jobs/3495?"
            "getParams=%7B%22isInternal%22%3A0%7D"
        ),
        html=json.dumps({"success": True, "data": data}),
        source="synthetic-isolved-inventory",
    )


def job(job_id="123", title="Registered Nurse", **overrides):
    return {
        "id": int(job_id),
        "title": title,
        "jobUrl": f"https://westpace.isolvedhire.com/jobs/{job_id}",
        "jobLocation": "San Marcos, CA",
        **overrides,
    }


class ISolvedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ISolvedAdapter()
        self.board = JobBoard(BOARD_URL, "isolved", TENANT)

    def test_canonicalizes_public_board_and_duplicate_slashes(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        for url in (
            BOARD_URL,
            BOARD_URL.rstrip("/"),
            "https://westpace.isolvedhire.com//jobs//",
            "https://westpace.isolvedhire.com///jobs/?source=careers",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_non_public_hosts_login_contact_and_detail_routes(self):
        rejected = (
            "http://westpace.isolvedhire.com/jobs/",
            "https://isolvedhire.com/jobs/",
            "https://westpace.isolvedhire.com.evil.test/jobs/",
            "https://user@westpace.isolvedhire.com/jobs/",
            "https://westpace.isolvedhire.com:8443/jobs/",
            "https://westpace.isolvedhire.com/account/login.php",
            "https://westpace.isolvedhire.com/contact/",
            "https://westpace.isolvedhire.com/jobs/12345/",
            "https://westpace.isolvedhire.com/jobs/#opening",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_validates_identity_and_lists_complete_public_inventory(self):
        fetcher = RecordingFetcher(
            pages=[
                Page(url=BOARD_URL, html=board_html(), source="frozen-westpace"),
                inventory_page([job()]),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Registered Nurse")
        )

        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual(fetcher.requests[0], (BOARD_URL, None, None))
        self.assertEqual(
            fetcher.requests[1][0],
            "https://westpace.isolvedhire.com/core/jobs/3495?"
            "getParams=%7B%22isInternal%22%3A0%7D",
        )
        self.assertEqual(fetcher.requests[1][2]["Referer"], BOARD_URL)
        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://westpace.isolvedhire.com/jobs/123")
        self.assertEqual(result.candidates[0].location, "San Marcos, CA")
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.trace["api_urls"]), 1)
        self.assertEqual(
            result.trace["identity"],
            {
                "tenant": TENANT,
                "career_site_name": "Gary and Mary West PACE",
                "organization_id": "2730",
                "domain_id": "3495",
            },
        )

    def test_accepts_harmless_detail_variants_and_returns_local_canonical_url(self):
        for detail_url in (
            "https://westpace.isolvedhire.com/jobs/123",
            "https://westpace.isolvedhire.com/jobs/123/",
            "https://westpace.isolvedhire.com:443/jobs/123/",
        ):
            with self.subTest(detail_url=detail_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        pages=[
                            Page(url=BOARD_URL, html=board_html()),
                            inventory_page([job(jobUrl=detail_url)]),
                        ]
                    ),
                    self.board,
                    JobQuery(title="Registered Nurse"),
                )

                self.assertIsNone(result.reason_code)
                self.assertEqual(len(result.candidates), 1)
                self.assertEqual(
                    result.candidates[0].url,
                    "https://westpace.isolvedhire.com/jobs/123",
                )

    def test_rejects_noncanonical_detail_paths_and_query_controls(self):
        invalid_urls = (
            "https://westpace.isolvedhire.com/jobs/123/apply",
            "https://westpace.isolvedhire.com/jobs/123/arbitrary",
            "https://westpace.isolvedhire.com/jobs/123//",
            "https://westpace.isolvedhire.com//jobs/123",
            "https://westpace.isolvedhire.com/Jobs/123",
            "https://westpace.isolvedhire.com/jobs%2F123",
            "https://westpace.isolvedhire.com/jobs/123%2Fapply",
            "https://westpace.isolvedhire.com/jobs/123%5Capply",
            "https://westpace.isolvedhire.com/jobs\\123",
            "https://westpace.isolvedhire.com/jobs/123?apply=true",
            "https://westpace.isolvedhire.com/jobs/123?redirect=%2Fjobs%2F999",
            "https://westpace.isolvedhire.com/jobs/123#apply",
        )
        for detail_url in invalid_urls:
            with self.subTest(detail_url=detail_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        pages=[
                            Page(url=BOARD_URL, html=board_html()),
                            inventory_page([job(jobUrl=detail_url)]),
                        ]
                    ),
                    self.board,
                    JobQuery(),
                )

                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.candidates, [])
                self.assertFalse(result.inventory_complete)

    def test_inventory_empty_is_complete_and_malformed_or_cross_tenant_fails_closed(self):
        empty = self.adapter.list_jobs(
            RecordingFetcher(
                pages=[Page(url=BOARD_URL, html=board_html()), inventory_page([])]
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(empty.inventory_complete)

        invalid_pages = (
            inventory_page([job()], count=2),
            inventory_page([job()], domainId=9999),
            inventory_page([job()], subdomainName="other"),
            inventory_page([job(jobUrl="https://other.isolvedhire.com/jobs/123")]),
            inventory_page([job(jobUrl="https://westpace.isolvedhire.com/jobs/999")]),
        )
        for invalid_page in invalid_pages:
            with self.subTest(body=invalid_page.html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        pages=[Page(url=BOARD_URL, html=board_html()), invalid_page]
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_rejects_cross_tenant_and_login_redirects(self):
        for final_url in (
            "https://other.isolvedhire.com/jobs/",
            "https://westpace.isolvedhire.com/account/login.php",
            "https://westpace.isolvedhire.com/contact/",
            "https://westpace.isolvedhire.com/jobs/?session=unexpected",
        ):
            with self.subTest(final_url=final_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        Page(url=BOARD_URL, final_url=final_url, html=board_html())
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertEqual(result.candidates, [])
                self.assertFalse(result.inventory_complete)

    def test_rejects_cross_tenant_or_conflicting_page_identity(self):
        variants = (
            board_html(tenant="other"),
            board_html(component_organization_id="9999"),
            board_html(component_domain_id="9999"),
            board_html(domain_id="0"),
            board_html(career_site_name=""),
            board_html() + board_html(organization_id="9999"),
        )
        for html in variants:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(Page(url=BOARD_URL, html=html)),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertNotIn("identity", result.trace)
                self.assertEqual(result.candidates, [])

    def test_rejects_malformed_and_incomplete_page_identity(self):
        variants = (
            "",
            board_html(include_route=False),
            board_html(include_component=False),
            '<script>mountingData.courierCurrentRouteData = {bad json};</script>',
            board_html().replace('domainName: "isolvedhire.com"', 'domainName: "evil.test"'),
        )
        for html in variants:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher(Page(url=BOARD_URL, html=html)),
                    self.board,
                    JobQuery(title="Nurse"),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertEqual(result.inventory_scope, "unknown")
                self.assertFalse(result.inventory_complete)

    def test_rejects_tampered_board_and_classifies_fetch_failure(self):
        tampered = JobBoard(BOARD_URL, "isolved", "other")
        invalid = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(failed.retryable)
        self.assertFalse(failed.inventory_complete)


if __name__ == "__main__":
    unittest.main()
