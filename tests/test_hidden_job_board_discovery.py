import unittest
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


if __name__ == "__main__":
    unittest.main()
