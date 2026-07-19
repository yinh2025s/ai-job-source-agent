import unittest
from pathlib import Path

from job_source_agent.content_probe import (
    discover_first_party_career_navigation,
    probe_first_party_cms_payload,
)
from job_source_agent.career_search import CareerSearchResult
from job_source_agent.career_candidate_scheduler import candidate_concrete_host
from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.errors import DiscoveryError
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.job_board import JobBoard
from job_source_agent.models import CompanyInput, LinkCandidate, dataclass_to_dict
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page, RawLink


ROOT = Path(__file__).resolve().parents[1]


class OfflinePipelineTests(unittest.TestCase):
    def test_non_production_eightfold_tenant_is_not_promoted_from_embedded_url(self):
        class Adapter:
            name = "eightfold"

        self.assertFalse(
            JobSourceAgent._provider_board_candidate_allowed(
                Adapter(),
                JobBoard(
                    "https://kering-sandbox.eightfold.ai/careers",
                    "eightfold",
                    "kering-sandbox",
                ),
            )
        )
        self.assertTrue(
            JobSourceAgent._provider_board_candidate_allowed(
                Adapter(),
                JobBoard(
                    "https://acme.eightfold.ai/careers",
                    "eightfold",
                    "acme",
                ),
            )
        )
        agent = JobSourceAgent(Fetcher(offline=True))
        self.assertFalse(
            agent._provider_url_candidate_allowed(
                "https://kering-sandbox.eightfold.ai/careers"
            )
        )
        self.assertTrue(
            agent._provider_url_candidate_allowed("https://acme.eightfold.ai/careers")
        )

    def test_non_production_provider_link_is_not_requeued_on_deferred_page_pass(self):
        career = "https://careers.kering.example/careers?filter_house=Brand"
        sandbox = "https://kering-sandbox.eightfold.ai/careers"

        class KeringFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<html><title>Brand jobs</title><main>"
                            "<h1>Open positions</h1>"
                            f'<a href="{sandbox}">Search jobs</a>'
                            "</main></html>"
                        ),
                    )
                raise AssertionError(f"disallowed provider URL was fetched: {url}")

        fetcher = KeringFetcher()
        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(
                fetcher,
                max_job_pages=4,
                max_ats_board_fetches=0,
            ).find_job_board(career)

        self.assertEqual(fetcher.calls, [career])
        self.assertEqual(
            raised.exception.trace["provider_candidate_rejections"],
            [{"url": sandbox, "reason": "non_production_provider_tenant"}],
        )

    def test_visible_first_party_inventory_precedes_talent_community_cta(self):
        career = "https://www.parent.example/talent/job-offers/brand-careers/"
        join = "https://careers.parent.example/careers/join"
        first = f"{career}northern-america/brand-financial-analyst/"
        second = f"{career}europe/brand-pricing-analyst/"

        class InventoryFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<article class="job-card"><h3>Financial Analyst</h3>'
                            f'<a href="{first}">Brand Financial Analyst</a></article>'
                            '<article class="job-card"><h3>Pricing Analyst</h3>'
                            f'<a href="{second}">Brand Pricing Analyst</a></article>'
                            f'<a href="{join}">Can’t find a role that fits right now?</a>'
                        ),
                    )
                raise AssertionError(f"lower-priority CTA was fetched: {url}")

        fetcher = InventoryFetcher()
        job_list_url, trace = JobSourceAgent(
            fetcher,
            max_job_pages=4,
            max_ats_board_fetches=0,
        ).find_job_board(career)

        self.assertEqual(job_list_url, career)
        self.assertEqual(fetcher.calls, [career])
        self.assertEqual(
            trace["selected_from"],
            "verified_first_party_listing_inventory",
        )

    def test_target_region_gateway_follows_one_visible_nested_career_action(self):
        homepage = "https://brand.example"
        gateway = "https://us.brand.example/"
        career = "https://brand.career/"

        class GatewayFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == homepage:
                    return Page(
                        url=url,
                        html=f'<a href="{gateway}">United States</a>',
                    )
                if url.rstrip("/") == gateway.rstrip("/"):
                    return Page(
                        url=url,
                        final_url=gateway,
                        html=f'<a href="{career}">Recruitment &amp; Careers</a>',
                    )
                if url.rstrip("/") == career.rstrip("/"):
                    return Page(
                        url=url,
                        final_url=career,
                        html="<title>Careers</title><main>Open positions and jobs</main>",
                    )
                raise FetchError(f"unexpected speculative path: {url}")

        fetcher = GatewayFetcher()
        selected, trace = JobSourceAgent(
            fetcher,
            max_career_candidate_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
            max_ats_board_fetches=0,
        ).find_career_page(
            homepage,
            company_name="Brand",
            target_location="New York, NY",
        )

        self.assertEqual(selected, career)
        self.assertEqual(trace["selected_from"], "regional_gateway_navigation")
        self.assertEqual(fetcher.calls[:3], [homepage, gateway, career])

    def test_region_gateway_rejects_unlabelled_cross_site_navigation(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        candidate = LinkCandidate(
            "https://unrelated.example/",
            "Learn more",
            "https://us.brand.example/",
            100,
            [],
            origin="page_link",
        )

        self.assertFalse(agent._is_explicit_cross_site_job_portal(candidate))
        self.assertFalse(agent._is_safe_traversal_target(candidate, candidate.source_url))

    def test_first_party_bundle_precedes_speculative_paths_without_visible_career_link(self):
        homepage = "https://bundle.example"
        asset = f"{homepage}/main.js"
        career = "https://opportunities.bundle.example"

        class BundleFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == homepage:
                    return Page(
                        url=url,
                        html=f'<script type="module" src="{asset}"></script>',
                    )
                if url == asset:
                    return Page(
                        url=url,
                        html=f'<a href="{career}">Job Opportunities</a>',
                    )
                if url == career:
                    return Page(url=url, html="<main>Careers and open jobs</main>")
                raise FetchError(f"unexpected speculative path: {url}")

        selected, trace = JobSourceAgent(
            BundleFetcher(offline=True),
            max_career_candidate_fetches=10,
            enable_sitemap_discovery=False,
            enable_career_search=False,
            max_ats_board_fetches=0,
        ).find_career_page(homepage)

        self.assertEqual(selected, career)
        self.assertEqual(trace["selected_from"], "bundle_navigation_discovery")
        self.assertEqual(
            trace["sitemap_discovery"]["reason"],
            "first-party bundle navigation verified before speculative path fanout",
        )

    def test_blind_ats_verification_follows_sitemap_but_precedes_search_fanout(self):
        events = []

        class HomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(url=url, final_url=url, html="<html>Company</html>")

        class OrderedAgent(JobSourceAgent):
            def _select_verified_career_candidate(
                self,
                candidates,
                trace,
                *,
                schedule_source,
                **kwargs,
            ):
                events.append(schedule_source)
                if schedule_source == "blind_ats":
                    trace["selected"] = dataclass_to_dict(candidates[0])
                    return candidates[0].url
                return None

            def _sitemap_candidates(self, *args, **kwargs):
                events.append("sitemap_transport")
                return [], {}

            def _search_career_candidates(self, *args, **kwargs):
                events.append("search_transport")
                return CareerSearchResult(candidates=[], trace={})

        agent = OrderedAgent(HomepageFetcher(offline=True), max_ats_board_fetches=1)

        _career_url, trace = agent.find_career_page(
            "https://acme.example",
            company_name="Acme",
        )

        self.assertEqual(
            events,
            [
                "homepage_and_common_paths",
                "sitemap_transport",
                "sitemap",
                "blind_ats",
            ],
        )
        self.assertEqual(trace["selected_from"], "ats_board_discovery")
        self.assertTrue(trace["search_discovery"]["skipped"])

    def test_blind_ats_candidates_include_registered_pinpoint_tenant_shape(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        candidates = agent._ats_board_candidates("SKIMS", "https://skims.com")

        pinpoint = next(
            item for item in candidates
            if item.url == "https://skims.pinpointhq.com/"
        )
        self.assertIn("derived Pinpoint board candidate", pinpoint.reasons)
        self.assertEqual(pinpoint.origin, "blind_ats_probe")

    def test_official_visible_empty_state_is_terminal_without_company_exception(self):
        career = "https://empty.example/careers"

        class EmptyCareerFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == career:
                    return Page(
                        url=url,
                        final_url=career,
                        html=(
                            "<html><h1>Open positions</h1>"
                            "<p>No open positions available at the moment.</p></html>"
                        ),
                        source="official-career-page",
                    )
                raise FetchError(f"unexpected URL: {url}")

        agent = JobSourceAgent(
            EmptyCareerFetcher(offline=True),
            max_job_pages=1,
            max_ats_board_fetches=0,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career)

        self.assertEqual(raised.exception.code, "NO_PUBLIC_OPENINGS")
        self.assertEqual(
            raised.exception.trace["explicit_empty_inventory"]["phrase"],
            "No open positions available at the moment",
        )

    def test_hidden_or_script_empty_copy_is_not_authoritative(self):
        career = "https://not-empty.example/careers"

        class ScriptOnlyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=career,
                    html=(
                        "<html><h1>Careers</h1>"
                        '<div hidden>No open positions available at the moment.</div>'
                        '<script>const empty = "There are currently no open jobs";</script>'
                        "</html>"
                    ),
                )

        agent = JobSourceAgent(
            ScriptOnlyFetcher(offline=True),
            max_job_pages=1,
            max_ats_board_fetches=0,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career)

        self.assertEqual(raised.exception.code, "job_board_not_found")
        self.assertNotIn("explicit_empty_inventory", raised.exception.trace)

    def test_unverified_career_candidates_report_retryable_budget_exhaustion(self):
        homepage = "https://budget.example"

        class HomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html='<html><a href="/careers">Careers</a></html>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        agent = JobSourceAgent(
            HomepageFetcher(offline=True),
            max_career_candidate_fetches=0,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(
            raised.exception.trace["candidate_fetch_budget_exhausted"]["limit"],
            0,
        )

    def test_speculative_candidate_truncation_reports_career_page_not_found(self):
        homepage = "https://speculative.example"

        class SpeculativeFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(url=url, final_url=homepage, html="<html>Company</html>")
                raise FetchError(f"fixture miss: {url}")

        agent = JobSourceAgent(
            SpeculativeFetcher(offline=True),
            max_candidates=6,
            max_career_candidate_fetches=5,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "career_page_not_found")
        self.assertNotIn("candidate_fetch_budget_exhausted", raised.exception.trace)

    def test_official_career_forbidden_is_not_reported_as_not_found(self):
        homepage = "https://forbidden.example"
        career = f"{homepage}/careers"

        class ForbiddenFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<a href="{career}">Careers</a>',
                    )
                if url.rstrip("/") == career:
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not found", reason_code="HTTP_NOT_FOUND")

        agent = JobSourceAgent(
            ForbiddenFetcher(offline=True),
            max_career_candidate_fetches=8,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "HTTP_FORBIDDEN")

    def test_repeated_official_host_denials_are_not_reported_as_not_found(self):
        homepage = "https://blocked.example"

        class DeniedPathsFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None, *, interaction=None):
                if url.rstrip("/") == homepage:
                    return Page(url=url, final_url=homepage, html="<html>Company</html>")
                if candidate_concrete_host(url) == "blocked.example":
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not found", reason_code="HTTP_NOT_FOUND")

        agent = JobSourceAgent(
            CareerTransportBudgetFetcher(DeniedPathsFetcher(offline=True)),
            max_career_candidate_fetches=5,
            max_career_discovery_transport_calls=12,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "HTTP_FORBIDDEN")
        self.assertLess(
            raised.exception.trace["transport_budget"]["dispatched"],
            6,
        )
        self.assertTrue(raised.exception.trace["official_host_denial_skips"])

    def test_repeated_official_denial_prunes_first_party_fanout_but_keeps_provider_routes(self):
        homepage = "https://denied.example"
        provider = "https://jobs.lever.co/denied"

        class DeniedFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.official_calls = []

            def fetch(self, url, data=None, headers=None):
                if candidate_concrete_host(url).removeprefix("www.") == "denied.example":
                    self.official_calls.append(url)
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not found", reason_code="HTTP_NOT_FOUND")

        class ProviderRouteAgent(JobSourceAgent):
            def __init__(self, fetcher):
                super().__init__(
                    fetcher,
                    max_career_candidate_fetches=8,
                    max_ats_board_fetches=1,
                )
                self.search_ats_only = None
                self.blind_ats_checked = False

            def _select_verified_career_candidate(self, candidates, trace, *, schedule_source, **kwargs):
                if schedule_source == "blind_ats":
                    self.blind_ats_checked = True
                    return None
                if schedule_source == "search":
                    self.assert_search_candidates = list(candidates)
                    return provider
                return super()._select_verified_career_candidate(
                    candidates,
                    trace,
                    schedule_source=schedule_source,
                    **kwargs,
                )

            def _search_career_candidates(self, company_name, homepage_url, *, ats_only=False):
                self.search_ats_only = ats_only
                return CareerSearchResult(
                    candidates=[
                        LinkCandidate(
                            provider,
                            "Jobs",
                            homepage_url,
                            500,
                            [],
                            "search_result",
                        ),
                        LinkCandidate(
                            "https://careers.unrelated.example/jobs",
                            "Jobs",
                            homepage_url,
                            500,
                            [],
                            "search_result",
                        ),
                    ],
                    trace={},
                )

        fetcher = DeniedFetcher()
        agent = ProviderRouteAgent(fetcher)

        career_url, trace = agent.find_career_page(homepage, company_name="Denied")

        self.assertEqual(career_url, provider)
        self.assertTrue(agent.blind_ats_checked)
        self.assertFalse(agent.search_ats_only)
        self.assertEqual(
            [candidate.url for candidate in agent.assert_search_candidates],
            [provider],
        )
        self.assertEqual(trace["selected_from"], "search_discovery")
        self.assertEqual(
            trace["sitemap_discovery"]["reason"],
            "repeated deterministic denial on official host",
        )
        self.assertEqual(
            trace["search_discovery"]["official_host_denial_policy"]["search_scope"],
            "ats_or_same_official_site",
        )
        self.assertEqual(len(fetcher.official_calls), 2)
        self.assertTrue(trace["official_host_denial_skips"])

    def test_identity_career_root_survives_existing_official_host_denial(self):
        homepage = "https://denied.example"
        external_apply = "https://apply.example/jobs"

        class ExternalApplyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == external_apply:
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><title>Careers</title><h1>Careers</h1><p>Open jobs</p></html>",
                    )
                raise FetchError(f"unexpected URL: {url}")

        trace = {
            "candidate_fetch_errors": [
                {"url": homepage, "reason_code": "HTTP_FORBIDDEN"},
                {"url": f"{homepage}/careers", "reason_code": "HTTP_FORBIDDEN"},
            ]
        }
        candidate = LinkCandidate(
            external_apply,
            "Apply",
            homepage,
            500,
            ["identity-supplied career root requiring verification"],
            "identity_career_root",
        )
        agent = JobSourceAgent(ExternalApplyFetcher(offline=True))

        selected = agent._select_verified_career_candidate(
            [candidate],
            trace,
            homepage_url=homepage,
        )

        self.assertEqual(selected, external_apply)
        self.assertNotIn("official_host_denial_skips", trace)

    def test_login_required_precedes_forbidden_for_repeated_official_denial(self):
        homepage = "https://login.example"

        class LoginFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    raise FetchError(
                        "authentication required",
                        status=401,
                        reason_code="LOGIN_REQUIRED",
                        retryable=False,
                    )
                if candidate_concrete_host(url).removeprefix("www.") == "login.example":
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not found", reason_code="HTTP_NOT_FOUND")

        agent = JobSourceAgent(
            LoginFetcher(offline=True),
            max_career_candidate_fetches=8,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "LOGIN_REQUIRED")
        self.assertTrue(raised.exception.trace["official_host_denial_skips"])

    def test_caller_deadline_precedes_not_found_and_fetch_budget_projection(self):
        homepage = "https://deadline.example"
        career = f"{homepage}/careers"

        class DeadlineFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<a href="{career}">Careers</a>',
                    )
                raise FetchError(
                    "operation timed out at caller deadline",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                )

        agent = JobSourceAgent(
            DeadlineFetcher(offline=True),
            max_career_candidate_fetches=1,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(
            raised.exception.code,
            "COMPANY_TIME_BUDGET_EXHAUSTED",
        )

    def test_explicit_offline_fixture_gap_survives_career_failure_aggregation(self):
        homepage = "https://fixture-gap.example"
        career = f"{homepage}/careers"

        class FixtureGapFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<html><a href="{career}">Careers</a></html>',
                    )
                raise FetchError(
                    f"No fixture found for {url}",
                    reason_code="OFFLINE_FIXTURE_MISSING",
                    retryable=False,
                )

        agent = JobSourceAgent(
            FixtureGapFetcher(offline=True),
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "OFFLINE_FIXTURE_MISSING")
        failure = raised.exception.trace["candidate_fetch_errors"][0]
        self.assertEqual(failure["reason_code_source"], "exception")

    def test_explicit_offline_fixture_gap_survives_job_board_aggregation(self):
        career = "https://fixture-gap.example/careers"

        class FixtureGapFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                raise FetchError(
                    f"No fixture found for {url}",
                    reason_code="OFFLINE_FIXTURE_MISSING",
                    retryable=False,
                )

        agent = JobSourceAgent(
            FixtureGapFetcher(offline=True),
            max_job_pages=1,
            max_ats_board_fetches=0,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career)

        self.assertEqual(raised.exception.code, "OFFLINE_FIXTURE_MISSING")
        failure = raised.exception.trace["fetch_errors"][0]
        self.assertEqual(failure["reason_code_source"], "exception")

    def test_verified_career_root_timeout_remains_retryable(self):
        career = "https://transient.example/careers"

        class TransientCareerRootFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                raise FetchError(
                    "The read operation timed out",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                )

        agent = JobSourceAgent(
            TransientCareerRootFetcher(offline=True),
            max_job_pages=1,
            max_ats_board_fetches=0,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career)

        self.assertEqual(raised.exception.code, "NETWORK_TIMEOUT")
        failure = raised.exception.trace["fetch_errors"][0]
        self.assertEqual(failure["url"], career)
        self.assertEqual(failure["origin"], "verified_career_page")
        self.assertEqual(failure["evidence_tier"], 0)
        self.assertTrue(failure["retryable"])

    def test_provider_career_entry_is_canonicalized_to_listing_board(self):
        career = "https://www.google.com/about/careers/applications/"

        job_list_url, trace = JobSourceAgent(Fetcher(offline=True)).find_job_board(
            career
        )

        self.assertEqual(
            job_list_url,
            "https://www.google.com/about/careers/applications/jobs/results/",
        )
        self.assertEqual(trace["career_page_url"], career)
        self.assertEqual(trace["job_list_page_url"], job_list_url)

    def test_evidence_backed_candidate_timeout_remains_retryable(self):
        homepage = "https://transient.example"
        career = f"{homepage}/about/careers"

        class TransientCareerFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<html><a href="{career}">Careers</a></html>',
                    )
                if url.rstrip("/") == career:
                    raise FetchError(
                        "The read operation timed out",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                    )
                raise FetchError(f"fixture miss: {url}")

        agent = JobSourceAgent(
            TransientCareerFetcher(offline=True),
            max_candidates=6,
            max_career_candidate_fetches=5,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "NETWORK_TIMEOUT")
        error = raised.exception.trace["candidate_fetch_errors"][0]
        self.assertEqual(error["url"], career)
        self.assertEqual(error["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(error["retryable"])
        self.assertEqual(error["origin"], "page_link")
        self.assertEqual(error["evidence_tier"], 1)

    def test_evidence_backed_job_board_caller_deadline_is_budget_exhaustion(self):
        career = "https://transient.example/careers"
        job_list = "https://transient.example/search-results"

        class TransientJobBoardFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == career:
                    return Page(
                        url=url,
                        final_url=career,
                        html=f'<html><a href="{job_list}">Search jobs</a></html>',
                    )
                if url.rstrip("/") == job_list:
                    raise FetchError(
                        "operation timed out at caller deadline",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                    )
                raise FetchError(f"fixture miss: {url}")

        agent = JobSourceAgent(
            TransientJobBoardFetcher(offline=True),
            max_job_pages=2,
            max_ats_board_fetches=0,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career)

        self.assertEqual(raised.exception.code, "COMPANY_TIME_BUDGET_EXHAUSTED")
        failure = raised.exception.trace["fetch_errors"][0]
        self.assertEqual(failure["url"], job_list)
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(failure["retryable"])
        self.assertEqual(failure["origin"], "page_link")
        self.assertLessEqual(failure["evidence_tier"], 2)

    def test_untried_page_link_reports_fetch_budget_exhausted(self):
        homepage = "https://evidence-budget.example"
        first = f"{homepage}/careers-primary"
        second = f"{homepage}/careers-secondary"

        class EvidenceBudgetFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=(
                            f'<a href="{first}">Careers</a>'
                            f'<a href="{second}">Careers</a>'
                        ),
                    )
                raise FetchError(f"fixture miss: {url}")

        agent = JobSourceAgent(
            EvidenceBudgetFetcher(offline=True),
            max_candidates=6,
            max_career_candidate_fetches=1,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage)

        self.assertEqual(raised.exception.code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(
            raised.exception.trace["candidate_fetch_budget_exhausted"][
                "untried_evidence_backed_count"
            ],
            1,
        )

    def test_labeled_first_party_bundle_navigation_recovers_career_without_sitemap(self):
        homepage = "https://bundle.example"
        asset = homepage + "/assets/index-app.js"
        career = homepage + "/company/careers"
        payload = (
            "https://magnolia-public.bundle.example/.rest/delivery/marketing-pages/v1/"
            "bundle-site/company/careers"
        )
        workday = "https://bundle.wd5.myworkdayjobs.com/bundle-careers"
        bundle = (
            'const nav=[{href:"/company/about",children:"About"},'
            '{href:"/company/careers",children:"Careers"}];'
            'const endpoint="/.rest/delivery/marketing-pages/v1";'
            'const cms="https://magnolia-public.bundle.example";'
            'const base=sessionStorage.getItem("appBase")||"/bundle-site";'
        )

        class BundleFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<script type="module" src="{asset}"></script>',
                    )
                if url == asset:
                    return Page(url=url, final_url=url, html=bundle, source="public-js")
                if url.rstrip("/") == career:
                    return Page(
                        url=url,
                        final_url=career,
                        html=f'<script type="module" src="{asset}"></script>',
                    )
                if url == payload:
                    return Page(
                        url=url,
                        final_url=url,
                        html=f'<h1>Careers</h1><script>const board="{workday}";</script>',
                        source="magnolia-public",
                    )
                raise FetchError(f"not available: {url}")

        fetcher = BundleFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_career_candidate_fetches=2,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page(homepage, company_name="Bundle")

        self.assertEqual(career_url, career)
        self.assertEqual(trace["selected_from"], "bundle_navigation_discovery")
        self.assertEqual(
            trace["bundle_navigation_discovery"]["candidate_urls"],
            [career],
        )

    def test_labeled_bundle_route_verifies_client_rendered_career_shell(self):
        homepage = "https://spa.example"
        asset = homepage + "/main.js"
        career = homepage + "/careers"

        class BundleShellFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<script type="module" src="{asset}"></script>',
                    )
                if url == asset:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            'const attrs=["routerLink","/careers",'
                            '"aria-label","Go to careers page"];'
                        ),
                        source="public-js",
                    )
                if url.rstrip("/") == career:
                    return Page(
                        url=url,
                        final_url=career,
                        html=(
                            '<base href="/"><app-root></app-root>'
                            f'<script type="module" src="{asset}"></script>'
                        ),
                        source="client-shell",
                    )
                raise FetchError(f"not available: {url}")

        career_url, trace = JobSourceAgent(
            BundleShellFetcher(offline=True),
            max_career_candidate_fetches=2,
            max_ats_board_fetches=0,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        ).find_career_page(homepage, company_name="SPA")

        self.assertEqual(career_url, career)
        self.assertEqual(trace["selected_from"], "bundle_navigation_discovery")
        self.assertEqual(
            trace["selected_route_evidence"],
            "first_party_bundle_navigation",
        )

    def test_bundle_navigation_requires_labeled_same_origin_career_route(self):
        homepage = "https://bundle.example"
        asset = homepage + "/assets/index.js"

        class BundleFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == asset:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '{href:"/company/careers",children:"Products"},'
                            '{href:"https://other.example/careers",children:"Careers"}'
                        ),
                    )
                raise FetchError("unexpected")

        page = Page(
            url=homepage,
            html=f'<script type="module" src="{asset}"></script>',
        )

        candidates, trace = discover_first_party_career_navigation(BundleFetcher(), page)

        self.assertEqual(candidates, [])
        self.assertEqual(trace["candidate_urls"], [])

    def test_sample_jobs_discover_successfully(self):
        companies = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company) for company in companies]

        self.assertEqual([result.status for result in results], ["success", "success"])
        self.assertEqual(results[0].career_page_url, "https://jobs.lever.co/aurora-data")
        self.assertIn("d9d64766", results[0].open_position_url)
        self.assertEqual(results[1].career_page_url, "https://nimbus-robotics.example/careers")
        self.assertIn("5012345001", results[1].open_position_url)

    def test_provider_root_uses_linkedin_title_for_opening_match(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        company = CompanyInput(
            company_name="Google",
            company_website_url="https://www.google.com",
            career_root_url="https://www.google.com/about/careers/applications/",
            job_title="Product Manager, Ads",
        )

        result = agent.discover(company)

        self.assertEqual(result.status, "success")
        self.assertIn("123-product-manager-ads", result.open_position_url)

    def test_brand_join_path_can_be_discovered(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        career_url, trace = agent.find_career_page("https://brandedjoin.example")

        self.assertEqual(career_url, "https://brandedjoin.example/join-brandedjoin")
        self.assertIn("brand-specific join path", trace["selected"]["reasons"])

    def test_target_title_prevents_unrelated_opening_match(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        opening_url, job_list_url, trace = agent.find_open_position(
            "https://jobs.lever.co/titlefilter",
            target_title="AI Engineer",
        )

        self.assertIsNone(opening_url)
        self.assertEqual(job_list_url, "https://jobs.lever.co/titlefilter")
        self.assertEqual(trace["opening_error"], "specific_opening_not_found")

    def test_find_open_position_passes_location_to_board_discovery(self):
        class CapturingAgent(JobSourceAgent):
            def __init__(self):
                super().__init__(Fetcher(offline=True))
                self.board_location = None

            def find_job_board_with_evidence(
                self,
                career_page_url,
                company_name=None,
                target_location=None,
            ):
                self.board_location = target_location
                return career_page_url, {}, None

            def _match_opening(
                self,
                job_list_url,
                target_title,
                target_location,
                discovered_board=None,
            ):
                return None, job_list_url, {}

        agent = CapturingAgent()

        agent.find_open_position(
            "https://acme.example/careers",
            target_location="Brussels, Belgium",
        )

        self.assertEqual(agent.board_location, "Brussels, Belgium")

    def test_rippling_board_is_not_mistaken_for_a_job_detail(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        opening_url, job_list_url, trace = agent.find_open_position(
            "https://ats.rippling.com/embed/acme-rippling/jobs",
            target_title="Data Analyst",
        )

        self.assertIn("b4f5c9d3", opening_url)
        self.assertEqual(job_list_url, "https://ats.rippling.com/embed/acme-rippling/jobs")
        self.assertEqual(trace["selected"]["provider"], "rippling")

    def test_provider_board_is_kept_when_its_page_contains_only_assets(self):
        class WorkdayAssetFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == "https://example.com/careers":
                    return Page(
                        url=url,
                        final_url=url,
                        html='<a href="https://tenant.wd1.myworkdayjobs.com/acme">Search jobs</a>',
                    )
                if url == "https://tenant.wd1.myworkdayjobs.com/acme":
                    return Page(
                        url=url,
                        final_url=url,
                        html='<img src="https://tenant.wd1.myworkdayjobs.com/acme/assets/logo">',
                    )
                if "/wday/cxs/" in url:
                    raise FetchError("no matching job")
                raise FetchError(f"unexpected URL: {url}")

        agent = JobSourceAgent(WorkdayAssetFetcher(offline=True), max_job_pages=3)

        opening_url, job_list_url, _trace = agent.find_open_position(
            "https://example.com/careers",
            target_title="Data Analyst",
        )

        self.assertIsNone(opening_url)
        self.assertEqual(job_list_url, "https://tenant.wd1.myworkdayjobs.com/acme")

    def test_career_page_can_be_discovered_from_search_fallback(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=4,
            enable_sitemap_discovery=False,
        )

        career_url, trace = agent.find_career_page(
            "https://searchfallback.example",
            company_name="Search Fallback",
        )

        self.assertEqual(career_url, "https://searchfallback.example/real-careers")
        self.assertEqual(trace["selected_from"], "search_discovery")

    def test_general_role_search_penalizes_early_career_board(self):
        homepage = "https://example.test"
        early = "https://tenant.wd1.myworkdayjobs.com/Example_Early_Careers"
        general = "https://careers.smartrecruiters.com/Example"

        class SearchPolicyAgent(JobSourceAgent):
            def __init__(self):
                super().__init__(
                    Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
                    enable_sitemap_discovery=False,
                )
                self.search_candidates = []

            def _search_career_candidates(self, *args, **kwargs):
                return CareerSearchResult(
                    candidates=[
                        LinkCandidate(early, early, homepage, 550, [], "search_result"),
                        LinkCandidate(general, general, homepage, 510, [], "search_result"),
                    ],
                    trace={"candidates": []},
                )

            def _select_verified_career_candidate(
                self,
                candidates,
                trace,
                *,
                schedule_source,
                **kwargs,
            ):
                if schedule_source != "search":
                    return None
                self.search_candidates = list(candidates)
                trace["selected"] = dataclass_to_dict(candidates[0])
                return candidates[0].url

        agent = SearchPolicyAgent()
        career_url, trace = agent.find_career_page(
            homepage,
            company_name="Example",
            target_title="Data Scientist",
        )

        self.assertEqual(career_url, general)
        self.assertEqual(trace["selected_from"], "search_discovery")
        self.assertEqual(agent.search_candidates[1].url, early)
        self.assertIn(
            "career audience mismatch: early-career",
            agent.search_candidates[1].reasons,
        )

    def test_career_page_can_be_discovered_from_derived_ats_board(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=4,
            max_career_candidate_fetches=2,
            max_ats_board_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page(
            "https://atsprobe.example",
            company_name="ATS Probe",
            target_title="Data Analyst",
        )

        self.assertEqual(career_url, "https://jobs.lever.co/atsprobe")
        self.assertEqual(trace["selected_from"], "ats_board_discovery")
        self.assertIn("derived Lever board candidate", trace["selected"]["reasons"])
        self.assertIn(
            "page_job_links",
            [
                item["method"]
                for item in trace["ats_board_discovery"]["provider_board_verification"]
            ],
        )

    def test_unverified_derived_ats_board_is_rejected(self):
        class EmptyAshbyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "api.ashbyhq.com" in url:
                    raise FetchError("HTTP Error 404: Not Found")
                return Page(url=url, final_url=url, html="<html><body>Ashby</body></html>")

        agent = JobSourceAgent(EmptyAshbyFetcher(offline=True), max_ats_board_fetches=1)
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    "https://jobs.ashbyhq.com/missing",
                    "",
                    "https://missing.example",
                    180,
                    ["derived Ashby board candidate"],
                )
            ],
            trace,
            max_fetches=1,
        )

        self.assertIsNone(selected)
        self.assertEqual(
            trace["provider_board_verification"][0]["method"],
            "native_adapter_first",
        )
        self.assertEqual(
            trace["candidate_fetch_errors"][0]["error"],
            "derived provider adapter rejected tenant or title",
        )

    def test_common_path_candidates_include_www_variant(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=80,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page("https://wwwvariant.example")

        self.assertEqual(career_url, "https://www.wwwvariant.example/careers")
        self.assertEqual(trace["selected"]["url"], "https://www.wwwvariant.example/careers")

    def test_team_path_requires_strong_employment_evidence(self):
        homepage = "https://startup.example"
        team = f"{homepage}/team"

        class TeamFetcher(Fetcher):
            def __init__(self, team_html):
                super().__init__(offline=True)
                self.team_html = team_html

            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html='<html><title>Startup</title><a href="/team">Team</a></html>',
                    )
                if url.rstrip("/") == team:
                    return Page(url=url, final_url=team, html=self.team_html)
                raise FetchError("not this route")

        accepted, trace = JobSourceAgent(
            TeamFetcher(
                '<html><title>Our Team</title><body>Join our team to build useful products. '
                '<a href="https://jobs.ashbyhq.com/startup">View Job Openings</a></body></html>'
            ),
            max_career_candidate_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        ).find_career_page(homepage, company_name="Startup")
        with self.assertRaises(DiscoveryError):
            JobSourceAgent(
                TeamFetcher(
                    "<html><title>Our Team</title><body>Meet our leadership team.</body></html>"
                ),
                max_career_candidate_fetches=3,
                enable_sitemap_discovery=False,
                enable_career_search=False,
            ).find_career_page(homepage, company_name="Startup")

        self.assertEqual(accepted, team)
        self.assertIn(
            "homepage team link requiring employment evidence",
            trace["selected"]["reasons"],
        )

    def test_magnolia_spa_payload_exposes_career_page_and_provider_board(self):
        homepage = "https://magnolia.example"
        career = f"{homepage}/company/careers"
        asset = f"{homepage}/js/index.js"
        payload = (
            "https://magnolia-public.example.cloud/.rest/delivery/marketing-pages/v1"
            "/example-site/company/careers"
        )
        workday = "https://acme.wd5.myworkdayjobs.com/en-US/acme"

        class MagnoliaFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                normalized = url.rstrip("/")
                if normalized == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html='<html><a href="/company/careers">Careers</a></html>',
                    )
                if normalized == career:
                    return Page(
                        url=url,
                        final_url=career,
                        html=(
                            '<html><div id="root"></div><script type="module" '
                            'src="/js/index.js"></script></html>'
                        ),
                    )
                if normalized == asset:
                    return Page(
                        url=url,
                        final_url=asset,
                        html=(
                            'production:{base:"https://magnolia-public.example.cloud"};'
                            'const endpoint="/.rest/delivery/marketing-pages/v1";'
                            'sessionStorage.getItem("appBase")||"/example-site"'
                        ),
                    )
                if normalized == payload:
                    return Page(
                        url=url,
                        final_url=payload,
                        html=f'{{"label":"Open positions","url":"{workday}"}}',
                    )
                if normalized == workday:
                    return Page(url=url, final_url=workday, html="<html>Workday jobs</html>")
                raise FetchError(f"not this route: {url}")

        agent = JobSourceAgent(
            MagnoliaFetcher(offline=True),
            max_career_candidate_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, career_trace = agent.find_career_page(homepage, company_name="Acme")
        board_url, board_trace = agent.find_job_board(career_url, company_name="Acme")

        self.assertEqual(career_url, career)
        self.assertEqual(board_url, workday)
        self.assertEqual(
            career_trace["content_payload_probes"][0]["method"],
            "magnolia_delivery",
        )
        self.assertEqual(board_trace["content_payload_probes"][0]["payload_url"], payload)

    def test_magnolia_probe_rejects_cross_site_module_asset(self):
        class CrossSiteAssetFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                raise AssertionError("cross-site module asset must not be fetched")

        fetcher = CrossSiteAssetFetcher()
        page = Page(
            url="https://acme.example/careers",
            final_url="https://acme.example/careers",
            html='<script type="module" src="https://cdn.unrelated.example/app.js"></script>',
        )

        enriched, trace = probe_first_party_cms_payload(fetcher, page)

        self.assertIs(enriched, page)
        self.assertIsNone(trace)
        self.assertEqual(fetcher.requested, [])

    def test_common_path_candidates_include_localized_us_paths(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=80,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page("https://localized.example")

        self.assertEqual(career_url, "https://localized.example/en-us/careers")
        self.assertEqual(trace["selected"]["url"], "https://localized.example/en-us/careers")

    def test_localized_paths_include_nested_company_careers_route(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        paths = agent._locale_career_paths("/en/company/overview")

        self.assertIn("/en/company/careers", paths)

    def test_localized_company_careers_path_is_prioritized(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        candidate = agent._score_career_candidate(
            RawLink(
                "https://example.com/en/company/careers",
                "",
                "https://example.com/en",
                "path_probe",
            )
        )

        self.assertIn("localized career section", candidate.reasons)
        self.assertGreater(candidate.score, 240)

    def test_explicit_homepage_job_results_route_survives_same_site_redirect(self):
        homepage = "https://routes.example"
        listed_route = f"{homepage}/en-us/careers/job-results"
        redirected_route = f"{homepage}/en/careers/job-results-global?isRedirected=true"

        class JobResultsFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=f'<a href="{listed_route}">Search jobs</a>',
                    )
                if url == listed_route:
                    return Page(
                        url=url,
                        final_url=redirected_route,
                        html='<html><body><div id="root"></div></body></html>',
                    )
                raise FetchError("not this route")

        career_url, trace = JobSourceAgent(
            JobResultsFetcher(offline=True),
            max_career_candidate_fetches=1,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        ).find_career_page(
            homepage,
            company_name="Routes",
            target_location="United States",
        )

        self.assertEqual(career_url, redirected_route)
        self.assertIn("explicit job-list route", trace["selected"]["reasons"])
        self.assertNotIn("generated path probe", trace["selected"]["reasons"])

    def test_https_homepage_upgrades_explicit_same_site_http_career_link(self):
        homepage = "https://secure.example"
        candidate = JobSourceAgent(Fetcher(offline=True))._score_career_candidate(
            RawLink(
                url="http://www.secure.example/en-us/careers/job-results",
                text="Search jobs",
                source_url=homepage,
                origin="page_link",
            ),
            homepage,
            target_location="United States",
        )

        self.assertEqual(
            candidate.url,
            "https://www.secure.example/en-us/careers/job-results",
        )
        self.assertIn("upgraded observed HTTP link to HTTPS", candidate.reasons)

    def test_https_homepage_upgrades_observed_exact_http_ats_anchor(self):
        homepage = "https://www.aperia.com/"
        candidate = JobSourceAgent(Fetcher(offline=True))._score_career_candidate(
            RawLink(
                url="http://job-boards.greenhouse.io/aperiasolutions",
                text="Join Our Team",
                source_url=homepage,
                origin="page_link",
            ),
            homepage,
        )

        self.assertEqual(
            candidate.url,
            "https://job-boards.greenhouse.io/aperiasolutions",
        )
        self.assertIn("upgraded observed HTTP link to HTTPS", candidate.reasons)

    def test_does_not_upgrade_untrusted_cross_site_http_link(self):
        homepage = "https://www.aperia.com/"
        agent = JobSourceAgent(Fetcher(offline=True))
        for url in (
            "http://evil.job-boards.greenhouse.io/aperiasolutions",
            "http://user:secret@job-boards.greenhouse.io/aperiasolutions",
            "http://job-boards.greenhouse.io:8080/aperiasolutions",
            "http://unrelated.example/jobs",
        ):
            with self.subTest(url=url):
                candidate = agent._score_career_candidate(
                    RawLink(
                        url=url,
                        text="Join Our Team",
                        source_url=homepage,
                        origin="page_link",
                    ),
                    homepage,
                )
                self.assertEqual(candidate.url, url)

    def test_job_board_stops_after_first_verified_first_party_listing_route(self):
        career = "https://routes.example/en/careers"
        us_listing = "https://routes.example/en-us/careers/job-results"
        other_listing = "https://routes.example/en-be/careers/job-results"

        class RegionalListingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<a href="http://routes.example/en-us/careers/job-results">US jobs</a>'
                            f'<a href="{other_listing}">Belgium jobs</a>'
                        ),
                    )
                if url == us_listing:
                    return Page(
                        url=url,
                        final_url=url,
                        html='<html><body><div id="jobs"></div></body></html>',
                    )
                if url == other_listing:
                    raise AssertionError("later regional listing must not overwrite the first verified route")
                raise FetchError("not this route")

        job_list_url, trace = JobSourceAgent(
            RegionalListingFetcher(offline=True),
            max_job_pages=4,
            max_ats_board_fetches=0,
        ).find_job_board(career)

        self.assertEqual(job_list_url, us_listing)
        self.assertEqual(trace["selected_from"], "explicit_first_party_listing_route")
        self.assertEqual([page["url"] for page in trace["pages_visited"]], [career, us_listing])

    def test_job_board_excludes_conflicting_region_and_preserves_matching_timeout(self):
        career = "https://routes.example/careers"
        us_listing = "https://routes.example/en-us/careers/job-results"
        belgium_listing = "https://routes.example/en-be/careers/job-results"

        class RegionalTimeoutFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            f'<a href="{us_listing}">US jobs</a>'
                            f'<a href="{belgium_listing}">Belgium jobs</a>'
                        ),
                    )
                if url == us_listing:
                    raise FetchError(
                        "regional listing timed out",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                    )
                if url == belgium_listing:
                    raise AssertionError("conflicting regional board must not be fetched")
                raise FetchError(f"not this route: {url}")

        fetcher = RegionalTimeoutFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_job_pages=4,
            max_ats_board_fetches=0,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career, target_location="United States")

        self.assertEqual(raised.exception.code, "NETWORK_TIMEOUT")
        self.assertEqual(fetcher.urls, [career, us_listing])
        conflicting = next(
            item
            for item in raised.exception.trace["candidates"]
            if item["url"] == belgium_listing
        )
        self.assertIn(
            "conflicts with target location region 'us': 'be'",
            conflicting["reasons"],
        )

    def test_job_board_uses_neutral_fallback_after_matching_region_timeout(self):
        career = "https://routes.example/careers"
        us_listing = "https://routes.example/en-us/careers/job-results"
        neutral_listing = "https://routes.example/careers/job-results"

        class NeutralFallbackFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            f'<a href="{neutral_listing}">All jobs</a>'
                            f'<a href="{us_listing}">US jobs</a>'
                        ),
                    )
                if url == us_listing:
                    raise FetchError(
                        "regional listing timed out",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                    )
                if url == neutral_listing:
                    return Page(
                        url=url,
                        final_url=url,
                        html='<html><body><div id="jobs"></div></body></html>',
                    )
                raise FetchError(f"not this route: {url}")

        fetcher = NeutralFallbackFetcher()
        job_list_url, trace = JobSourceAgent(
            fetcher,
            max_job_pages=4,
            max_ats_board_fetches=0,
        ).find_job_board(career, target_location="United States")

        self.assertEqual(job_list_url, neutral_listing)
        self.assertEqual(fetcher.urls, [career, us_listing, neutral_listing])
        self.assertEqual(trace["target_region"], "us")

    def test_cross_region_career_hub_discovers_matching_workday_board(self):
        career = "https://regional.example/en-au/careers"
        us_board = "https://regional.wd5.myworkdayjobs.com/en-US/careers"

        class RegionalHubFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=f'<a href="{us_board}">Search US jobs</a>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = RegionalHubFetcher()
        job_list_url, trace = JobSourceAgent(
            fetcher,
            max_job_pages=3,
            max_ats_board_fetches=0,
        ).find_job_board(career, target_location="United States")

        self.assertEqual(job_list_url, us_board)
        self.assertEqual(fetcher.urls, [career])
        self.assertEqual(trace["provider"], "workday")
        self.assertEqual(trace["target_region"], "us")

    def test_cross_region_career_hub_is_inspected_but_not_promoted(self):
        career = "https://regional.example/en-au/careers"
        au_board = "https://regional.wd5.myworkdayjobs.com/en-AU/careers"
        au_listing = "https://regional.example/en-au/careers/jobs"

        class ConflictingRegionalHubFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            f'<a href="{au_board}">Search Australia jobs</a>'
                            f'<a href="{au_listing}">View all Australia jobs</a>'
                        ),
                    )
                raise AssertionError("conflicting regional candidates must not be fetched")

        fetcher = ConflictingRegionalHubFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_job_pages=3,
            max_ats_board_fetches=0,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_job_board(career, target_location="United States")

        self.assertEqual(raised.exception.code, "job_board_not_found")
        self.assertEqual(fetcher.urls, [career])
        trace = raised.exception.trace
        self.assertEqual(trace["pages_visited"][0]["url"], career)
        self.assertIsNone(trace["job_list_page_url"])
        self.assertNotIn("selected", trace)
        self.assertEqual(
            {item["url"] for item in trace["regional_exclusions"]},
            {au_board, au_listing},
        )

    def test_job_board_without_location_preserves_first_regional_candidate(self):
        career = "https://routes.example/careers"
        belgium_listing = "https://routes.example/en-be/careers/job-results"
        us_listing = "https://routes.example/en-us/careers/job-results"

        class LegacyRegionalFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == career:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            f'<a href="{belgium_listing}">Belgium jobs</a>'
                            f'<a href="{us_listing}">US jobs</a>'
                        ),
                    )
                if url == belgium_listing:
                    return Page(
                        url=url,
                        final_url=url,
                        html='<html><body><div id="jobs"></div></body></html>',
                    )
                if url == us_listing:
                    raise AssertionError("legacy order should stop at the first verified board")
                raise FetchError(f"not this route: {url}")

        fetcher = LegacyRegionalFetcher()
        job_list_url, trace = JobSourceAgent(
            fetcher,
            max_job_pages=4,
            max_ats_board_fetches=0,
        ).find_job_board(career)

        self.assertEqual(job_list_url, belgium_listing)
        self.assertEqual(fetcher.urls, [career, belgium_listing])
        self.assertNotIn("target_region", trace)

    def test_verified_career_listing_route_is_checked_once_for_page_provider(self):
        listing = "https://routes.example/en-us/careers/job-results"

        class ListingFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                return Page(
                    url=url,
                    final_url=url,
                    html='<html><body><div id="jobs"></div></body></html>',
                )

        fetcher = ListingFetcher()
        job_list_url, trace = JobSourceAgent(fetcher).find_job_board(listing)

        self.assertEqual(job_list_url, listing)
        self.assertEqual(trace["selected_from"], "explicit_first_party_listing_route")
        self.assertEqual(fetcher.urls, [listing])

    def test_career_candidate_preserves_verified_homepage_locale(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        homepage = "https://example.com/us/en.html"

        same_region = agent._score_career_candidate(
            RawLink(
                "https://example.com/us/en/careers/careers.html",
                "Careers",
                homepage,
                "page_link",
            ),
            homepage,
        )
        other_region = agent._score_career_candidate(
            RawLink(
                "https://example.com/au/en/careers/hot-jobs.html",
                "Hot jobs",
                "https://example.com/au/sitemap.xml",
                "sitemap",
            ),
            homepage,
        )

        self.assertGreater(same_region.score, other_region.score)
        self.assertIn("matches homepage locale 'us'", same_region.reasons)
        self.assertIn("conflicts with homepage locale 'us': 'au'", other_region.reasons)

    def test_general_role_prefers_career_home_over_audience_page_and_self_link(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        homepage = "https://example.com/us/en.html"
        target = "Agentic AI Engineer"
        links = [
            RawLink(homepage, "Careers", homepage, "page_link"),
            RawLink(
                "https://example.com/us/en/careers/executive-jobs.html",
                "Executives",
                homepage,
                "page_link",
            ),
            RawLink(
                "https://example.com/us/en/careers/careers.html",
                "Careers home",
                homepage,
                "page_link",
            ),
        ]

        scored = [
            agent._score_career_candidate(link, homepage, target_title=target)
            for link in links
        ]

        self.assertEqual(max(scored, key=lambda candidate: candidate.score).url, links[2].url)
        self.assertIn("homepage self-link", scored[0].reasons)
        self.assertIn("career audience mismatch: executive", scored[1].reasons)
        self.assertIn("explicit career landing root", scored[2].reasons)

    def test_short_career_probe_ranks_above_deep_career_jobs_probe(self):
        agent = JobSourceAgent(Fetcher(offline=True), enable_career_search=False)

        ranked = sorted(
            [agent._score_career_candidate(link) for link in agent._common_path_candidates("https://example.com")],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        urls = [candidate.url for candidate in ranked]

        self.assertLess(urls.index("https://example.com/careers"), urls.index("https://example.com/careers/jobs"))

    def test_homepage_career_link_ranks_above_same_path_probe(self):
        agent = JobSourceAgent(Fetcher(offline=True), enable_career_search=False)
        homepage_link = agent._score_career_candidate(
            RawLink("https://example.com/about/careers", "Careers", "https://example.com", "page_link")
        )
        path_probe = agent._score_career_candidate(
            RawLink("https://example.com/careers", "", "https://example.com", "path_probe")
        )

        self.assertGreater(homepage_link.score, path_probe.score)

    def test_explicit_first_party_jobs_portal_precedes_marketing_career_root(self):
        homepage = "https://www.northstar.example"
        marketing_root = f"{homepage}/careers"
        jobs_portal = "https://jobs.northstar.example/"

        class PortalFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if url.rstrip("/") == homepage:
                    return Page(
                        url=url,
                        final_url=homepage,
                        html=(
                            f'<a href="{marketing_root}">Careers</a>'
                            f'<a href="{jobs_portal}">Search jobs</a>'
                        ),
                    )
                if url == jobs_portal:
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><h1>Open roles</h1></html>",
                    )
                raise AssertionError(f"unexpected candidate fetch: {url}")

        fetcher = PortalFetcher()
        career_url, trace = JobSourceAgent(
            fetcher,
            max_candidates=1,
            max_career_candidate_fetches=1,
            enable_career_search=False,
            enable_sitemap_discovery=False,
        ).find_career_page(homepage)

        self.assertEqual(career_url, jobs_portal)
        self.assertEqual(fetcher.requested, [homepage, jobs_portal])
        self.assertEqual(trace["candidate_schedule"]["scheduled"][0]["evidence_tier"], 1)
        self.assertIn(
            "explicit first-party jobs portal action",
            trace["selected"]["reasons"],
        )

    def test_jobs_portal_priority_requires_strong_first_party_homepage_action(self):
        homepage = "https://www.northstar.example"
        agent = JobSourceAgent(Fetcher(offline=True), enable_career_search=False)
        marketing_root = agent._score_career_candidate(
            RawLink(f"{homepage}/careers", "Careers", homepage, "page_link"),
            homepage,
        )
        for portal_url, action_text in (
            ("https://apply.northstar.example", "Apply now"),
            ("https://jobs.northstar.example", "Search jobs"),
            ("https://portal.northstar.example", "Open jobs"),
        ):
            explicit_portal = agent._score_career_candidate(
                RawLink(portal_url, action_text, homepage, "page_link"),
                homepage,
            )
            self.assertGreater(explicit_portal.score, marketing_root.score)
            self.assertIn(
                "explicit first-party jobs portal action",
                explicit_portal.reasons,
            )
        weak_action = agent._score_career_candidate(
            RawLink("https://jobs.northstar.example", "Learn more", homepage, "page_link"),
            homepage,
        )
        external_portal = agent._score_career_candidate(
            RawLink("https://jobs.unrelated.example", "Search jobs", homepage, "page_link"),
            homepage,
        )
        conflicting_region = agent._score_career_candidate(
            RawLink(
                "https://jobs.northstar.example/en-gb",
                "Search jobs",
                homepage,
                "page_link",
            ),
            homepage,
            target_location="United States",
        )

        self.assertGreater(marketing_root.score, weak_action.score)
        self.assertGreater(marketing_root.score, external_portal.score)
        self.assertGreater(marketing_root.score, conflicting_region.score)
        self.assertNotIn("explicit first-party jobs portal action", weak_action.reasons)
        self.assertNotIn("explicit first-party jobs portal action", external_portal.reasons)
        self.assertIn(
            "conflicts with target location region 'us': 'gb'",
            conflicting_region.reasons,
        )

    def test_error_page_is_not_treated_as_career_page(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

    def test_career_candidate_rejects_unverified_cross_site_redirect(self):
        class RedirectFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://content.example.net/job-search-tips",
                    html="<html><body>Careers jobs and open roles</body></html>",
                    source="fixture",
                )

        agent = JobSourceAgent(RedirectFetcher(offline=True))
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    "https://company.example/news/timesjobs-career-advice",
                    "timesjobs career advice",
                    "https://company.example/sitemap.xml",
                    200,
                    ["sitemap source"],
                )
            ],
            trace,
            max_fetches=1,
        )

        self.assertIsNone(selected)
        self.assertIn("unverified cross-site redirect", trace["candidate_fetch_errors"][0]["error"])

        self.assertTrue(
            agent._looks_like_error_page(
                "https://example.com/errors/404/",
                "<html><title>Careers</title><body>Page not found</body></html>",
            )
        )

    def test_career_candidate_fetch_budget_is_respected(self):
        class BudgetFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "slow" in url:
                    raise FetchError("timeout")
                return Page(url=url, html="<html><body>Open roles and careers</body></html>", final_url=url)

        agent = JobSourceAgent(
            BudgetFetcher(offline=True),
            max_candidates=2,
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate("https://slow.example/careers", "careers", "https://example.com", 100, []),
                LinkCandidate("https://good.example/careers", "careers", "https://example.com", 100, []),
            ],
            trace,
        )

        self.assertIsNone(selected)
        self.assertEqual(len(trace["candidate_fetch_errors"]), 1)
        self.assertEqual(trace["candidate_fetch_budget_exhausted"]["limit"], 1)

    def test_legacy_candidate_selection_can_retry_across_phases_without_evidence(self):
        candidate_url = "https://company.example/careers"

        class TransientFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                if self.calls == 1:
                    raise FetchError(
                        "temporary timeout",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                    )
                return Page(
                    url=url,
                    final_url=url,
                    html="<html><body>Browse careers and open roles.</body></html>",
                )

        fetcher = TransientFetcher()
        agent = JobSourceAgent(fetcher, max_candidates=1, max_career_candidate_fetches=1)
        candidates = [
            LinkCandidate(
                candidate_url,
                "Careers",
                "https://company.example",
                100,
                ["homepage navigation link"],
            )
        ]

        first = agent._select_verified_career_candidate(
            candidates,
            {"candidate_fetch_errors": []},
            schedule_source="homepage_and_common_paths",
        )
        second = agent._select_verified_career_candidate(
            candidates,
            {"candidate_fetch_errors": []},
            schedule_source="search",
        )

        self.assertIsNone(first)
        self.assertEqual(second, candidate_url)
        self.assertEqual(fetcher.calls, 2)

    def test_homepage_navigation_evidence_precedes_higher_scored_path_probe(self):
        explicit_url = "https://company.example/team"

        class EvidenceFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url != explicit_url:
                    raise AssertionError(f"speculative path fetched before homepage evidence: {url}")
                return Page(
                    url=url,
                    final_url=url,
                    html="<html><body>Join our team. Browse careers and open roles.</body></html>",
                    source="fixture",
                )

        agent = JobSourceAgent(EvidenceFetcher(offline=True), max_career_candidate_fetches=1)
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    "https://company.example/en-us/careers",
                    "",
                    "https://company.example",
                    420,
                    ["generated path probe"],
                ),
                LinkCandidate(
                    explicit_url,
                    "Team",
                    "https://company.example",
                    310,
                    ["homepage navigation link", "homepage team link requiring employment evidence"],
                ),
            ],
            trace,
            max_fetches=1,
        )

        self.assertEqual(selected, explicit_url)
        self.assertEqual(trace["selected"]["url"], explicit_url)

    def test_zero_career_candidate_fetch_budget_skips_candidates(self):
        agent = JobSourceAgent(Fetcher(offline=True), max_career_candidate_fetches=0)
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [LinkCandidate("https://good.example/careers", "careers", "https://example.com", 100, [])],
            trace,
        )

        self.assertIsNone(selected)
        self.assertEqual(trace["candidate_fetch_budget_exhausted"]["limit"], 0)


if __name__ == "__main__":
    unittest.main()
