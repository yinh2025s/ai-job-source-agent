import json
from pathlib import Path
import unittest
from urllib.parse import parse_qsl, urlparse

from job_source_agent.job_board import JobBoard
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.providers.talentbrew import ADAPTER, TalentBrewAdapter
from job_source_agent.web import FetchError, Page


HOST = "jobs.northstar.example"
TENANT = "73124"
SITE = "88402"
BOARD_URL = f"https://{HOST}/en/search-jobs"


def fingerprint_html(
    *,
    tenant=TENANT,
    site=SITE,
    locale="en",
    form_host=HOST,
    form_method="GET",
    org_ids=None,
    asset_host="tbcdn.talentbrew.com",
    asset_tenant=None,
):
    org_ids = tenant if org_ids is None else org_ids
    asset_tenant = tenant if asset_tenant is None else asset_tenant
    return f"""
      <html><head>
        <meta name="site-tenant-id" content="{tenant}">
        <meta name="site-organization-id" content="{tenant}">
        <meta name="site-id" content="{site}">
        <meta name="gtm_tenantid" content="{tenant}">
        <meta name="gtm_companysiteid" content="{site}">
        <meta name="site-current-language" content="{locale}">
        <meta name="site-url-modified-language-code" content="{locale}">
        <link rel="stylesheet"
          href="https://{asset_host}/company/{asset_tenant}/css/{site}-GST.css">
      </head><body>
        <form action="https://{form_host}/{locale}/search-jobs" method="{form_method}">
          <input name="k" type="search">
          <input name="l" type="text">
          <input name="orgIds" type="hidden" value="{org_ids}">
        </form>
      </body></html>
    """


def card(job_id, title, *, tenant=TENANT, locale="en", location=None, host=HOST):
    location_html = (
        f'<span class="section29__result-location">{location}</span>'
        if location is not None
        else ""
    )
    slug = title.casefold().replace(" ", "-")
    return f"""
      <li class="section29__search-results-li">
        <a class="section29__search-results-link"
           href="https://{host}/{locale}/job/meridian/{slug}/{tenant}/{job_id}"
           data-job-id="{job_id}">
          <h2 class="section29__search-results-job-title">{title}</h2>
          {location_html}
        </a>
      </li>
    """


def inventory_html(*, total, pages, current, page_size, cards=()):
    return f"""
      <section id="search-results"
        data-total-job-results="{total}"
        data-total-pages="{pages}"
        data-current-page="{current}"
        data-records-per-page="{page_size}">
        <ul class="section29__search-results-ul">{''.join(cards)}</ul>
      </section>
    """


class RecordingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = list(pages or [])
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.pages:
            raise AssertionError(f"unexpected request: {url}")
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Page):
            return value
        return Page(url=url, final_url=url, html=value, source="fictional-fixture")


class TalentBrewAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TalentBrewAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=f"https://{HOST}/en/index.html", html=fingerprint_html())
        )
        self.assertIsNotNone(self.board)

    def test_native_adapter_is_page_aware_and_url_recognition_is_disabled(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["talentbrew"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertFalse(ADAPTER.recognizes(BOARD_URL))
        self.assertIsNone(ADAPTER.identify_board(BOARD_URL))

    def test_strong_fingerprint_builds_replay_safe_strict_locator(self):
        self.assertEqual(self.board.url, BOARD_URL)
        self.assertTrue(self.board.replay_safe)
        self.assertEqual(
            self.board.identifier,
            '{"host":"jobs.northstar.example","locale":"en",'
            '"site_id":"88402","tenant_id":"73124"}',
        )

        second = self.adapter.identify_board_from_page(
            Page(
                url="https://careers.orbit.example/fr/home",
                html=fingerprint_html(
                    tenant="90210",
                    site="4815",
                    locale="fr",
                    form_host="careers.orbit.example",
                ),
            )
        )
        self.assertEqual(second.url, "https://careers.orbit.example/fr/search-jobs")
        self.assertEqual(
            json.loads(second.identifier),
            {
                "host": "careers.orbit.example",
                "locale": "fr",
                "site_id": "4815",
                "tenant_id": "90210",
            },
        )

    def test_rejects_weak_spoofed_or_inconsistent_fingerprints(self):
        cases = (
            "Radancy TalentBrew search-jobs",
            fingerprint_html(asset_host="tbcdn.talentbrew.com.evil.example"),
            fingerprint_html(asset_tenant="99999"),
            fingerprint_html(org_ids="99999"),
            fingerprint_html(form_host="other.example"),
            fingerprint_html(form_method="POST"),
            fingerprint_html().replace(
                '<meta name="gtm_companysiteid" content="88402">',
                '<meta name="gtm_companysiteid" content="99999">',
            ),
            f"<!-- {fingerprint_html()} -->",
        )
        for html in cases:
            with self.subTest(html=html[:100]):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=f"https://{HOST}/en/home", html=html)
                    )
                )

    def test_rejects_unsafe_page_origins(self):
        for url in (
            f"http://{HOST}/en/home",
            f"https://user@{HOST}/en/home",
            f"https://{HOST}:8443/en/home",
            "https://127.0.0.1/en/home",
        ):
            with self.subTest(url=url):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=url, html=fingerprint_html())
                    )
                )

    def test_optional_palo_shaped_snapshot_fingerprint(self):
        snapshot = Path(
            "/private/tmp/.87-focused-snapshots/sites/"
            "jobs.paloaltonetworks.com/en/index.html"
        )
        if not snapshot.exists():
            self.skipTest("focused read-only snapshot unavailable")

        board = self.adapter.identify_board_from_page(
            Page(
                url="https://jobs.paloaltonetworks.com/en/index.html",
                html=snapshot.read_text(encoding="utf-8"),
                source="focused-read-only-snapshot",
            )
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.provider, "talentbrew")
        self.assertTrue(board.replay_safe)

    def test_requests_only_frozen_ssr_get_keys_and_parses_typed_cards(self):
        fetcher = RecordingFetcher(
            [
                inventory_html(
                    total=2,
                    pages=1,
                    current=1,
                    page_size=20,
                    cards=(
                        card("41001", "Applied AI Engineer", location="Meridian"),
                        card("41002", "Platform Engineer"),
                    ),
                )
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Applied AI Engineer", location="Meridian"),
        )

        requested, data, headers = fetcher.requests[0]
        self.assertEqual(
            parse_qsl(urlparse(requested).query, keep_blank_values=True),
            [
                ("k", "Applied AI Engineer"),
                ("l", "Meridian"),
                ("orgIds", TENANT),
                ("p", "1"),
            ],
        )
        self.assertIsNone(data)
        self.assertIsNone(headers)
        self.assertEqual([item.title for item in result.candidates], ["Applied AI Engineer", "Platform Engineer"])
        self.assertEqual(result.candidates[0].location, "Meridian")
        self.assertIsNone(result.candidates[1].location)
        self.assertTrue(result.inventory_complete)
        self.assertNotIn("requests", result.trace)

    def test_follows_all_pages_and_requires_stable_count_continuity(self):
        fetcher = RecordingFetcher(
            [
                inventory_html(
                    total=3,
                    pages=2,
                    current=1,
                    page_size=2,
                    cards=(card("42001", "One"), card("42002", "Two")),
                ),
                inventory_html(
                    total=3,
                    pages=2,
                    current=2,
                    page_size=2,
                    cards=(card("42003", "Three"),),
                ),
            ]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Missing"))

        self.assertEqual([urlparse(item[0]).query[-3:] for item in fetcher.requests], ["p=1", "p=2"])
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["records_seen"], 3)
        self.assertTrue(result.inventory_complete)
        self.assertFalse(result.trace["stopped_on_exact_title"])

    def test_exact_title_can_stop_early_but_inventory_is_incomplete(self):
        fetcher = RecordingFetcher(
            [
                inventory_html(
                    total=2,
                    pages=2,
                    current=1,
                    page_size=1,
                    cards=(card("43001", "  Applied   AI Engineer "),),
                )
            ]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="applied ai engineer")
        )

        self.assertEqual(len(fetcher.requests), 1)
        self.assertIsNone(result.reason_code)
        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.trace["stopped_on_exact_title"])

    def test_schema_valid_filtered_zero_is_authoritative(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [inventory_html(total=0, pages=0, current=1, page_size=20)]
            ),
            self.board,
            JobQuery(title="No Such Role"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")

    def test_empty_module_or_has_jobs_fragment_never_claims_empty(self):
        for html in (
            "<div class='section29'></div>",
            '{"results":"","hasJobs":true}',
            inventory_html(total=1, pages=1, current=1, page_size=20),
        ):
            with self.subTest(html=html):
                result = self.adapter.list_jobs(
                    RecordingFetcher([html]), self.board, JobQuery(title="Missing")
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_duplicate_malformed_and_cross_tenant_cards(self):
        cases = (
            (
                [
                    inventory_html(
                        total=2,
                        pages=2,
                        current=1,
                        page_size=1,
                        cards=(card("44001", "One"),),
                    ),
                    inventory_html(
                        total=2,
                        pages=2,
                        current=2,
                        page_size=1,
                        cards=(card("44001", "One Again"),),
                    ),
                ],
                "duplicate_job_id",
            ),
            (
                [
                    inventory_html(
                        total=1,
                        pages=1,
                        current=1,
                        page_size=20,
                        cards=(card("44002", "Wrong Tenant", tenant="99999"),),
                    )
                ],
                "cross_tenant_or_invalid_job_card",
            ),
            (
                [
                    inventory_html(
                        total=1,
                        pages=1,
                        current=1,
                        page_size=20,
                        cards=(
                            '<li class="section29__search-results-li">'
                            '<a class="section29__search-results-link" data-job-id="44003" '
                            f'href="/en/job/city/role/{TENANT}/44003"></a></li>',
                        ),
                    )
                ],
                "invalid_typed_job_card",
            ),
        )
        for pages, stop_reason in cases:
            with self.subTest(stop_reason=stop_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(pages), self.board, JobQuery()
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.trace["stop_reason"], stop_reason)
                self.assertFalse(result.inventory_complete)

    def test_rejects_response_redirect_and_tampered_locator(self):
        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    Page(
                        url=BOARD_URL,
                        final_url="https://other.example/en/search-jobs?k=&l=&orgIds=73124&p=1",
                        html=inventory_html(total=0, pages=0, current=1, page_size=20),
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        value = json.loads(self.board.identifier)
        tampered = JobBoard(
            url=self.board.url,
            provider=self.board.provider,
            identifier=json.dumps(value),
            replay_safe=True,
        )
        invalid = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_fetch_failure_is_typed_and_trace_does_not_store_query(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="Private Search Terms", location="Private Location"),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertNotIn("Private", json.dumps(result.trace))

    def test_rejects_oversized_or_controlled_query_without_fetch(self):
        for query in (
            JobQuery(title="x" * 201),
            JobQuery(title="Data\x00Scientist"),
            JobQuery(title="Data Scientist", location="x" * 301),
        ):
            with self.subTest(query=query):
                fetcher = RecordingFetcher()
                result = self.adapter.list_jobs(fetcher, self.board, query)
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertEqual(fetcher.requests, [])

    def test_page_cap_is_incomplete(self):
        pages = []
        for page_number in range(1, 11):
            pages.append(
                inventory_html(
                    total=11,
                    pages=11,
                    current=page_number,
                    page_size=1,
                    cards=(card(str(45000 + page_number), f"Role {page_number}"),),
                )
            )

        result = self.adapter.list_jobs(RecordingFetcher(pages), self.board, JobQuery())

        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.trace["page_count"], 10)
        self.assertFalse(result.inventory_complete)


if __name__ == "__main__":
    unittest.main()
