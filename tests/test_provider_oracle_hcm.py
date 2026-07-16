import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.oracle_hcm import ADAPTER, OracleHCMAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import Page


HOST = "acme.fa.us2.oraclecloud.com"
BOARD_URL = f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1"
DETAIL_URL = f"{BOARD_URL}/job/1042"


def job_page(**overrides):
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Platform Engineer",
        "identifier": {"@type": "PropertyValue", "value": "1042"},
        "hiringOrganization": {"@type": "Organization", "name": "Acme Systems"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "Denver",
                "addressRegion": "CO",
                "addressCountry": "US",
            },
        },
        "url": DETAIL_URL,
        "datePosted": "2026-06-01",
        "validThrough": "2099-12-31T23:59:59Z",
    }
    posting.update(overrides)
    return f'<script type="application/ld+json">{json.dumps(posting)}</script>'


class StubFetcher:
    def __init__(self, html, final_url=None):
        self.html = html
        self.final_url = final_url
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="oracle-hcm-fixture",
        )


class OracleHCMAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = OracleHCMAdapter()

    def test_exported_adapter_satisfies_provider_contract(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertEqual(ADAPTER.name, "oracle_hcm")
        self.assertTrue(ADAPTER.supports_listing)
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        self.assertIs(native["oracle_hcm"], ADAPTER)

    def test_identifies_canonical_board_and_versioned_exact_locator(self):
        board = self.adapter.identify_board(f"{DETAIL_URL}?source=search#description")

        self.assertEqual(board.url, BOARD_URL)
        locator = json.loads(board.identifier)
        self.assertEqual(locator["v"], 1)
        self.assertEqual(locator["tenant"], "acme")
        self.assertEqual(locator["site"], "CX_1")
        self.assertEqual(locator["opening_id"], "1042")
        self.assertEqual(locator["detail_url"], DETAIL_URL)
        plain = self.adapter.identify_board(f"{BOARD_URL}/")
        self.assertEqual(plain.url, BOARD_URL)
        self.assertNotIn("detail_url", json.loads(plain.identifier))
        no_region = self.adapter.identify_board(
            "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        )
        self.assertEqual(
            no_region.url,
            "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        )

    def test_fetches_only_exact_detail_and_preserves_structured_raw_evidence(self):
        board = self.adapter.identify_board(DETAIL_URL)
        fetcher = StubFetcher(job_page())

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual([call["url"] for call in fetcher.calls], [DETAIL_URL])
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Platform Engineer")
        self.assertEqual(candidate.url, DETAIL_URL)
        self.assertEqual(candidate.location, "Denver, CO, US")
        self.assertEqual(candidate.raw["hiringOrganization"]["name"], "Acme Systems")
        self.assertEqual(candidate.raw["hiring_organization_name"], "Acme Systems")
        self.assertEqual(candidate.raw["datePosted"], "2026-06-01")
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertTrue(result.inventory_complete)

    def test_rejects_cross_tenant_site_and_opening_redirects(self):
        board = self.adapter.identify_board(DETAIL_URL)
        redirects = [
            "https://other.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1042",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_2/job/1042",
            f"{BOARD_URL}/job/9999",
        ]
        for redirect in redirects:
            with self.subTest(redirect=redirect):
                result = self.adapter.list_jobs(
                    StubFetcher(job_page(), final_url=redirect),
                    board,
                    JobQuery(title="Platform Engineer"),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.inventory_complete)

    def test_rejects_structured_url_and_identifier_conflicts(self):
        board = self.adapter.identify_board(DETAIL_URL)
        payloads = [
            job_page(url=f"{BOARD_URL}/job/9999"),
            job_page(identifier={"@type": "PropertyValue", "value": "9999"}),
            job_page(url="https://other.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1042"),
        ]
        for html in payloads:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    StubFetcher(html), board, JobQuery(title="Platform Engineer")
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_closed_detail_is_verified_empty(self):
        board = self.adapter.identify_board(DETAIL_URL)
        closed_by_page = self.adapter.list_jobs(
            StubFetcher("<main>This job is no longer available.</main>"),
            board,
            JobQuery(title="Platform Engineer"),
        )
        closed_by_date = self.adapter.list_jobs(
            StubFetcher(job_page(validThrough="2000-01-01T00:00:00Z")),
            board,
            JobQuery(title="Platform Engineer"),
        )
        for result in (closed_by_page, closed_by_date):
            self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
            self.assertTrue(result.inventory_complete)
            self.assertEqual(result.candidates, [])

    def test_missing_or_malformed_json_ld_is_typed_incomplete(self):
        board = self.adapter.identify_board(DETAIL_URL)
        pages = [
            "<html><h1>Platform Engineer</h1></html>",
            '<script type="application/ld+json">{"@type":"JobPosting"</script>',
        ]
        for html in pages:
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    StubFetcher(html),
                    board,
                    JobQuery(title="Platform Engineer"),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_login_wall_is_typed_incomplete(self):
        board = self.adapter.identify_board(DETAIL_URL)

        result = self.adapter.list_jobs(
            StubFetcher("<title>Sign in</title><main>Sign in to continue</main>"),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(result.reason_code, "LOGIN_REQUIRED")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_title_is_never_synthesized_from_target_query(self):
        board = self.adapter.identify_board(DETAIL_URL)
        missing = self.adapter.list_jobs(
            StubFetcher(job_page(title=None)),
            board,
            JobQuery(title="Invented Query Title"),
        )
        mismatch = self.adapter.list_jobs(
            StubFetcher(job_page()),
            board,
            JobQuery(title="Invented Query Title"),
        )

        self.assertEqual(missing.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(missing.inventory_complete)
        self.assertEqual(mismatch.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(mismatch.inventory_complete)
        self.assertEqual(mismatch.candidates, [])

    def test_rejects_unsafe_and_non_candidate_experience_urls(self):
        unsafe = [
            f"http://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1",
            f"https://user:secret@{HOST}/hcmUI/CandidateExperience/en/sites/CX_1",
            f"https://{HOST}:8443/hcmUI/CandidateExperience/en/sites/CX_1",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1?token=secret",
            "https://[::1]/hcmUI/CandidateExperience/en/sites/CX_1",
            "https://[broken/hcmUI/CandidateExperience/en/sites/CX_1",
            "https://acme.fa.us2.oraclecloud.com.evil.example/hcmUI/CandidateExperience/en/sites/CX_1",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/login",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/profile",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/1042/extra",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1%2Fother/job/1042",
        ]
        for url in unsafe:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_plain_board_returns_typed_incomplete_without_fetching(self):
        board = self.adapter.identify_board(BOARD_URL)
        fetcher = StubFetcher(job_page())

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(fetcher.calls, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertFalse(result.inventory_complete)

    def test_rejects_tampered_locator(self):
        board = self.adapter.identify_board(DETAIL_URL)
        locator = json.loads(board.identifier)
        locator["site"] = "CX_2"
        tampered = JobBoard(
            url=board.url,
            provider=board.provider,
            identifier=json.dumps(locator),
        )
        result = self.adapter.list_jobs(
            StubFetcher(job_page()), tampered, JobQuery(title="Platform Engineer")
        )
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)


if __name__ == "__main__":
    unittest.main()
