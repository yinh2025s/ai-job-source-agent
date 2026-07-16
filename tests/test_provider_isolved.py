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
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if self.page is None:
            raise FetchError(f"unexpected URL: {url}")
        return self.page


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

    def test_validates_identity_but_keeps_inventory_typed_incomplete(self):
        fetcher = RecordingFetcher(
            Page(url=BOARD_URL, html=board_html(), source="frozen-westpace")
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Registered Nurse")
        )

        self.assertEqual(fetcher.requests, [(BOARD_URL, None, None)])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.inventory_scope, "unknown")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["api_urls"], [])
        self.assertEqual(
            result.trace["identity"],
            {
                "tenant": TENANT,
                "career_site_name": "Gary and Mary West PACE",
                "organization_id": "2730",
                "domain_id": "3495",
            },
        )

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

