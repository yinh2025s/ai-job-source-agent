import unittest
import html
import json
from pathlib import Path

from job_source_agent.career_search import CareerSearchResult
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.errors import DiscoveryError
from job_source_agent.providers.registry import ProviderRegistry
from job_source_agent.web import FetchError, Fetcher, Page, RawLink


ROOT = Path(__file__).resolve().parents[1]


class MappingFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def fetch(self, url, data=None, headers=None):
        self.requested.append(url)
        page = self.pages.get(url)
        if page is None:
            raise FetchError(f"unexpected URL: {url}")
        return page


class HiddenJobBoardDiscoveryTests(unittest.TestCase):
    def test_career_root_with_multiple_verified_job_accordions_is_a_generic_board(self):
        career = "https://school.example/careers/"
        html = """
        <p>East Sandwich, MA 02537</p>
        <div class="accordion-item">
          <a class="toggle" href="#overnight-rn-school-nurse">Overnight RN - School Nurse</a>
          <p>Reports To: Director. Essential Job Function: onsite care.</p>
          <a href="/employment-application/">online application</a>
        </div>
        <div class="accordion-item">
          <a class="toggle" href="#evening-rn-school-nurse">Evening RN - School Nurse</a>
          <p>Job Classification: Part Time. Candidate Requirements: RN license.</p>
          <a href="/employment-application/">online application</a>
        </div>
        """
        fetcher = MappingFetcher({career: Page(url=career, html=html)})

        job_list, trace, discovered = JobSourceAgent(fetcher).find_job_board_with_evidence(
            career,
            company_name="Example School",
            target_location="East Sandwich, MA",
        )

        self.assertEqual(job_list, career)
        self.assertEqual(trace["selected_from"], "verified_first_party_listing_inventory")
        self.assertIsNone(discovered)

    def test_upgrades_selected_board_from_observed_two_hop_career_action_chain(self):
        career = "https://careers.example.com/"
        category = "https://careers.example.com/nursing/"
        provider_root = "https://careers-example-company.icims.com/"
        board_url = "https://careers-example-company.icims.com/jobs/search"
        agent = JobSourceAgent(MappingFetcher({}))
        primary = DiscoveredJobBoard(
            JobBoard(board_url, "icims", "careers-example-company.icims.com"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url=provider_root,
        )
        trace = {
            "pages_visited": [
                {"url": career},
                {"url": category},
                {"url": provider_root},
            ],
            "career_actions": [
                {
                    "source_url": career,
                    "target_url": category,
                    "kind": "open_job_list",
                    "confidence": "high",
                    "status": "eligible",
                },
                {
                    "source_url": category,
                    "target_url": provider_root,
                    "kind": "open_job_list",
                    "confidence": "high",
                    "status": "verified_job_list",
                },
            ],
            "candidates": [
                {
                    "url": provider_root,
                    "source_url": category,
                    "origin": "page_link",
                }
            ],
        }

        upgraded = agent._upgrade_observed_provider_handoff(
            primary,
            trace,
            career,
        )

        self.assertEqual(upgraded.board.url, board_url)
        self.assertEqual(upgraded.detection_method, "linked_url_evidence")
        self.assertEqual(upgraded.relationship_evidence_url, category)

    def test_does_not_upgrade_board_through_unvisited_or_cross_site_intermediate(self):
        career = "https://careers.example.com/"
        provider_root = "https://careers-example-company.icims.com/"
        board_url = "https://careers-example-company.icims.com/jobs/search"
        agent = JobSourceAgent(MappingFetcher({}))
        primary = DiscoveredJobBoard(
            JobBoard(board_url, "icims", "careers-example-company.icims.com"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url=provider_root,
        )
        base_trace = {
            "career_actions": [
                {
                    "source_url": career,
                    "target_url": "https://unrelated.example/jobs/",
                    "kind": "open_job_list",
                    "confidence": "high",
                    "status": "visited",
                }
            ],
            "candidates": [
                {
                    "url": provider_root,
                    "source_url": "https://unrelated.example/jobs/",
                    "origin": "page_link",
                }
            ],
        }
        cases = (
            {**base_trace, "pages_visited": [{"url": career}]},
            {
                **base_trace,
                "pages_visited": [
                    {"url": career},
                    {"url": "https://unrelated.example/jobs/"},
                ],
            },
        )

        for trace in cases:
            with self.subTest(trace=trace):
                self.assertIs(
                    agent._upgrade_observed_provider_handoff(
                        primary,
                        trace,
                        career,
                    ),
                    primary,
                )

    def test_matching_bundle_destination_outranks_sibling_brand_board(self):
        source = "https://careers.example.com"
        group = JobSourceAgent._score_job_board_link(
            RawLink(
                "https://jobs.example.com",
                "Example Group",
                source,
                "first_party_bundle_job_destination",
            ),
            source,
            "Example Group (B Corp)",
        )
        sibling = JobSourceAgent._score_job_board_link(
            RawLink(
                "https://job-boards.greenhouse.io/brand",
                "Sibling Brand",
                source,
                "first_party_bundle_job_destination",
            ),
            source,
            "Example Group (B Corp)",
        )

        self.assertGreater(group.score, sibling.score)
        self.assertIn(
            "job destination label matches company identity",
            group.reasons,
        )

        trace = {}
        retained = JobSourceAgent._filter_bundle_destination_scope(
            [sibling, group],
            "Example Group (B Corp)",
            trace,
        )
        self.assertEqual(retained, [group])
        self.assertEqual(
            trace["bundle_destination_scope_rejections"][0]["reason"],
            "sibling_brand_not_current_hiring_entity",
        )

    def test_provider_career_url_fast_path_preserves_typed_evidence(self):
        career = "https://jobs.example/acme"

        class DirectProviderAdapter:
            name = "direct_provider"
            supports_listing = True

            def recognizes(self, url):
                return url.startswith("https://jobs.example/")

            def identify_board(self, url):
                return JobBoard(url=career, provider=self.name, identifier="acme")

        fetcher = MappingFetcher({})
        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((DirectProviderAdapter(),)),
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, career)
        self.assertEqual(fetcher.requested, [])
        self.assertEqual(trace["selected"]["reason"], "career page is already a provider job board")
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.board.identifier, "acme")
        self.assertEqual(discovered.detection_method, "url_evidence")
        self.assertEqual(discovered.evidence_url, career)

    def test_follows_explicit_same_site_job_offers_listing(self):
        career = "https://careers.example.com/en/index.html"
        listing = "https://careers.example.com/en/annonces"
        opening = "https://careers.example.com/en/annonces/job/123/software-engineer"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                final_url=career,
                html=f'<a href="{listing}">Our job offers</a>',
            ),
            listing: Page(
                url=listing,
                final_url=listing,
                html=f'<a href="{opening}">Software Engineer</a>',
            ),
        })

        job_list, trace, _discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested[:2], [career, listing])
        selected_pages = [item["url"] for item in trace["pages_visited"]]
        self.assertIn(listing, selected_pages)

    def test_follows_visible_job_offers_route_with_shortened_label(self):
        career = "https://caudalie.career/"
        listing = "https://caudalie.career/home/our-job-offers"
        opening = f"{listing}/123/account-executive"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                final_url=career,
                html=f'<a href="{listing}">Our Offers</a>',
            ),
            listing: Page(
                url=listing,
                final_url=listing,
                html=f'<a href="{opening}">Account Executive</a>',
            ),
        })

        job_list, _trace, _discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested[:2], [career, listing])

    def test_scoped_workday_board_builds_replay_safe_general_portfolio(self):
        early = "https://visa.wd1.myworkdayjobs.com/en-US/Visa_Early_Careers"
        general = "https://visa.wd1.myworkdayjobs.com/en-US/Visa_Careers"
        general_api = "https://visa.wd1.myworkdayjobs.com/wday/cxs/visa/Visa_Careers/jobs"
        fetcher = MappingFetcher({
            general_api: Page(
                url=general_api,
                final_url=general_api,
                html=json.dumps({"total": 0, "jobPostings": []}),
            ),
        })
        agent = JobSourceAgent(fetcher, max_job_board_attempts=3)
        agent._search_career_candidates = lambda *args, **kwargs: CareerSearchResult(
            candidates=[
                LinkCandidate(
                    url=early,
                    text="University jobs",
                    source_url="https://www.visa.com/careers",
                    score=550,
                ),
                LinkCandidate(
                    url=general,
                    text="Search jobs",
                    source_url="https://www.visa.com/careers",
                    score=355,
                ),
            ],
            trace={"error": None},
        )

        selected, trace, portfolio = agent.find_job_board_portfolio(
            early,
            company_name="Visa",
            target_title="Data Scientist",
            target_location="United States",
        )

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(selected, general)
        self.assertEqual(
            [item.board.url for item in portfolio.boards],
            [general, early],
        )
        self.assertTrue(portfolio.eligible_set_complete)
        self.assertIsNotNone(portfolio.to_checkpoint_payload())
        self.assertEqual(fetcher.requested, [general_api])
        self.assertEqual(trace["job_board_portfolio"]["eligible_count"], 2)

    def test_interrupted_portfolio_search_is_not_marked_complete(self):
        early = "https://visa.wd1.myworkdayjobs.com/en-US/Visa_Early_Careers"
        agent = JobSourceAgent(MappingFetcher({}), max_job_board_attempts=3)
        agent._search_career_candidates = lambda *args, **kwargs: CareerSearchResult(
            candidates=[],
            trace={"error": "search timed out"},
        )

        _selected, trace, portfolio = agent.find_job_board_portfolio(
            early,
            company_name="Visa",
            target_title="Data Scientist",
            target_location="United States",
        )

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertFalse(portfolio.eligible_set_complete)
        self.assertFalse(trace["job_board_portfolio"]["eligible_set_complete"])

    def test_portfolio_retains_visited_first_party_action_before_nested_provider(self):
        career = "https://careers.brand.example"
        portal = "https://careers.parent.example/careers?filter_house=Brand"
        nested = "https://sandbox.jobs.example/careers"

        class NestedAdapter:
            name = "nested"
            supports_listing = True

            def recognizes(self, url):
                return url.startswith("https://sandbox.jobs.example/")

            def identify_board(self, url):
                if not self.recognizes(url):
                    return None
                return JobBoard(nested, self.name, "sandbox")

        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{portal}">Jobs in the house</a>',
            ),
            portal: Page(
                url=portal,
                html=f'<script>const embeddedBoard = "{nested}";</script>',
            ),
        })

        _selected, trace, portfolio = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((NestedAdapter(),)),
            max_job_pages=2,
        ).find_job_board_portfolio(
            career,
            company_name="Brand",
            target_title="Financial Analyst",
        )

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        identities = {
            (item.board.provider, item.board.url) for item in portfolio.boards
        }
        self.assertIn(("nested", nested), identities)
        self.assertIn(("generic", portal), identities)
        self.assertTrue(portfolio.eligible_set_complete)
        self.assertEqual(trace["job_board_portfolio"]["eligible_count"], 2)

    def test_page_evidence_native_board_skips_blind_ats_search(self):
        career = "https://careers.example.com/open-roles"

        class CustomerDomainAdapter:
            name = "customer_domain"
            supports_listing = True

            def recognizes(self, url):
                return False

            def identify_board(self, url):
                return None

            def identify_board_from_page(self, page):
                if "customer-domain-jobs" not in page.html:
                    return None
                return JobBoard(url=career, provider=self.name)

            def list_jobs(self, fetcher, board, query):
                raise AssertionError("S5 must not query provider inventory")

        fetcher = MappingFetcher({
            career: Page(url=career, html="<main>customer-domain-jobs</main>"),
        })
        agent = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((CustomerDomainAdapter(),)),
            max_job_pages=1,
        )

        job_list, trace, discovered = agent.find_job_board_with_evidence(
            career,
            company_name="Example",
        )

        self.assertEqual(job_list, career)
        self.assertEqual(fetcher.requested, [career])
        self.assertNotIn("ats_search_fallback", trace)
        self.assertEqual(trace["provider_detection"]["method"], "page_evidence")
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.board.provider, "customer_domain")

    def test_detection_only_page_evidence_keeps_ats_search_fallback(self):
        career = "https://careers.example.com/open-roles"
        searched_board = "https://jobs.lever.co/example"

        class DetectionOnlyAdapter:
            name = "detection_only"
            supports_listing = False

            def recognizes(self, url):
                return False

            def identify_board(self, url):
                return None

            def identify_board_from_page(self, page):
                return JobBoard(url=career, provider=self.name)

            def list_jobs(self, fetcher, board, query):
                raise AssertionError("Detection-only inventory must not be queried in S5")

        fetcher = MappingFetcher({career: Page(url=career, html="<main>Careers</main>")})
        agent = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((DetectionOnlyAdapter(),)),
            max_job_pages=1,
        )
        agent._search_verified_ats_board = lambda company_name, career_url: (
            searched_board,
            {"search": {"status": "verified"}},
        )

        job_list, trace, discovered = agent.find_job_board_with_evidence(
            career,
            company_name="Example",
        )

        self.assertEqual(job_list, searched_board)
        self.assertEqual(trace["selected_from"], "ats_search_fallback")
        self.assertIsNone(discovered)

    def test_recovers_provider_url_from_bounded_same_site_module_assets(self):
        career = "https://www.example.com/careers/"
        route_asset = "https://www.example.com/assets/page-careers-A1.js"
        shared_asset = "https://www.example.com/assets/page-about-B2.js"
        board = "https://jobs.ashbyhq.com/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<link rel="modulepreload" href="{shared_asset}">'
                    f'<link rel="modulepreload" href="{route_asset}">'
                    "<main>Open positions</main>"
                ),
            ),
            route_asset: Page(url=route_asset, html='import "./page-about-B2.js";'),
            shared_asset: Page(url=shared_asset, html=f'const jobs="{board}";'),
            board: Page(url=board, html="<html>Ashby job board</html>"),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        probe = trace["content_payload_probes"][0]
        self.assertEqual(probe["method"], "first_party_provider_asset")
        self.assertEqual(probe["asset_urls"], [route_asset, shared_asset])
        self.assertEqual(probe["provider_urls"], [board])

    def test_validates_strong_same_site_listing_route_before_provider_assets(self):
        career = "https://www.example.com/en/careers"
        listing = "https://www.example.com/en-us/careers/job-results"
        asset = "https://www.example.com/assets/page-careers.js"

        class EmbeddedBoardAdapter:
            name = "embedded_board"
            supports_listing = True

            def recognizes(self, url):
                return False

            def identify_board(self, url):
                return None

            def identify_board_from_page(self, page):
                if "sitecore-job-results" not in page.html:
                    return None
                return JobBoard(url=listing, provider=self.name)

            def probe_board(self, fetcher, page):
                fetcher.fetch(asset)
                return None

            def list_jobs(self, fetcher, board, query):
                raise AssertionError("S5 must not query provider inventory")

        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<script src="{asset}"></script>'
                    f'<script>const listingRoute = "{listing}";</script>'
                ),
            ),
            listing: Page(url=listing, html="<main>sitecore-job-results</main>"),
        })
        agent = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((EmbeddedBoardAdapter(),)),
            max_job_pages=2,
        )

        job_list, trace, discovered = agent.find_job_board_with_evidence(career)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested, [career, listing])
        self.assertNotIn(asset, fetcher.requested)
        self.assertEqual(trace["provider_detection"]["method"], "page_evidence")
        self.assertIsNotNone(discovered)

    def test_follows_explicit_current_openings_across_sibling_career_route(self):
        career = "https://www.example.com/careers"
        listing = "https://www.example.com/career-openings"
        board = "https://recruitingbypaycor.com/career/iframe.action?clientId=ABC"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{listing}">View All Current Openings</a>',
            ),
            listing: Page(
                url=listing,
                html=f'<iframe src="{board}"></iframe>',
            ),
            board: Page(url=board, html="<title>Current openings</title>"),
        })

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=3,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, listing)
        self.assertIsNone(discovered)
        self.assertIn(listing, fetcher.requested)

    def test_visible_canonical_provider_board_precedes_same_site_listing_route(self):
        career = "https://www.example.com/careers"
        generic_listing = "https://www.example.com/jobs"
        board = "https://jobs.ashbyhq.com/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{generic_listing}">Search jobs</a>'
                    f'<a href="{board}">View roles</a>'
                ),
            ),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(
            trace["provider_detection"]["method"],
            "linked_url_evidence",
        )
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.detection_method, "linked_url_evidence")
        self.assertEqual(discovered.evidence_url, board)
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_legacy_greenhouse_visible_board_enters_portfolio_as_canonical_board(self):
        career = "https://www.example.com/careers"
        legacy_board = "https://boards.greenhouse.io/acme"
        canonical_board = "https://job-boards.greenhouse.io/acme"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{legacy_board}">View roles</a>',
            ),
        })

        job_list, _trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, canonical_board)
        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(portfolio.primary.board.url, canonical_board)
        self.assertEqual(portfolio.primary.evidence_url, canonical_board)
        self.assertEqual(portfolio.primary.relationship_evidence_url, career)
        self.assertEqual(fetcher.requested, [career])

    def test_visible_canonical_provider_board_accepts_presentation_query(self):
        career = "https://www.example.com/careers"
        visible_board = "https://jobs.ashbyhq.com/example?display=embedded"
        canonical_board = "https://jobs.ashbyhq.com/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{visible_board}">View roles</a>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, canonical_board)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(trace["provider_detection"]["method"], "linked_url_evidence")

    def test_visible_typed_adp_locator_canonicalizes_without_fetching_provider(self):
        career = "https://www.example.com/careers"
        visible_board = (
            "https://recruiting.adp.com/srccar/public/RTI.home?"
            "c=1181515&d=ExternalCareerSite"
        )
        canonical_board = (
            "https://recruiting.adp.com/srccar/public/nghome.guid?"
            "c=1181515&d=ExternalCareerSite"
        )
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=f'<a href="{visible_board}">Corporate opportunities</a>',
                ),
            }
        )

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, canonical_board)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(trace["provider_detection"]["method"], "linked_url_evidence")
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_multiple_visible_provider_details_establish_one_tenant_board(self):
        career = "https://www.example.com/careers"
        first = "https://acme.applicantstack.com/x/detail/abc12345"
        second = "https://acme.applicantstack.com/x/detail/def67890"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{first}">Registered Nurse</a>'
                    f'<a href="{second}">Nurse Manager</a>'
                ),
            ),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(
            job_list,
            "https://acme.applicantstack.com/x/openings",
        )
        self.assertEqual(trace["provider"], "applicantstack")
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_visible_provider_details_from_multiple_tenants_are_ambiguous(self):
        career = "https://www.example.com/careers"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    '<a href="https://one.applicantstack.com/x/detail/abc12345">'
                    'Registered Nurse</a>'
                    '<a href="https://two.applicantstack.com/x/detail/def67890">'
                    'Nurse Manager</a>'
                ),
            ),
        })

        job_list, _trace, discovered = JobSourceAgent(
            fetcher, max_job_pages=1
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, career)
        self.assertIsNone(discovered)
        self.assertEqual(fetcher.requested[0], career)

    def test_embedded_canonical_provider_board_is_a_typed_handoff_candidate(self):
        career = "https://www.example.com/careers"
        board = "https://job-boards.greenhouse.io/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<section data-job-board-url="{board}">Open roles</section>',
            ),
        })

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio.primary.detection_method, "linked_url_evidence")
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(
            trace["provider_detection"]["method"],
            "embedded_provider_url_evidence",
        )

    def test_iframe_provider_board_is_a_typed_first_party_handoff(self):
        career = "https://www.example.com/careers"
        board = "https://example-search.app.loxo.co/example-search"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<iframe src="{board}?disable_addthis=true"></iframe>',
            ),
        })

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(portfolio.primary.board.provider, "loxo")
        self.assertEqual(portfolio.primary.board.url, board)
        self.assertEqual(portfolio.primary.relationship_evidence_url, career)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(
            trace["provider_detection"]["method"],
            "embedded_provider_url_evidence",
        )

    def test_embedded_provider_details_preserve_multiple_first_party_tenants(self):
        career = "https://careers.example.com/jobs"
        haven = "https://job-boards.greenhouse.io/haven/jobs/123"
        company = (
            "https://job-boards.greenhouse.io/"
            "sonyinteractiveentertainmentglobal/jobs/456"
        )
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=(
                        f'<script>window.jobs=["{haven}","{company}"]</script>'
                    ),
                )
            }
        )

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(
            career,
            company_name="Sony Interactive Entertainment",
            target_title="Software Engineer",
        )

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(len(portfolio.boards), 2)
        self.assertEqual(
            job_list,
            "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal",
        )
        self.assertFalse(portfolio.eligible_set_complete)
        self.assertEqual(trace["job_board_portfolio"]["eligible_count"], 2)

    def test_visible_multi_opening_page_remains_the_job_list_root(self):
        career = "https://talent.example.com/jobs"
        first = "https://talent.example.com/job/111-first-engineer"
        second = "https://talent.example.com/job/222-second-engineer"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=(
                        f'<a href="{first}">View role</a>'
                        f'<a href="{second}">View role</a>'
                    ),
                )
            }
        )

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, career)
        self.assertIsNone(discovered)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(
            trace["selected_from"],
            "explicit_first_party_listing_route",
        )

    def test_structured_component_provider_root_precedes_content_pages(self):
        career = "https://www.example.com/careers"
        root = (
            "https://edmn.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX_1"
        )
        content = f"{root}/pages/24075"
        model = html.escape(json.dumps({
            "items": [
                {"label": "See openings", "url": content},
                {"label": "All jobs", "url": root},
            ]
        }), quote=True)
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<div data-model="{model}"></div>',
            ),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, root)
        self.assertEqual(trace["provider"], "oracle_hcm")
        self.assertEqual(fetcher.requested, [career])
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_embedded_provider_detail_is_only_promoted_to_tenant_board(self):
        career = "https://www.example.com/careers"
        detail = "https://jobs.ashbyhq.com/example/06d5624e-d35c-41b1-a091-edfc79c10dba"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<section data-example-url="{detail}">Our culture</section>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/example")
        self.assertEqual(trace["provider"], "ashby")
        self.assertEqual(fetcher.requested, [career])

    def test_visible_provider_detail_with_query_is_not_promoted_as_board(self):
        career = "https://www.example.com/careers"
        generic_listing = "https://www.example.com/jobs"
        detail = (
            "https://jobs.ashbyhq.com/example/"
            "06d5624e-d35c-41b1-a091-edfc79c10dba?display=embedded"
        )
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{generic_listing}">Search jobs</a>'
                    f'<a href="{detail}">Software Engineer</a>'
                ),
            ),
            generic_listing: Page(
                url=generic_listing,
                html="<main>Search open roles</main>",
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, generic_listing)
        self.assertEqual(fetcher.requested, [career, generic_listing])
        self.assertNotEqual(trace.get("provider"), "ashby")

    def test_same_site_redirect_uses_final_url_without_deferred_request_key(self):
        requested = "https://www.example.com/careers"
        redirected = "https://www.example.com/join-us"
        listing = "https://www.example.com/jobs"
        asset = "https://www.example.com/assets/join-us.js"
        fetcher = MappingFetcher({
            requested: Page(
                url=requested,
                final_url=redirected,
                html=(
                    f'<script src="{asset}"></script>'
                    f'<a href="{listing}">Search jobs</a>'
                ),
            ),
            asset: Page(url=asset, html="const page = 'join-us';"),
            listing: Page(url=listing, html="<main>Search open roles</main>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(requested)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested, [requested, asset, listing])
        self.assertEqual(trace["pages_visited"][0]["url"], redirected)

    def test_provider_asset_probe_follows_failed_priority_listing_validation(self):
        career = "https://www.example.com/en/careers"
        listing = "https://www.example.com/en-us/careers/job-results"
        asset = "https://www.example.com/assets/page-careers.js"
        board = "https://jobs.ashbyhq.com/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<script src="{asset}"></script>'
                    f'<a href="{listing}">Search jobs</a>'
                ),
            ),
            listing: Page(
                url=listing,
                final_url=career,
                html="<main>Careers</main>",
            ),
            asset: Page(url=asset, html=f'const board = "{board}";'),
            board: Page(url=board, html="<main>Ashby job board</main>"),
        })

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio.primary.detection_method, "linked_url_evidence")
        self.assertEqual(portfolio.primary.evidence_url, board)
        self.assertEqual(portfolio.primary.relationship_evidence_url, career)
        self.assertEqual(fetcher.requested, [career, listing, asset, board])
        self.assertEqual(fetcher.requested.count(asset), 1)
        provider_asset_probes = [
            probe
            for probe in trace["content_payload_probes"]
            if probe["method"] == "first_party_provider_asset"
        ]
        self.assertEqual(len(provider_asset_probes), 1)
        candidate_urls = [candidate["url"] for candidate in trace["candidates"]]
        self.assertEqual(candidate_urls.count(listing), 1)

    def test_preserves_asset_backed_provider_handoff_when_board_redirects_to_career(self):
        career = "https://www.example.com/careers/"
        route_asset = "https://www.example.com/assets/page-careers.js"
        embed = "https://boards.greenhouse.io/embed/job_board/js?for=example"
        board = "https://job-boards.greenhouse.io/example"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<script src="{route_asset}"></script><main>Open positions</main>',
            ),
            route_asset: Page(url=route_asset, html=f'const board="{embed}";'),
            board: Page(url=board, final_url=career, html="<main>Open positions</main>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(
            trace["provider_detection"]["method"],
            "redirected_linked_url_evidence",
        )
        self.assertTrue(trace["pages_visited"][1]["provider_handoff_preserved"])

    def test_preserves_direct_provider_embed_when_board_redirects_to_career(self):
        career = "https://www.example.com/careers/"
        embed = "https://boards.greenhouse.io/embed/job_board/js?for=example"
        board = "https://job-boards.greenhouse.io/example"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<script src="{embed}"></script>'),
            board: Page(url=board, final_url=career, html="<main>Open positions</main>"),
        })

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio.primary.detection_method, "linked_url_evidence")
        self.assertEqual(
            trace["provider_detection"]["method"],
            "embedded_provider_url_evidence",
        )

    def test_traverses_explicit_same_site_all_jobs_route(self):
        career = "https://careers.example.com/en/"
        all_jobs = "https://careers.example.com/en/all-jobs/"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{all_jobs}">Search Jobs</a>'),
            all_jobs: Page(url=all_jobs, html="<main>Find your next role</main>"),
        })

        job_list, trace, portfolio = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_portfolio(career)

        self.assertEqual(job_list, all_jobs)
        self.assertEqual(trace["selected_from"], "explicit_first_party_listing_route")
        self.assertIsNone(portfolio)

    def test_verified_first_party_action_attests_generic_cross_site_inventory(self):
        career = "https://www.example.com/careers"
        board = "https://opaque-hiring.example/jobs"
        detail = "https://opaque-hiring.example/jobs/42/registered-nurse"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=f'<a href="{board}">Search Jobs</a>',
                ),
                board: Page(
                    url=board,
                    html=(
                        '<article class="job-card"><h3>Registered Nurse</h3>'
                        f'<a href="{detail}">View job</a></article>'
                    ),
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.detection_method, "verified_first_party_action")
        self.assertEqual(discovered.evidence_url, board)
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_semantic_job_actions_attest_cross_site_inventory(self):
        for index, label in enumerate(("Explore opportunities", "Jobs in the house")):
            with self.subTest(label=label):
                career = f"https://www.example{index}.com/careers"
                board = f"https://hiring.example{index}.net/jobs"
                detail = f"{board}/42/data-analyst"
                fetcher = MappingFetcher(
                    {
                        career: Page(
                            url=career,
                            html=f'<a href="{board}">{label}</a>',
                        ),
                        board: Page(
                            url=board,
                            html=(
                                '<article class="job-card"><h3>Data Analyst</h3>'
                                f'<a href="{detail}">View job</a></article>'
                            ),
                        ),
                    }
                )

                job_list, _trace, discovered = JobSourceAgent(
                    fetcher,
                    max_job_pages=2,
                ).find_job_board_with_evidence(career)

                self.assertEqual(job_list, board)
                self.assertIsNotNone(discovered)
                assert discovered is not None
                self.assertEqual(
                    discovered.detection_method,
                    "verified_first_party_action",
                )
                self.assertEqual(discovered.relationship_evidence_url, career)

    def test_verified_first_party_action_is_preserved_through_provider_root(self):
        career = "https://www.example.com/careers"
        provider_root = "https://careers-example.icims.com/"
        board = "https://careers-example.icims.com/jobs/search"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=f'<a href="{provider_root}">Current Job Openings</a>',
                ),
                provider_root: Page(
                    url=provider_root,
                    html=f'<a href="{board}">Current job openings</a>',
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.detection_method, "linked_url_evidence")
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_direct_action_attests_board_reached_through_sibling_route(self):
        career = "https://careers.example.com/us/en"
        sibling = "https://careers.example.com/us/en/jobs?brand%5B%5D=Example"
        board = "https://parent.example.net/careers/jobs?units[]=example"
        detail_one = "https://parent.example.net/careers/jobs/12345/engineer"
        detail_two = "https://parent.example.net/careers/jobs/12346/analyst"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=(
                        f'<a href="{sibling}">Search jobs</a>'
                        '<a href="https://parent.example.net/careers/jobs?units%5B%5D=example">'
                        'Search jobs</a>'
                    ),
                ),
                sibling: Page(
                    url=sibling,
                    final_url=board,
                    html=(
                        f'<a href="{detail_one}">Software Engineer</a>'
                        f'<a href="{detail_two}">Data Analyst</a>'
                    ),
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.detection_method, "verified_first_party_action")
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_direct_action_does_not_attest_board_with_different_query_scope(self):
        career = "https://careers.example.com/us/en"
        sibling = "https://careers.example.com/us/en/jobs"
        board = "https://parent.example.net/careers/jobs?units=other"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=(
                        f'<a href="{sibling}">Search jobs</a>'
                        '<a href="https://parent.example.net/careers/jobs?units=example">'
                        'Search jobs</a>'
                    ),
                ),
                sibling: Page(
                    url=sibling,
                    final_url=board,
                    html=(
                        '<a href="https://parent.example.net/careers/jobs/12345/engineer">'
                        'Software Engineer</a>'
                    ),
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertIsNone(discovered)

    def test_verified_action_accepts_query_identity_job_details_on_opaque_route(self):
        career = "https://www.example.com/careers"
        board = "https://opaque-hiring.example/careers?co=acme"
        detail = "https://opaque-hiring.example/jobs?pos=acme123"
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=f'<a href="{board}">View Job Postings</a>',
                ),
                board: Page(
                    url=board,
                    html=f'<a href="{detail}">Registered Nurse RN</a>',
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, board)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.detection_method, "verified_first_party_action")
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_first_party_search_route_accepts_repeated_structured_job_details(self):
        career = "https://careers.example.com"
        board = "https://careers.example.com/search/searchjobs"
        first = (
            "https://careers.example.com/search/jobdetails/mechanical-engineer/"
            "bd6ff1b8-8a51-40ea-8741-b0d6222a750a"
        )
        second = (
            "https://careers.example.com/search/jobdetails/data-analyst/"
            "f0a8ea76-8909-461e-85b6-cdf6f1037751"
        )
        fetcher = MappingFetcher(
            {
                career: Page(url=career, html=f'<a href="{board}">Search All Jobs</a>'),
                board: Page(
                    url=board,
                    html=(
                        f'<div data-job-url="{first}"></div>'
                        f'<div data-job-url="{second}"></div>'
                    ),
                ),
            }
        )

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(
            career
        )

        self.assertEqual(job_list, board)

    def test_first_party_search_route_rejects_single_opaque_detail_attribute(self):
        career = "https://careers.example.com"
        board = "https://careers.example.com/search/searchjobs"
        detail = (
            "https://careers.example.com/search/jobdetails/unrelated/"
            "bd6ff1b8-8a51-40ea-8741-b0d6222a750a"
        )
        fetcher = MappingFetcher(
            {
                career: Page(url=career, html=f'<a href="{board}">Explore</a>'),
                board: Page(url=board, html=f'<div data-job-url="{detail}"></div>'),
            }
        )

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

    def test_traverses_visible_cross_site_job_board_command_before_publishing(self):
        career = "https://www.example.com/careers"
        board = "https://recruiting2.ultipro.com/EXAMPLE/JobBoard/board-id/"
        detail = "https://recruiting2.ultipro.com/jobs/123/registered-nurse"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{board}">Open Positions</a>'),
            board: Page(
                url=board,
                html=(
                    '<article class="job-card"><h3>Registered Nurse</h3>'
                    f'<a href="{detail}">View job</a></article>'
                ),
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(fetcher.requested, [career, board])
        self.assertEqual(
            trace["selected_from"],
            "verified_first_party_listing_inventory",
        )

    def test_rejects_cross_site_job_command_without_recruiting_host_or_path(self):
        career = "https://www.example.com/careers"
        unrelated = "https://unrelated.example.net/products/board-id"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{unrelated}">Open Positions</a>'),
            unrelated: Page(url=unrelated, html="<main>Unverified page</main>"),
        })

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(raised.exception.code, "job_board_not_found")
        self.assertEqual(fetcher.requested, [career, unrelated])

    def test_explicit_deeper_search_command_precedes_generic_jobs_root(self):
        root = "https://www.example.com/careers/jobs"
        listing = "https://www.example.com/careers/jobs/joblisting"
        detail = "https://www.example.com/careers/jobs/joblisting/42/mechanical-engineer"
        fetcher = MappingFetcher({
            root: Page(url=root, html=f'<a href="{listing}">Search Now</a>'),
            listing: Page(
                url=listing,
                html=(
                    '<article class="job-card"><h3>Mechanical Engineer</h3>'
                    f'<a href="{detail}">View job</a></article>'
                ),
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(root)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested, [root, listing])
        self.assertEqual(
            trace["selected_from"],
            "verified_first_party_listing_inventory",
        )

    def test_explicit_deeper_joblisting_route_is_inventory_start_without_static_cards(self):
        root = "https://www.example.com/careers/jobs"
        listing = "https://www.example.com/careers/jobs/joblisting"
        fetcher = MappingFetcher({
            root: Page(url=root, html=f'<a href="{listing}">Search Now</a>'),
            listing: Page(url=listing, html="<main>Search available jobs</main>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(root)

        self.assertEqual(job_list, listing)
        self.assertEqual(fetcher.requested, [root, listing])
        self.assertEqual(trace["selected_from"], "explicit_first_party_listing_route")

    def test_exact_all_jobs_command_precedes_decorated_summary_link(self):
        root = "https://www.example.com/jobs"
        summary = "https://www.example.com/jobs/all-jobs"
        results = "https://www.example.com/jobs/results"
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=(
                    f'<a href="{summary}">All Jobs 680</a>'
                    f'<a href="{results}">All Jobs</a>'
                ),
            ),
            results: Page(
                url=results,
                html=(
                    '<article class="job-card"><h3>Software Engineer</h3>'
                    '<a href="/jobs/42/software-engineer">View job</a></article>'
                ),
            ),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(root)

        self.assertEqual(job_list, results)
        self.assertEqual(fetcher.requested, [root, results])

    def test_global_search_action_precedes_marketing_and_category_actions(self):
        root = "https://careers.example.com/"
        marketing = "https://careers.example.com/why-us"
        category = "https://careers.example.com/search?category=Operations"
        search = "https://careers.example.com/search"
        opening = "https://careers.example.com/jobs/123/project-manager"
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=(
                    f'<a href="{marketing}">Explore our career</a>'
                    f'<a href="{category}">See Open Jobs</a>'
                    f'<a href="{search}">Search Jobs</a>'
                ),
            ),
            search: Page(
                url=search,
                html=(
                    '<article class="job-card"><h3>Project Manager</h3>'
                    f'<a href="{opening}">Project Manager</a></article>'
                ),
            ),
        })

        job_list, _trace = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board(root)

        self.assertEqual(job_list, search)
        self.assertEqual(fetcher.requested, [root, search])

    def test_declared_anonymous_js_inventory_attests_generic_search_board(self):
        root = "https://careers.example.com/"
        search = "https://careers.example.com/search"
        asset = "https://careers.example.com/assets/page-search.js"
        script = """
            var url = window.settings.homeUrl + '/wp-json/example/jobs';
            url += "&keyword=" + encodeURIComponent(this.search.keyword);
            url += "&limit=" + this.jobsToShow;
            axios.get(url).then(function (response) {
                var tmpjobs = response.data;
                tmpjobs.map(function (job) { if (job.title) render(job); });
            });
        """
        fetcher = MappingFetcher({
            root: Page(url=root, html=f'<a href="{search}">Search Jobs</a>'),
            search: Page(
                url=search,
                html=f'<main>Search available jobs</main><script src="{asset}"></script>',
            ),
            asset: Page(url=asset, final_url=asset, html=script),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(root)

        self.assertEqual(job_list, search)
        self.assertEqual(trace["selected_from"], "verified_js_inventory_transport")
        self.assertEqual(trace["js_inventory_transports"][0]["status"], "declared")
        self.assertEqual(discovered.board.provider, "generic")
        self.assertEqual(
            discovered.detection_method,
            "verified_declared_inventory",
        )
        self.assertEqual(fetcher.requested, [root, search, asset])

    def test_declared_inventory_is_inspected_on_verified_career_root(self):
        root = "https://careers.example.com/"
        asset = "https://careers.example.com/assets/page-careers.js"
        script = """
            var url = window.settings.homeUrl + '/wp-json/example/jobs';
            url += "&keyword=" + encodeURIComponent(this.search.keyword);
            url += "&limit=" + this.jobsToShow;
            axios.get(url).then(function (response) {
                response.data.map(function (job) { if (job.title) render(job); });
            });
        """
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=f'<main>Available jobs</main><script src="{asset}"></script>',
            ),
            asset: Page(url=asset, final_url=asset, html=script),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=1,
        ).find_job_board_with_evidence(root)

        self.assertEqual(job_list, root)
        self.assertEqual(trace["selected_from"], "verified_js_inventory_transport")
        self.assertEqual(discovered.detection_method, "verified_declared_inventory")
        self.assertEqual(fetcher.requested, [root, asset])

    def test_view_all_open_roles_precedes_department_scoped_open_roles(self):
        root = "https://www.example.com/careers"
        department = "https://www.example.com/jobs?department=Retail"
        all_roles = "https://www.example.com/jobs-beta"
        opening = "https://www.example.com/jobs/123/hr-manager"
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=(
                    f'<a href="{department}">View Open Roles</a>'
                    f'<a href="{all_roles}">View all open roles</a>'
                ),
            ),
            all_roles: Page(
                url=all_roles,
                html=(
                    '<article class="job-card"><h3>HR Manager</h3>'
                    f'<a href="{opening}">HR Manager</a></article>'
                ),
            ),
        })

        job_list, _trace = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board(root)

        self.assertEqual(job_list, all_roles)
        self.assertEqual(fetcher.requested, [root, all_roles])

    def test_traverses_official_careers_subdomain_without_promoting_link_alone(self):
        root = "https://www.example.com/about/culture-careers"
        destination = "https://careers.example.com/"
        opening = "https://careers.example.com/jobs/42/ux-designer"
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=f'<a href="{destination}">Careers at Example</a>',
            ),
            destination: Page(
                url=destination,
                html=(
                    '<article class="job-card"><h3>UX Designer</h3>'
                    f'<a href="{opening}">UX Designer</a></article>'
                ),
            ),
        })

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(root)

        self.assertEqual(job_list, destination)
        self.assertEqual(fetcher.requested, [root, destination])
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.relationship_evidence_url, root)

    def test_traverses_exact_same_host_work_with_us_destination(self):
        root = "https://example.com/scholarship-opportunities"
        destination = "https://example.com/work-with-us/"
        opening = "https://example.com/jobs/42/project-manager"
        fetcher = MappingFetcher({
            root: Page(url=root, html=f'<a href="{destination}">Careers</a>'),
            destination: Page(
                url=destination,
                html=f'<a href="{opening}">Project Manager</a>',
            ),
        })

        job_list, _trace = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board(root)

        self.assertEqual(job_list, destination)
        self.assertEqual(fetcher.requested, [root, destination])

    def test_reserves_official_career_destination_beyond_candidate_cap(self):
        source = "https://www.example.com/about/culture-careers"
        destination = "https://careers.example.com/"
        candidates = [
            LinkCandidate(
                url=f"https://www.example.com/about/topic-{index}",
                text=f"Topic {index}",
                source_url=source,
                score=100 - index,
                origin="page_link",
            )
            for index in range(3)
        ]
        candidates.append(
            LinkCandidate(
                url=destination,
                text="Careers at Example",
                source_url=source,
                score=0,
                origin="page_link",
            )
        )

        selected = JobSourceAgent(
            MappingFetcher({}),
            max_candidates=2,
        )._strong_same_site_listing_candidate(candidates, source, source)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.url, destination)

    def test_provider_config_after_official_destination_preserves_root_handoff(self):
        root = "https://example.com/scholarship-opportunities/"
        destination = "https://example.com/work-with-us/"
        board = "https://acme.bamboohr.com/careers"
        fetcher = MappingFetcher({
            root: Page(url=root, html=f'<a href="{destination}">Careers</a>'),
            destination: Page(
                url=destination,
                html=(
                    '<div id="BambooHR" data-domain="acme.bamboohr.com"></div>'
                    '<script src="https://acme.bamboohr.com/js/embed.js"></script>'
                ),
            ),
        })

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(root)

        self.assertEqual(job_list, board)
        self.assertEqual(fetcher.requested, [root, destination])
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered.relationship_evidence_url, root)

    def test_rejects_culture_and_cross_site_careers_destinations(self):
        root = "https://example.com/careers"
        culture = "https://example.com/about/culture-careers"
        cross_site = "https://careers.unrelated.example.net/"
        fetcher = MappingFetcher({
            root: Page(
                url=root,
                html=(
                    f'<a href="{culture}">Careers</a>'
                    f'<a href="{cross_site}">Careers at Example</a>'
                ),
            ),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(root)

        self.assertEqual(fetcher.requested, [root])

    def test_unlabeled_route_is_ignored_and_explicit_cross_site_action_is_probed(self):
        career = "https://careers.example.com/en/"
        unlabeled = "https://careers.example.com/en/all-jobs/"
        cross_site = "https://careers.unrelated.example.net/en/all-jobs/"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{unlabeled}"></a>'
                    f'<a href="{cross_site}">Search Jobs</a>'
                ),
            ),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(fetcher.requested, [career, cross_site])

    def test_traverses_same_site_search_jobs_subdomain(self):
        career = "https://www.example.com/careers/job-search-tips"
        jobs = "https://jobs.example.com/"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{jobs}">Search Jobs</a>',
            ),
            jobs: Page(
                url=jobs,
                html='<a href="/jobs/12345/mechanical-design-engineer">Mechanical Design Engineer</a>',
            ),
        })

        job_list, _trace = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board(career)

        self.assertEqual(job_list, jobs)
        self.assertEqual(fetcher.requested, [career])

    def test_provider_asset_probe_rejects_credentials_and_cross_site_assets(self):
        career = "https://www.example.com/careers/"
        credentialed = "https://user@www.example.com/assets/page-careers.js"
        cross_site = "https://cdn.evil.example/assets/page-careers.js"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<link rel="modulepreload" href="{credentialed}">'
                    f'<link rel="modulepreload" href="{cross_site}">'
                ),
            ),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=1).find_job_board(career)

        self.assertEqual(fetcher.requested, [career])

    def test_invalid_identity_career_root_falls_back_to_verified_homepage_link(self):
        homepage = "https://example.com"
        wrong = "https://example.com/careers-channel"
        correct = "https://careers.example.com/jobs"
        fetcher = MappingFetcher({
            homepage: Page(url=homepage, html=f'<a href="{correct}">Careers</a>'),
            wrong: Page(url=wrong, html="<html>Videos and live streams</html>"),
            correct: Page(url=correct, html="<html>Explore open roles and apply now</html>"),
        })

        career, trace = JobSourceAgent(fetcher, max_job_pages=1).find_career_page(
            homepage,
            company_name="Example",
            preferred_url=wrong,
        )

        self.assertEqual(career, correct)
        self.assertEqual(trace["preferred_career_root"], wrong)
        self.assertIn(wrong, fetcher.requested)
        self.assertIn(correct, fetcher.requested)

    def test_identity_career_root_needs_strong_employment_semantics(self):
        homepage = "https://example.com"
        wrong = "https://example.com/careers"
        correct = "https://careers.example.com/jobs"
        fetcher = MappingFetcher({
            homepage: Page(url=homepage, html=f'<a href="{correct}">Careers</a>'),
            wrong: Page(url=wrong, html="<html><title>Careers channel</title>Videos and streams</html>"),
            correct: Page(url=correct, html="<html>Search jobs and explore open roles</html>"),
        })

        career, _trace = JobSourceAgent(fetcher).find_career_page(
            homepage,
            company_name="Example",
            preferred_url=wrong,
        )

        self.assertEqual(career, correct)

    def test_corporate_careers_title_is_enough_without_channel_markers(self):
        homepage = "https://example.com"
        careers = "https://example.com/careers"
        fetcher = MappingFetcher({
            homepage: Page(url=homepage, html="<html>Example</html>"),
            careers: Page(url=careers, html="<html><title>Careers | Example</title><main>Build with us</main></html>"),
        })

        selected, _trace = JobSourceAgent(fetcher).find_career_page(
            homepage,
            company_name="Example",
            preferred_url=careers,
        )

        self.assertEqual(selected, careers)

    def test_generated_career_path_does_not_pass_on_word_careers_alone(self):
        homepage = "https://example.com"
        generated = "https://example.com/careers"
        fetcher = MappingFetcher({
            homepage: Page(url=homepage, html="<html>Example homepage</html>"),
            generated: Page(url=generated, html="<html><title>Careers channel</title>Videos</html>"),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_ats_board_fetches=0).find_career_page(
                homepage,
                company_name=None,
            )

        self.assertIn(generated, fetcher.requested)

    def test_discovers_hidden_oracle_list_root_from_official_link(self):
        career = "https://example.com/careers"
        board = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<div data-jobs-url="{board}"></div>'),
            board: Page(url=board, html="<html>Search jobs</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(fetcher.requested, [career])
        self.assertEqual(trace["provider"], "oracle_hcm")
        self.assertEqual(
            trace["provider_detection"]["method"],
            "embedded_provider_url_evidence",
        )
        self.assertEqual(trace["provider_detection"]["url"], board)

    def test_follows_escaped_eightfold_root_but_not_untrusted_job_url(self):
        career = "https://example.com/careers"
        board = "https://acme.eightfold.ai/careers"
        html = '<script>"https:\\/\\/evil.example.net\\/jobs";"https:\\/\\/acme.eightfold.ai\\/careers"</script>'
        fetcher = MappingFetcher({
            career: Page(url=career, html=html),
            board: Page(url=board, html="<html>Open positions</html>"),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertNotIn("https://evil.example.net/jobs", fetcher.requested)

    def test_follows_known_ats_embed_board_and_returns_its_canonical_root(self):
        career = "https://example.com/careers"
        embed = "https://jobs.ashbyhq.com/Acme/embed?version=2"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<iframe src="{embed}"></iframe>'),
            embed: Page(url=embed, html="<html>Ashby job board</html>"),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/Acme")

    def test_hidden_known_ats_detail_is_promoted_before_generic_detail_acceptance(self):
        career = "https://example.com/careers"
        detail = "https://jobs.ashbyhq.com/acme/06d5624e-d35c-41b1-a091-edfc79c10dba"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<script>"{detail}"</script>'),
            detail: Page(url=detail, html="<html>Ashby posting</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, "https://jobs.ashbyhq.com/acme")
        self.assertEqual(trace["provider"], "ashby")
        self.assertEqual(trace["provider_detection"]["method"], "linked_url_evidence")
        self.assertEqual(fetcher.requested, [career])

    def test_visible_canonical_whitecarrot_board_is_handed_to_provider(self):
        career = "https://smart-bricks.com/company/careers/open-roles"
        board = "https://app.whitecarrot.io/careers/smart-bricks"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{board}">See Open Roles</a>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(trace["provider"], "whitecarrot")
        self.assertEqual(trace["provider_detection"]["method"], "linked_url_evidence")
        self.assertEqual(fetcher.requested, [career])

    def test_visible_taleo_board_outside_candidate_cap_is_handed_to_provider(self):
        career = "https://example.com/careers"
        board = "https://jobs.example.net/careersection/percepta/jobsearch.ftl"
        low_ranked_links = "".join(
            f'<a href="https://example.com/jobs/{index}">Software Engineer {index}</a>'
            for index in range(8)
        )
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=low_ranked_links + f'<a href="{board}">Percepta opportunities</a>',
            ),
        })

        job_list, trace = JobSourceAgent(
            fetcher,
            max_candidates=2,
            max_job_pages=1,
        ).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(trace["provider"], "taleo")
        self.assertEqual(trace["provider_detection"]["method"], "linked_url_evidence")
        self.assertEqual(fetcher.requested, [career])

    def test_follows_registry_backed_paycom_board_outside_static_ats_domains(self):
        career = "https://example.com/careers"
        client_key = "AA674B442E9B6A1284BD7F78CB0C3E73"
        legacy = (
            "https://www.paycomonline.net/v4/ats/web.php/jobs"
            f"?clientkey={client_key}&session_nonce=ephemeral"
        )
        canonical = (
            "https://www.paycomonline.net/v4/ats/web.php/portal/"
            f"{client_key}/career-page"
        )
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{legacy}">Explore opportunities</a>'),
            legacy: Page(url=legacy, final_url=canonical, html="<html>Paycom portal</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, canonical)
        self.assertEqual(trace["provider"], "paycom")
        self.assertEqual(fetcher.requested, [career, legacy])

    def test_traverses_staff_category_and_accepts_explicit_first_party_jobs_portal(self):
        career = "https://www.example.com/careers"
        students = "https://www.example.com/careers/united-states/law-students"
        staff = "https://www.example.com/careers/united-states/staff"
        portal = "https://staffjobsus.example.com/"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{students}">Law Students</a>'
                    f'<a href="{staff}">Staff</a>'
                ),
            ),
            staff: Page(
                url=staff,
                html=f'<a href="{portal}">Explore U.S. Staff Job Opportunities</a>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, portal)
        self.assertEqual(trace["selected_page_source"], "first_party_portal_link")
        self.assertEqual(fetcher.requested, [career, staff])

    def test_probes_but_does_not_publish_unverified_external_job_action(self):
        career = "https://www.example.com/careers"
        external = "https://jobs.unrelated.example.net/"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{external}">Explore Job Opportunities</a>',
            ),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)
        self.assertEqual(fetcher.requested, [career, external])

    def test_accepts_same_brand_apply_subdomain_with_explicit_job_search_command(self):
        career = "https://www.example.com/us/en/careers/careers.html"
        portal = "https://apply.example.com"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{portal}">Job search</a>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, portal)
        self.assertEqual(trace["selected_page_source"], "first_party_portal_link")
        self.assertEqual(fetcher.requested, [career])

    def test_accepts_same_brand_jobs_subdomain_with_find_jobs_command(self):
        career = "https://www.example.com/careers/"
        portal = "https://jobs.example.com/en/"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{portal}">Find jobs</a>',
            ),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, portal)
        self.assertEqual(trace["selected_page_source"], "first_party_portal_link")
        self.assertEqual(fetcher.requested, [career])

    def test_probes_explicit_cross_site_roles_link_but_requires_provider_page_evidence(self):
        career = "https://jobs.example.com"
        board = "https://explore.jobs.example.net/careers"
        state = html.escape(json.dumps({
            "domain": "example.com",
            "positions": [],
            "count": 0,
            "isPcsEnabled": True,
        }))
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{board}">View Roles</a>'),
            board: Page(url=board, html=f'<code id="smartApplyData">{state}</code>'),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual(trace["provider"], "eightfold")
        self.assertEqual(trace["provider_detection"]["method"], "page_evidence")

    def test_follows_explicit_cross_site_root_job_action_and_records_disposition(self):
        career = "https://www.example.com/careers"
        portal = "https://example-hiring.test"
        opening = "https://example-hiring.test/jobs/registered-nurse-123"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{portal}">VIEW ALL AVAILABLE JOBS AND APPLY</a>'
                    f'<a href="{portal}/jobs?internal=true">Internal Applicants Only</a>'
                ),
            ),
            portal: Page(
                url=portal,
                html=(
                    '<article class="job-card">'
                    '<h2>Registered Nurse</h2>'
                    f'<a href="{opening}">Registered Nurse</a>'
                    '</article>'
                ),
            ),
        })

        job_list, trace = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board(career)

        self.assertEqual(job_list, portal)
        self.assertEqual(fetcher.requested, [career, portal])
        actions = {item["target_url"]: item for item in trace["career_actions"]}
        self.assertEqual(actions[portal]["status"], "visited")
        self.assertEqual(
            actions[f"{portal}/jobs?internal=true"]["status"],
            "rejected_internal",
        )

    def test_cross_site_job_action_without_listing_evidence_is_not_published(self):
        career = "https://www.example.com/careers"
        portal = "https://unverified-hiring.test"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=f'<a href="{portal}">View Job Postings</a>',
            ),
            portal: Page(url=portal, html="<main>Marketing content only</main>"),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(fetcher.requested, [career, portal])

    def test_scoped_cross_site_job_search_action_publishes_board_only(self):
        career = "https://careers.example.com"
        portal = (
            "https://parent.example.net/careers/jobs?"
            "businessUnits%5B%5D=source"
        )
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=f'<a href="{portal}">Search jobs</a>',
                ),
                portal: Page(url=portal, html="<main>Dynamic job search</main>"),
            }
        )

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, portal)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.detection_method, "verified_first_party_action")
        self.assertEqual(trace["selected_from"], "verified_scoped_first_party_action")

    def test_redirect_recovers_observed_scoped_cross_site_action_provenance(self):
        career = "https://careers.example.com"
        local_jobs = "https://careers.example.com/jobs"
        portal = (
            "https://parent.example.net/careers/jobs?"
            "businessUnits%5B%5D=source"
        )
        fetcher = MappingFetcher(
            {
                career: Page(
                    url=career,
                    html=(
                        f'<a href="{portal}">Search jobs</a>'
                        f'<a href="{local_jobs}">Jobs</a>'
                    ),
                ),
                local_jobs: Page(
                    url=local_jobs,
                    final_url=portal,
                    html="<main>Dynamic job search</main>",
                ),
            }
        )

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, portal)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.relationship_evidence_url, career)

    def test_rejects_explicit_cross_site_roles_link_without_provider_evidence(self):
        career = "https://jobs.example.com"
        external = "https://careers.unrelated.example.net/careers"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{external}">View Roles</a>'),
            external: Page(url=external, html="<html>Unverified external page</html>"),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)
        self.assertEqual(fetcher.requested, [career, external])

    def test_probes_acquired_brand_portal_with_parent_identity_and_typed_provider(self):
        career = "https://www.source.example/careers/"
        portal = "https://jobs.parent.example/en/"

        class ParentPortalAdapter:
            name = "parent_portal"
            supports_listing = True

            def recognizes(self, _url):
                return False

            def identify_board(self, _url):
                return None

            def identify_board_from_page(self, page):
                if 'data-provider="parent"' not in page.html:
                    return None
                return JobBoard(url=page.final_url or page.url, provider=self.name)

            def list_jobs(self, fetcher, board, query):
                raise AssertionError("inventory is outside S5 discovery")

        source_html = (
            '<section class="career-callout">'
            '<p>Source Brand is now a Parent Corp company.</p>'
            f'<a href="{portal}">Search All Jobs</a>'
            "</section>"
        )
        target_html = (
            f'<link rel="canonical" href="{portal}">'
            '<meta property="og:site_name" content="Parent Corp Careers">'
            '<div data-provider="parent"></div>'
        )
        fetcher = MappingFetcher({
            career: Page(url=career, html=source_html),
            portal: Page(url=portal, html=target_html),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((ParentPortalAdapter(),)),
            max_job_pages=2,
        ).find_job_board_with_evidence(career, company_name="Source Brand")

        self.assertEqual(job_list, portal)
        self.assertEqual(discovered.board.provider, "parent_portal")
        self.assertEqual(discovered.detection_method, "acquired_brand_handoff")
        self.assertTrue(trace["acquired_brand_handoff"]["verified"])
        self.assertEqual(trace["selected_page_source"], "acquired_brand_portal")
        self.assertEqual(fetcher.requested, [career, portal])

    def test_rejects_acquired_brand_portal_when_parent_metadata_mismatches(self):
        career = "https://www.source.example/careers/"
        portal = "https://jobs.parent.example/en/"
        source_html = (
            '<section><p>Source Brand is now a Parent Corp company.</p>'
            f'<a href="{portal}">Search All Jobs</a></section>'
        )
        target_html = (
            f'<link rel="canonical" href="{portal}">'
            '<meta property="og:site_name" content="Unrelated Careers">'
        )
        fetcher = MappingFetcher({
            career: Page(url=career, html=source_html),
            portal: Page(url=portal, html=target_html),
        })

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(
                fetcher,
                max_job_pages=2,
                max_ats_board_fetches=0,
            ).find_job_board(
                career,
                company_name="Source Brand",
            )

        self.assertEqual(
            raised.exception.trace["acquired_brand_handoff"]["reason"],
            "parent portal identity mismatch",
        )
        self.assertEqual(fetcher.requested, [career, portal])

    def test_acquired_brand_handoff_types_talentbrew_parent_board(self):
        career = "https://www.source.example/careers/"
        portal = "https://jobs.parent.example/en/"
        board = "https://jobs.parent.example/en/search-jobs"
        source_html = (
            '<section><p>Source Brand is now a Parent Corp company.</p>'
            f'<a href="{portal}">Search All Jobs</a></section>'
        )
        target_html = f"""
          <link rel="canonical" href="{portal}">
          <meta property="og:site_name" content="Parent Corp Careers">
          <meta name="site-tenant-id" content="47263">
          <meta name="site-organization-id" content="47263">
          <meta name="site-id" content="62886">
          <meta name="gtm_tenantid" content="47263">
          <meta name="gtm_companysiteid" content="62886">
          <meta name="site-current-language" content="en">
          <meta name="site-url-modified-language-code" content="en">
          <link rel="stylesheet"
                href="https://tbcdn.talentbrew.com/company/47263/css/62886.css">
          <form action="{board}" method="GET">
            <input name="k" type="search">
            <input name="l" type="text">
            <input name="orgIds" type="hidden" value="47263">
          </form>
        """
        fetcher = MappingFetcher({
            career: Page(url=career, html=source_html),
            portal: Page(url=portal, html=target_html),
        })

        job_list, trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=2,
            max_ats_board_fetches=0,
        ).find_job_board_with_evidence(career, company_name="Source Brand")

        self.assertEqual(job_list, board)
        self.assertEqual(discovered.board.provider, "talentbrew")
        self.assertTrue(discovered.board.replay_safe)
        self.assertEqual(trace["provider"], "talentbrew")
        self.assertEqual(fetcher.requested, [career, portal])

    def test_listing_traversal_prefers_route_that_preserves_locale_prefix(self):
        career = "https://careers.example.com/world/en"
        alias = "https://careers.example.com/world/search-results"
        localized = "https://careers.example.com/world/en/search-results"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{alias}">Search results</a>'
                    f'<a href="{localized}">Search results</a>'
                ),
            ),
            localized: Page(url=localized, html="<html>Search open roles</html>"),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, localized)
        self.assertNotIn(alias, fetcher.requested)

    def test_redirect_back_to_visited_root_does_not_consume_page_budget(self):
        career = "https://careers.example.com/global/en"
        alias = "https://careers.example.com/global/search-results"
        localized = "https://careers.example.com/global/en/search-results"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    f'<a href="{alias}">Engineer search results</a>'
                    f'<a href="{localized}">Search results</a>'
                ),
            ),
            alias: Page(url=alias, final_url=career, html="<html>Career root</html>"),
            localized: Page(url=localized, html="<html>Search open roles</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, localized)
        self.assertEqual(fetcher.requested, [career, alias, localized])
        self.assertTrue(trace["pages_visited"][1]["redirect_duplicate"])

    def test_redirect_final_url_is_used_as_board_evidence(self):
        career = "https://example.com/careers"
        legacy_board = "https://boards.greenhouse.io/acme"
        canonical_board = "https://job-boards.greenhouse.io/acme"
        fetcher = MappingFetcher({
            career: Page(url=career, final_url=legacy_board, html="<html></html>")
        })

        job_list, _trace, discovered = JobSourceAgent(
            fetcher,
            max_job_pages=1,
        ).find_job_board_with_evidence(career)

        self.assertEqual(job_list, canonical_board)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.board.url, canonical_board)
        self.assertEqual(discovered.evidence_url, canonical_board)

    def test_does_not_traverse_credentials_or_nonstandard_ports(self):
        career = "https://example.com/careers"
        fetcher = MappingFetcher({
            career: Page(
                url=career,
                html=(
                    '<a href="https://user:secret@example.com/jobs">Jobs</a>'
                    '<a href="https://example.com:8443/jobs">Jobs</a>'
                ),
            ),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)
        self.assertEqual(fetcher.requested, [career])

    def test_oracle_login_link_is_not_promoted_to_listing_root(self):
        career = "https://jobs.example.com/en/"
        root = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/AcmeCareers"
        login = f"{root}/my-profile/sign-in"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{login}">Login</a>'),
        })

        with self.assertRaises(DiscoveryError):
            JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)
        self.assertEqual(fetcher.requested, [career])

    def test_generic_career_page_is_not_reported_as_job_list_without_listing_evidence(self):
        career = "https://example.com/people"
        fetcher = MappingFetcher({
            career: Page(url=career, html="<html>Meet our people and explore our culture</html>"),
        })

        with self.assertRaises(DiscoveryError) as raised:
            JobSourceAgent(fetcher, max_job_pages=1).find_job_board(career)

        self.assertEqual(raised.exception.code, "job_board_not_found")

    def test_traversed_first_party_search_route_becomes_job_list(self):
        career = "https://example.com/people"
        search = "https://example.com/careers/career-opportunities-search"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{search}">Opportunities</a>'),
            search: Page(url=search, html="<html>Search career opportunities</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, search)
        self.assertEqual([page["url"] for page in trace["pages_visited"]], [career, search])

    def test_allows_company_www_to_careers_subdomain_transition(self):
        career = "https://careers.example.com/international"
        jobs = "https://www.example.com/careers/jobs"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{jobs}">USA jobs</a>'),
            jobs: Page(url=jobs, html='<a href="/careers/jobs/123/software-engineer">Software Engineer</a>'),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, jobs)

    def test_generic_career_root_uses_verified_ats_search_fallback(self):
        career = "https://www.glean.com/careers"
        detail = "https://job-boards.greenhouse.io/gleanwork/jobs/4006734005"
        api = "https://boards-api.greenhouse.io/v1/boards/gleanwork/jobs?content=true"

        class SearchFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if url == career:
                    return Page(url=url, html="<html>Careers at Glean</html>")
                if "bing.com" in url and "format=rss" in url:
                    return Page(
                        url=url,
                        html=f"<rss><channel><item><link>{detail}</link></item></channel></rss>",
                    )
                if url == api:
                    return Page(
                        url=url,
                        html=json.dumps({"jobs": [{"title": "Software Engineer, Fullstack", "absolute_url": detail}]}),
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = SearchFetcher()
        job_list, trace = JobSourceAgent(fetcher, max_job_pages=1).find_job_board(
            career,
            company_name="Glean",
        )

        self.assertEqual(job_list, "https://job-boards.greenhouse.io/gleanwork")
        self.assertEqual(trace["selected_from"], "ats_search_fallback")
        self.assertIn(api, fetcher.requested)

    def test_speculative_tenant_requires_target_title_match(self):
        board = "https://jobs.smartrecruiters.com/glean"
        api = "https://api.smartrecruiters.com/v1/companies/glean/postings?limit=100"
        payload = json.dumps({
            "content": [
                {
                    "name": "Senior Software Engineer, Backend",
                    "ref": "https://jobs.smartrecruiters.com/glean/123-backend",
                }
            ]
        })
        fetcher = MappingFetcher({api: Page(url=api, html=payload)})
        agent = JobSourceAgent(fetcher)

        verified, trace = agent._verify_derived_provider_board(
            board,
            "",
            target_title="Software Engineer, Fullstack",
        )

        self.assertFalse(verified)
        self.assertEqual(trace["title_match_count"], 0)

    def test_speculative_native_adapter_rejects_valid_wrong_company_inventory(self):
        agent = JobSourceAgent(Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True))

        rejected = agent._verify_derived_provider_with_adapter(
            "https://jobs.smartrecruiters.com/AcmeApi",
            target_title="Quantum Archaeologist",
            trusted_configuration=False,
        )
        accepted = agent._verify_derived_provider_with_adapter(
            "https://jobs.smartrecruiters.com/AcmeApi",
            target_title="Data Analyst",
            trusted_configuration=False,
        )

        self.assertIsNotNone(rejected)
        self.assertIsNone(rejected[0])
        self.assertEqual(rejected[1]["method"], "native_adapter_first")
        self.assertGreater(rejected[1]["candidate_count"], 0)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted[0], "https://jobs.smartrecruiters.com/AcmeApi")

    def test_speculative_native_adapter_accepts_verified_tenant_without_title_match(self):
        board = "https://jobs.smartrecruiters.com/Centraprise"
        api = (
            "https://api.smartrecruiters.com/v1/companies/Centraprise/postings"
            "?limit=100&q=AI%2FML+Engineer"
        )
        payload = json.dumps(
            {
                "totalFound": 1,
                "limit": 100,
                "content": [
                    {
                        "name": "Backend Engineer",
                        "id": "job-1",
                        "company": {
                            "identifier": "Centraprise",
                            "name": "Centraprise",
                        },
                    }
                ],
            }
        )
        agent = JobSourceAgent(MappingFetcher({api: Page(url=api, html=payload)}))

        verified = agent._verify_derived_provider_with_adapter(
            board,
            target_title="AI/ML Engineer",
            trusted_configuration=False,
        )

        self.assertIsNotNone(verified)
        self.assertEqual(verified[0], board)
        self.assertEqual(verified[1]["title_match_count"], 0)
        self.assertTrue(verified[1]["adapter_trace"]["tenant_identity_verified"])


if __name__ == "__main__":
    unittest.main()
