import unittest

from job_source_agent.web import Fetcher
from job_source_agent.website_resolver import CompanyWebsiteResolver, is_blocked_domain


class WebsiteResolverTests(unittest.TestCase):
    def test_linkedin_static_asset_domains_are_blocked(self):
        self.assertTrue(is_blocked_domain("https://media.licdn.com"))
        self.assertTrue(is_blocked_domain("https://static.licdn.com"))
        self.assertTrue(is_blocked_domain("https://dms.licdn.com"))

    def test_linkedin_slug_can_hint_domain_tld(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        dot_com = resolver._score_candidate(
            "https://tesseralabs.com",
            "Tessera Labs",
            linkedin_company_url="https://www.linkedin.com/company/tesseralabsai",
            verify=False,
        )
        dot_ai = resolver._score_candidate(
            "https://tesseralabs.ai",
            "Tessera Labs",
            linkedin_company_url="https://www.linkedin.com/company/tesseralabsai",
            verify=False,
        )

        self.assertGreater(dot_ai.score, dot_com.score)
        self.assertIn("LinkedIn company slug matches domain TLD", dot_ai.reasons)


if __name__ == "__main__":
    unittest.main()
