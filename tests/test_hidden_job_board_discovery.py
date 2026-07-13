import unittest

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Page


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

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, career)
        self.assertEqual(fetcher.requested, [career])

    def test_oracle_login_link_is_canonicalized_to_site_root(self):
        career = "https://jobs.example.com/en/"
        root = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/AcmeCareers"
        login = f"{root}/my-profile/sign-in"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{login}">Login</a>'),
            root: Page(url=root, html="<html>Search jobs</html>"),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, root)
        self.assertEqual(fetcher.requested, [career, root])

    def test_allows_company_www_to_careers_subdomain_transition(self):
        career = "https://careers.example.com/international"
        jobs = "https://www.example.com/careers/jobs"
        fetcher = MappingFetcher({
            career: Page(url=career, html=f'<a href="{jobs}">USA jobs</a>'),
            jobs: Page(url=jobs, html='<a href="/careers/jobs/123/software-engineer">Software Engineer</a>'),
        })

        job_list, _trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career)

        self.assertEqual(job_list, jobs)


if __name__ == "__main__":
    unittest.main()
