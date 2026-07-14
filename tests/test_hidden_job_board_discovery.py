import unittest
import html
import json
from pathlib import Path

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.errors import DiscoveryError
from job_source_agent.web import FetchError, Fetcher, Page


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

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        probe = trace["content_payload_probes"][0]
        self.assertEqual(probe["method"], "first_party_provider_asset")
        self.assertEqual(probe["asset_urls"], [route_asset, shared_asset])
        self.assertEqual(probe["provider_urls"], [board])

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

    def test_traverses_explicit_same_site_all_jobs_route(self):
        career = "https://careers.example.com/en/"
        all_jobs = "https://careers.example.com/en/all-jobs/"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{all_jobs}">Search Jobs</a>'),
            all_jobs: Page(url=all_jobs, html="<main>Find your next role</main>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, all_jobs)
        self.assertEqual(trace["selected_from"], "explicit_first_party_listing_route")

    def test_does_not_traverse_unlabeled_or_cross_site_all_jobs_route(self):
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

    def test_follows_hidden_oracle_list_root(self):
        career = "https://example.com/careers"
        board = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<div data-jobs-url="{board}"></div>'),
            board: Page(url=board, html="<html>Search jobs</html>"),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, board)
        self.assertEqual([item["url"] for item in trace["pages_visited"]], [career, board])

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

    def test_does_not_accept_unrelated_external_job_opportunities_link(self):
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
        self.assertEqual(fetcher.requested, [career])

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
        board = "https://boards.greenhouse.io/acme"
        fetcher = MappingFetcher({career: Page(url=career, final_url=board, html="<html></html>")})

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=1).find_job_board(career)

        self.assertEqual(job_list, board)

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
