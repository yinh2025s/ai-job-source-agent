import json
import unittest
from urllib.parse import parse_qs, urlparse

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
        self.html = html if isinstance(html, list) else [html]
        self.final_url = final_url if isinstance(final_url, list) else [final_url]
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        index = len(self.calls) - 1
        html = self.html[min(index, len(self.html) - 1)]
        final_url = self.final_url[min(index, len(self.final_url) - 1)]
        return Page(
            url=url,
            final_url=final_url or url,
            html=html,
            source="oracle-hcm-fixture",
        )


def inventory_payload(*rows, total=None, site="CX_1"):
    values = list(rows)
    return json.dumps(
        {
            "items": [
                {
                    "SiteNumber": site,
                    "TotalJobsCount": len(values) if total is None else total,
                    "requisitionList": values,
                }
            ]
        }
    )


def inventory_row(job_id="1042", title="Platform Engineer", location="Denver, CO, US"):
    return {
        "Id": job_id,
        "Title": title,
        "PrimaryLocation": location,
        "PostedDate": "2026-06-01",
    }


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
        listing = self.adapter.identify_board(f"{BOARD_URL}/jobs?keyword=Platform")
        self.assertEqual(listing.url, BOARD_URL)
        self.assertNotIn("detail_url", json.loads(listing.identifier))
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

    def test_preserves_texas_childrens_exact_detail_identity(self):
        detail_url = (
            "https://eohh.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX/job/425798"
        )
        title = "Registered Nurse - Inpatient"
        board = self.adapter.identify_board(detail_url)
        fetcher = StubFetcher(
            job_page(
                title=title,
                identifier={"@type": "PropertyValue", "value": "425798"},
                url=detail_url,
            )
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title=title))

        self.assertEqual([candidate.url for candidate in result.candidates], [detail_url])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["tenant"], "eohh")
        self.assertEqual(result.trace["site"], "CX")
        self.assertEqual(result.trace["opening_id"], "425798")

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
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/openings",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/1042/extra",
            f"https://{HOST}/hcmUI/CandidateExperience/en/sites/CX_1%2Fother/job/1042",
        ]
        for url in unsafe:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_plain_board_reads_public_title_filtered_inventory(self):
        board = self.adapter.identify_board(BOARD_URL)
        fetcher = StubFetcher(inventory_payload(inventory_row()))

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(len(fetcher.calls), 1)
        self.assertIn("/hcmRestApi/resources/latest/recruitingCEJobRequisitions?", fetcher.calls[0]["url"])
        self.assertIn("expand=requisitionList", fetcher.calls[0]["url"])
        self.assertEqual(fetcher.calls[0]["headers"], {"Accept": "application/json"})
        self.assertEqual([candidate.url for candidate in result.candidates], [DETAIL_URL])
        self.assertEqual(result.candidates[0].location, "Denver, CO, US")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertTrue(result.inventory_complete)

    def test_plain_board_accepts_common_title_punctuation(self):
        board = self.adapter.identify_board(BOARD_URL)
        title = "Registered Nurse (RN) - LDRP"
        fetcher = StubFetcher(inventory_payload(inventory_row(title=title)))

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title=title))

        finder = parse_qs(urlparse(fetcher.calls[0]["url"]).query)["finder"][0]
        self.assertIn(f"keyword={title},limit=25,offset=0", finder)
        self.assertEqual([candidate.title for candidate in result.candidates], [title])
        self.assertTrue(result.inventory_complete)

    def test_plain_board_paginates_until_the_title_filtered_inventory_is_complete(self):
        board = self.adapter.identify_board(BOARD_URL)
        first_page = [inventory_row(str(job_id), f"Platform Engineer {job_id}") for job_id in range(25)]
        second_page = [inventory_row(str(job_id), f"Platform Engineer {job_id}") for job_id in range(25, 40)]
        result = self.adapter.list_jobs(
            StubFetcher(
                [
                    inventory_payload(*first_page, total=40),
                    inventory_payload(*second_page, total=40),
                ]
            ),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(len(result.candidates), 40)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.trace["api_urls"]), 2)
        self.assertIn("offset%3D0", result.trace["api_urls"][0])
        self.assertIn("offset%3D25", result.trace["api_urls"][1])
        self.assertEqual(result.trace["stop_reason"], "complete")

    def test_plain_board_exposes_no_candidates_when_inventory_exceeds_bound(self):
        board = self.adapter.identify_board(BOARD_URL)
        result = self.adapter.list_jobs(
            StubFetcher(inventory_payload(inventory_row(), total=251)),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["candidate_count"], 0)
        self.assertEqual(result.trace["stop_reason"], "result_cap_reached")

    def test_plain_board_exposes_no_partial_candidates_for_broken_pagination(self):
        board = self.adapter.identify_board(BOARD_URL)
        first_page = [inventory_row(str(job_id)) for job_id in range(25)]
        bad_second_page = [inventory_row("24")]
        result = self.adapter.list_jobs(
            StubFetcher(
                [
                    inventory_payload(*first_page, total=26),
                    inventory_payload(*bad_second_page, total=26),
                ]
            ),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_plain_board_exposes_no_partial_candidates_after_cross_tenant_redirect(self):
        board = self.adapter.identify_board(BOARD_URL)
        first_page = [inventory_row(str(job_id)) for job_id in range(25)]
        second_page = [inventory_row("25")]
        other_endpoint = (
            "https://other.fa.us2.oraclecloud.com/hcmRestApi/resources/"
            "latest/recruitingCEJobRequisitions"
        )
        result = self.adapter.list_jobs(
            StubFetcher(
                [
                    inventory_payload(*first_page, total=26),
                    inventory_payload(*second_page, total=26),
                ],
                final_url=[None, other_endpoint],
            ),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_plain_board_rejects_cross_tenant_inventory_redirect(self):
        board = self.adapter.identify_board(BOARD_URL)
        result = self.adapter.list_jobs(
            StubFetcher(
                inventory_payload(inventory_row()),
                final_url=(
                    "https://other.fa.us2.oraclecloud.com/hcmRestApi/resources/"
                    "latest/recruitingCEJobRequisitions"
                ),
            ),
            board,
            JobQuery(title="Platform Engineer"),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_plain_board_rejects_invalid_inventory_contracts(self):
        board = self.adapter.identify_board(BOARD_URL)
        payloads = [
            "not-json",
            inventory_payload(inventory_row(), site="OTHER"),
            inventory_payload(inventory_row(), total=0),
            inventory_payload(inventory_row(job_id="bad/id")),
            inventory_payload(inventory_row(title="")),
            json.dumps({"items": []}),
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(
                    StubFetcher(payload),
                    board,
                    JobQuery(title="Platform Engineer"),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_plain_board_requires_bounded_title(self):
        board = self.adapter.identify_board(BOARD_URL)
        fetcher = StubFetcher(inventory_payload())

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(fetcher.calls, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)

    def test_plain_board_rejects_finder_grammar_punctuation_without_fetching(self):
        board = self.adapter.identify_board(BOARD_URL)
        for title in ("Engineer, Platform", "Engineer;offset=25", "Engineer\nAdmin"):
            with self.subTest(title=title):
                fetcher = StubFetcher(inventory_payload())
                result = self.adapter.list_jobs(fetcher, board, JobQuery(title=title))

                self.assertEqual(fetcher.calls, [])
                self.assertEqual(result.candidates, [])
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
