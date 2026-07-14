import unittest
from pathlib import Path

from job_source_agent.content_probe import (
    discover_first_party_career_navigation,
    probe_first_party_cms_payload,
)
from job_source_agent.errors import DiscoveryError
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import CompanyInput, LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page, RawLink


ROOT = Path(__file__).resolve().parents[1]


class OfflinePipelineTests(unittest.TestCase):
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
        self.assertEqual(trace["ats_board_discovery"]["provider_board_verification"][0]["method"], "page_job_links")

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
        self.assertIn("upgraded same-site HTTP link to HTTPS", candidate.reasons)

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
