import unittest

from job_source_agent.errors import DiscoveryError
from job_source_agent.models import LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page


class StaticFetcher(Fetcher):
    def __init__(self, pages):
        super().__init__(offline=True)
        self.pages = pages

    def fetch(self, url, data=None, headers=None):
        page = self.pages.get(url)
        if page is None:
            raise FetchError(f"fixture miss: {url}")
        return page


def build_agent(pages):
    return JobSourceAgent(
        StaticFetcher(pages),
        max_career_candidate_fetches=1,
        max_ats_board_fetches=0,
        enable_sitemap_discovery=False,
        enable_career_search=False,
    )


class CareerSurfaceDetectionTests(unittest.TestCase):
    def select_candidate(self, html, *, company_name="Coforge"):
        url = "https://www.coforge.example/career"
        agent = build_agent({url: Page(url=url, final_url=url, html=html)})
        trace = {"candidate_fetch_errors": []}
        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    url=url,
                    text="Career",
                    source_url="https://www.coforge.example",
                    score=250,
                    reasons=["homepage navigation link"],
                )
            ],
            trace,
            max_fetches=1,
            company_name=company_name,
            homepage_url="https://www.coforge.example",
        )
        return selected

    def test_accepts_company_bound_singular_career_metadata(self):
        selected = self.select_candidate(
            '<html><head><title>Career | Coforge</title>'
            '<meta property="og:site_name" content="Coforge"></head>'
            '<body><p>Build what comes next.</p></body></html>'
        )

        self.assertEqual(selected, "https://www.coforge.example/career")

    def test_rejects_unrelated_singular_career_and_marketing_surfaces(self):
        cases = (
            "<html><title>Career | Other Company</title><body>Career</body></html>",
            "<html><title>Coforge Culture</title><body><nav>Careers</nav></body></html>",
        )
        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(self.select_candidate(html))

    def test_accepts_verified_homepage_with_strong_surface_metadata(self):
        homepage = "https://foxtech.example"
        html = (
            '<html><head><title>FOX Jobs | Consumer Product and Engineering Careers</title>'
            '<meta property="og:site_name" content="FOX"></head>'
            '<body><h1>Open Positions</h1></body></html>'
        )

        selected, trace = build_agent(
            {homepage: Page(url=homepage, final_url=homepage, html=html, source="fixture")}
        ).find_career_page(homepage, company_name="FOX Tech")

        self.assertEqual(selected, homepage)
        self.assertEqual(trace["selected_from"], "verified_official_homepage")
        self.assertTrue(trace["homepage_career_surface_verification"]["verified"])

    def test_rejects_homepage_nav_weak_body_and_mismatched_redirect(self):
        homepage = "https://foxtech.example"
        cases = (
            Page(
                url=homepage,
                final_url=homepage,
                html=(
                    '<html><title>FOX Tech</title><h1>Technology services</h1>'
                    '<nav><a href="/careers">Careers</a></nav></html>'
                ),
            ),
            Page(
                url=homepage,
                final_url=homepage,
                html="<html><title>FOX Tech</title><p>Jobs power the economy.</p></html>",
            ),
            Page(
                url=homepage,
                final_url="https://other.example/careers",
                html="<html><title>Other Company Careers</title></html>",
            ),
        )
        for page in cases:
            with self.subTest(page=page):
                with self.assertRaises(DiscoveryError):
                    build_agent({homepage: page}).find_career_page(
                        homepage,
                        company_name="FOX Tech",
                    )


if __name__ == "__main__":
    unittest.main()
