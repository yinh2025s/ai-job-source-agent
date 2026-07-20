import json
from pathlib import Path
import unittest

from job_source_agent.providers.base import (
    JobBoard,
    JobQuery,
    PageProbeProviderAdapter,
    ProviderAdapter,
)
from job_source_agent.providers.freshteam import ADAPTER, FreshteamAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "freshteam"
ASSET_URL = (
    "https://s3.amazonaws.com/files.freshteam.com/production/24815/"
    "attachments/4002236987/original/4000011339_widget.js?1612292094"
)
TENANT = "fixtureco"
BOARD_URL = f"https://{TENANT}.freshteam.com/jobs"
INVENTORY_URL = f"https://{TENANT}.freshteam.com/hire/widgets/jobs.json"
CHAMP_SNAPSHOT = Path(
    "/private/tmp/fresh100-v188-cold-20260720-run1/snapshots/sites/"
    "www.champtitles.com/open-positions/index.html"
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def widget_page(html=None, *, url="https://careers.example/openings"):
    return Page(
        url=url,
        html=(f'<div id="freshteam-widget"></div><script src="{ASSET_URL}"></script>')
        if html is None
        else html,
        source="freshteam-page-fixture",
    )


def inventory_payload(jobs=None):
    payload = json.loads(fixture("inventory.json"))
    if jobs is not None:
        payload["jobs"] = jobs
    return json.dumps(payload)


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
            raise AssertionError(f"unexpected URL: {url}")
        if isinstance(response, Page):
            return response
        return Page(url=url, html=response, source="freshteam-provider-fixture")


def successful_probe_pages(*, asset_url=ASSET_URL, tenant=TENANT, inventory=None):
    inventory_url = f"https://{tenant}.freshteam.com/hire/widgets/jobs.json"
    widget = fixture("widget.js").replace("fixtureco", tenant)
    return {
        asset_url: Page(url=asset_url, html=widget),
        inventory_url: Page(
            url=inventory_url,
            html=inventory_payload() if inventory is None else inventory,
        ),
    }


class FreshteamAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FreshteamAdapter()
        self.board = JobBoard(
            BOARD_URL,
            "freshteam",
            TENANT,
            replay_safe=True,
        )

    def test_is_typed_and_canonicalizes_explicit_provider_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertIsInstance(ADAPTER, PageProbeProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        for url in (
            BOARD_URL,
            f"{BOARD_URL}/SUKqusm_DF1T",
            f"{BOARD_URL}/SUKqusm_DF1T/senior-platform-engineer",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_unsafe_or_non_provider_urls_without_tenant_inference(self):
        rejected = (
            "http://fixtureco.freshteam.com/jobs",
            "https://freshteam.com/jobs",
            "https://www.freshteam.com/jobs",
            "https://fixtureco.freshteam.com.evil.test/jobs",
            "https://user@fixtureco.freshteam.com/jobs",
            "https://fixtureco.freshteam.com:8443/jobs",
            "https://fixtureco.freshteam.com/jobs/bad$id",
            f"{BOARD_URL}?tenant=other",
            ASSET_URL,
            "https://champtitles.freshteam.invalid/jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_widget_declaration_alone_never_proves_a_board(self):
        page = widget_page()

        self.assertIsNone(self.adapter.identify_board_from_page(page))
        self.assertIsNone(
            self.adapter.probe_board(
                RoutingFetcher(error=FetchError("asset unavailable")), page
            )
        )

    def test_probes_declared_asset_and_nonempty_public_inventory(self):
        fetcher = RoutingFetcher(successful_probe_pages())

        board = self.adapter.probe_board(fetcher, widget_page())

        self.assertEqual(board, self.board)
        self.assertEqual([request[0] for request in fetcher.requests], [ASSET_URL, INVENTORY_URL])
        self.assertEqual(fetcher.requests[0][2]["Referer"], "https://careers.example/openings")
        self.assertEqual(fetcher.requests[1][2]["Accept"], "application/json")

        query_page = widget_page(url="https://careers.example/openings?token=private#jobs")
        query_fetcher = RoutingFetcher(successful_probe_pages())
        self.assertEqual(self.adapter.probe_board(query_fetcher, query_page), self.board)
        self.assertEqual(
            query_fetcher.requests[0][2]["Referer"],
            "https://careers.example/openings",
        )

    def test_champ_frozen_page_supplies_only_the_declared_asset_candidate(self):
        if not CHAMP_SNAPSHOT.exists():
            self.skipTest("CHAMP frozen page is unavailable")
        page = Page(
            url="https://www.champtitles.com/open-positions/",
            html=CHAMP_SNAPSHOT.read_text(encoding="utf-8"),
            source="fresh100-champ-snapshot",
        )
        ownum_inventory = inventory_payload()
        fetcher = RoutingFetcher(
            successful_probe_pages(tenant="ownum", inventory=ownum_inventory)
        )

        board = self.adapter.probe_board(fetcher, page)

        self.assertEqual(board.identifier, "ownum")
        self.assertEqual(board.url, "https://ownum.freshteam.com/jobs")
        self.assertNotIn("champ", board.identifier)
        self.assertEqual(fetcher.requests[0][0], ASSET_URL)

    def test_absent_or_ambiguous_config_and_empty_inventory_do_not_probe(self):
        bad_assets = (
            "",
            'loadjsfile("https://assets1.freshteam.com/assets/job_widget.js");',
            fixture("widget.js").replace(
                "</script>", ""
            )
            + 'new freshTeam.JobWidget(elem,"https://other.freshteam.com");',
            fixture("widget.js").replace(
                "https://fixtureco.freshteam.com",
                "http://fixtureco.freshteam.com",
            ),
        )
        for source in bad_assets:
            with self.subTest(source=source[-100:]):
                pages = successful_probe_pages()
                pages[ASSET_URL] = Page(url=ASSET_URL, html=source)
                self.assertIsNone(
                    self.adapter.probe_board(RoutingFetcher(pages), widget_page())
                )

        empty = successful_probe_pages(inventory=inventory_payload([]))
        self.assertIsNone(self.adapter.probe_board(RoutingFetcher(empty), widget_page()))

    def test_rejects_unsafe_widget_declarations_and_asset_redirects(self):
        unsafe_sources = (
            ASSET_URL.replace("https://", "http://"),
            ASSET_URL.replace("s3.amazonaws.com", "s3.amazonaws.com.evil.test"),
            ASSET_URL.replace("?1612292094", "?token=secret"),
            ASSET_URL.replace("/production/24815/", "/production/../24815/"),
        )
        for source in unsafe_sources:
            with self.subTest(source=source):
                page = widget_page(f'<script src="{source}"></script>')
                self.assertIsNone(
                    self.adapter.probe_board(RoutingFetcher(successful_probe_pages()), page)
                )

        pages = successful_probe_pages()
        pages[ASSET_URL] = Page(
            url=ASSET_URL,
            final_url=ASSET_URL.replace("4002236987", "4002236999"),
            html=fixture("widget.js"),
        )
        self.assertIsNone(self.adapter.probe_board(RoutingFetcher(pages), widget_page()))

    def test_rejects_cross_tenant_inventory_redirect_during_probe(self):
        pages = successful_probe_pages()
        pages[INVENTORY_URL] = Page(
            url=INVENTORY_URL,
            final_url="https://other.freshteam.com/hire/widgets/jobs.json",
            html=inventory_payload(),
        )

        self.assertIsNone(self.adapter.probe_board(RoutingFetcher(pages), widget_page()))

    def test_lists_validated_inventory_with_tenant_bound_details(self):
        fetcher = RoutingFetcher({INVENTORY_URL: inventory_payload()})

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" Senior  Platform Engineer "),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Senior Platform Engineer")
        self.assertEqual(candidate.location, "Cleveland, Ohio")
        self.assertEqual(candidate.url, f"{BOARD_URL}/SUKqusm_DF1T")
        self.assertEqual(candidate.provider, "freshteam")
        self.assertEqual(candidate.raw["id"], "5000131056")
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(fetcher.requests[0][2]["Referer"], BOARD_URL)

    def test_verified_empty_inventory_is_complete_but_cannot_establish_widget(self):
        result = self.adapter.list_jobs(
            RoutingFetcher({INVENTORY_URL: inventory_payload([])}),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_list_jobs_rejects_cross_tenant_redirect_and_tampered_board(self):
        redirected = self.adapter.list_jobs(
            RoutingFetcher(
                {
                    INVENTORY_URL: Page(
                        url=INVENTORY_URL,
                        final_url="https://other.freshteam.com/hire/widgets/jobs.json",
                        html=inventory_payload(),
                    )
                }
            ),
            self.board,
            JobQuery(),
        )
        tampered = self.adapter.list_jobs(
            RoutingFetcher(),
            JobBoard(BOARD_URL, "freshteam", "other", replay_safe=True),
            JobQuery(),
        )

        for result in (redirected, tampered):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

        for tenant in ("api", "app", "assets", "support", "www"):
            with self.subTest(tenant=tenant):
                fetcher = RoutingFetcher()
                result = self.adapter.list_jobs(
                    fetcher,
                    JobBoard(
                        f"https://{tenant}.freshteam.com/jobs",
                        "freshteam",
                        tenant,
                        replay_safe=True,
                    ),
                    JobQuery(),
                )
                self.assertEqual(
                    result.reason_code,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                )
                self.assertEqual(fetcher.requests, [])

    def test_invalid_duplicate_and_unsafe_identifiers_fail_closed(self):
        base = json.loads(inventory_payload())
        cases = []
        for key, value in (
            ("unique_id", "../other"),
            ("status", 1),
            ("deleted", True),
            ("branch_id", 999),
            ("job_role_id", 999),
            ("remote", "false"),
        ):
            payload = json.loads(json.dumps(base))
            payload["jobs"][0][key] = value
            cases.append(payload)
        duplicate = json.loads(json.dumps(base))
        duplicate["jobs"].append(json.loads(json.dumps(duplicate["jobs"][0])))
        cases.append(duplicate)

        for payload in cases:
            with self.subTest(payload=payload["jobs"][-1]):
                result = self.adapter.list_jobs(
                    RoutingFetcher({INVENTORY_URL: json.dumps(payload)}),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_asset_and_inventory_bounds_fail_closed(self):
        oversized_asset = fixture("widget.js") + (" " * 256_001)
        pages = successful_probe_pages()
        pages[ASSET_URL] = Page(url=ASSET_URL, html=oversized_asset)
        self.assertIsNone(self.adapter.probe_board(RoutingFetcher(pages), widget_page()))

        base = json.loads(inventory_payload())
        base["jobs"] = [base["jobs"][0]] * 2_001
        result = self.adapter.list_jobs(
            RoutingFetcher({INVENTORY_URL: json.dumps(base)}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["stop_reason"], "row_cap_exceeded")

    def test_unverified_inventory_and_fetch_failures_are_typed(self):
        for raw in ("not-json", '{"jobs": []}', '<title>Freshteam</title>'):
            with self.subTest(raw=raw):
                result = self.adapter.list_jobs(
                    RoutingFetcher({INVENTORY_URL: raw}), self.board, JobQuery()
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

        failed = self.adapter.list_jobs(
            RoutingFetcher(error=FetchError("timeout")), self.board, JobQuery()
        )
        self.assertEqual(failed.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(failed.retryable)


if __name__ == "__main__":
    unittest.main()
