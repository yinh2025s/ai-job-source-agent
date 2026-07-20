import unittest

from job_source_agent.models import LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher, Page


OFFICIAL_URL = "https://www.acme.example"
REQUEST_URL = f"{OFFICIAL_URL}/careers"
CAREER_URL = "https://careers.hiring-platform.test/acme/careers"


def redirect_html(
    final_url=CAREER_URL,
    *,
    company="Acme Corporation",
    canonical=None,
    og_url=None,
    job_route=None,
    backlink=OFFICIAL_URL,
):
    canonical = final_url if canonical is None else canonical
    og_url = final_url if og_url is None else og_url
    job_route = f"{final_url.rstrip('/')}/jobs" if job_route is None else job_route
    parts = [
        "<html><head>",
        f"<title>Careers at {company}</title>",
        f'<meta property="og:site_name" content="{company}">',
        f'<link rel="canonical" href="{canonical}">',
        f'<meta property="og:url" content="{og_url}">',
        "</head><body>",
    ]
    if job_route:
        parts.append(f'<a href="{job_route}">Search jobs</a>')
    if backlink:
        parts.append(f'<a href="{backlink}">Acme corporate site</a>')
    parts.append("</body></html>")
    return "".join(parts)


class RedirectFetcher(Fetcher):
    def __init__(self, final_url, html):
        super().__init__(offline=True)
        self.final_url = final_url
        self.html = html

    def fetch(self, url, data=None, headers=None):
        return Page(
            url=url,
            final_url=self.final_url,
            html=self.html,
            source="fixture",
        )


class GenericOfficialCareerRedirectTests(unittest.TestCase):
    def select(
        self,
        *,
        request_url=REQUEST_URL,
        final_url=CAREER_URL,
        html=None,
        company_name="Acme Corporation",
        homepage_url=OFFICIAL_URL,
    ):
        agent = JobSourceAgent(
            RedirectFetcher(final_url, html or redirect_html(final_url)),
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    request_url,
                    "Careers",
                    homepage_url,
                    300,
                    ["homepage navigation link"],
                )
            ],
            trace,
            max_fetches=1,
            company_name=company_name,
            homepage_url=homepage_url,
        )
        return selected, trace

    def assert_redirect_rejected(self, **kwargs):
        selected, trace = self.select(**kwargs)
        self.assertIsNone(selected)
        self.assertIn("unverified cross-site redirect", trace["candidate_fetch_errors"][0]["error"])
        self.assertFalse(trace["generic_career_redirect_verification"][0]["verified"])
        return trace["generic_career_redirect_verification"][0]["reason"]

    def test_accepts_strictly_verified_generic_official_career_redirect_chain(self):
        selected, trace = self.select()

        self.assertEqual(selected, CAREER_URL)
        self.assertEqual(trace["selected_redirect_kind"], "generic_official_career_root")
        self.assertEqual(trace["selected_page_source"], "fixture")
        self.assertNotIn("redirect_provider_detection", trace)
        verification = trace["generic_career_redirect_verification"][0]
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["official_backlinks"], [OFFICIAL_URL])
        self.assertIn(f"{CAREER_URL}/jobs", verification["actionable_routes"])

    def test_accepts_search_microsite_only_with_complete_relationship_evidence(self):
        agent = JobSourceAgent(
            RedirectFetcher(CAREER_URL, redirect_html()),
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    CAREER_URL,
                    CAREER_URL,
                    "https://search.example/results",
                    320,
                    ["unverified branded career microsite search lead"],
                    "search_result",
                )
            ],
            trace,
            max_fetches=1,
            company_name="Acme Corporation",
            homepage_url=OFFICIAL_URL,
        )

        self.assertEqual(selected, CAREER_URL)
        self.assertEqual(
            trace["selected_redirect_kind"],
            "verified_search_career_microsite",
        )
        self.assertTrue(trace["search_microsite_verification"][0]["verified"])

    def test_rejects_same_name_search_microsite_without_official_backlink(self):
        html = redirect_html(company="Acme Corporation", backlink="")
        agent = JobSourceAgent(
            RedirectFetcher(CAREER_URL, html),
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    CAREER_URL,
                    CAREER_URL,
                    "https://search.example/results",
                    320,
                    ["unverified branded career microsite search lead"],
                    "search_result",
                )
            ],
            trace,
            max_fetches=1,
            company_name="Acme Corporation",
            homepage_url=OFFICIAL_URL,
        )

        self.assertIsNone(selected)
        verification = trace["search_microsite_verification"][0]
        self.assertFalse(verification["verified"])
        self.assertEqual(
            verification["reason"],
            "redirect page lacks official source-origin backlink",
        )

    def test_rejects_same_name_blossom_restaurant_search_result(self):
        restaurant_url = "https://blossomrestaurant.com.sg/careers.html"
        html = redirect_html(
            final_url=restaurant_url,
            company="Blossom",
            job_route="https://blossomrestaurant.com.sg/jobs",
            backlink="",
        )
        agent = JobSourceAgent(
            RedirectFetcher(restaurant_url, html),
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    restaurant_url,
                    "Blossom Careers",
                    "https://search.example/results",
                    320,
                    ["unverified branded career microsite search lead"],
                    "search_result",
                )
            ],
            trace,
            max_fetches=1,
            company_name="Blossom",
            homepage_url="https://www.blossom.net",
        )

        self.assertIsNone(selected)
        verification = trace["search_microsite_verification"][0]
        self.assertFalse(verification["verified"])
        self.assertIn(
            verification["reason"],
            {
                "requested URL lacks career intent",
                "redirect page lacks official source-origin backlink",
            },
        )

    def test_find_career_page_supplies_verified_homepage_context(self):
        class HomepageRedirectFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)

            def fetch(self, url, data=None, headers=None):
                if url == OFFICIAL_URL:
                    return Page(
                        url=url,
                        final_url=url,
                        html=f'<html><a href="{REQUEST_URL}">Careers</a></html>',
                        source="fixture",
                    )
                if url == REQUEST_URL:
                    return Page(
                        url=url,
                        final_url=CAREER_URL,
                        html=redirect_html(),
                        source="fixture",
                    )
                raise AssertionError(f"unexpected candidate fetch: {url}")

        selected, trace = JobSourceAgent(
            HomepageRedirectFetcher(),
            max_career_candidate_fetches=1,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        ).find_career_page(OFFICIAL_URL, company_name="Acme Corporation")

        self.assertEqual(selected, CAREER_URL)
        self.assertEqual(trace["selected_redirect_kind"], "generic_official_career_root")

    def test_rejects_open_redirect_query_targets(self):
        for key in ("next", "url", "redirect", "continue"):
            with self.subTest(key=key):
                reason = self.assert_redirect_rejected(
                    request_url=f"{REQUEST_URL}?{key}=https%3A%2F%2Fevil.example"
                )
                self.assertIn("open-redirect query", reason)

    def test_rejects_redirect_request_not_originating_on_official_site(self):
        reason = self.assert_redirect_rejected(
            request_url="https://directory.example.net/acme/careers"
        )
        self.assertIn("did not originate on the official site", reason)

    def test_rejects_non_https_non_default_port_and_credentials(self):
        unsafe_requests = (
            "http://www.acme.example/careers",
            "https://www.acme.example:8443/careers",
            "https://user:secret@www.acme.example/careers",
        )
        for request_url in unsafe_requests:
            with self.subTest(request_url=request_url):
                reason = self.assert_redirect_rejected(request_url=request_url)
                self.assertIn("credential-free HTTPS on port 443", reason)

    def test_rejects_login_challenge_media_blog_cdn_and_tracking_surfaces(self):
        unsafe_final_urls = (
            "https://careers.hiring-platform.test/login",
            "https://careers.hiring-platform.test/challenge",
            "https://careers.hiring-platform.test/media/careers",
            "https://careers.hiring-platform.test/blog/careers",
            "https://cdn.hiring-platform.test/acme/careers",
            "https://careers.hiring-platform.test/tracking/careers",
        )
        for final_url in unsafe_final_urls:
            with self.subTest(final_url=final_url):
                reason = self.assert_redirect_rejected(
                    final_url=final_url,
                    html=redirect_html(final_url),
                )
                self.assertIn("disallowed surface", reason)

    def test_rejects_cross_origin_canonical_even_with_same_origin_og_url(self):
        reason = self.assert_redirect_rejected(
            html=redirect_html(canonical="https://identity.evil.example/acme/careers")
        )
        self.assertIn("canonical or og:url crosses origin", reason)

    def test_rejects_company_identity_mismatch(self):
        reason = self.assert_redirect_rejected(html=redirect_html(company="Other Company"))
        self.assertIn("company identity mismatch", reason)

    def test_rejects_page_without_same_origin_job_route(self):
        for job_route in ("", "https://jobs.other-platform.test/acme/jobs"):
            with self.subTest(job_route=job_route):
                reason = self.assert_redirect_rejected(html=redirect_html(job_route=job_route))
                self.assertIn("same-origin job route", reason)

    def test_rejects_page_without_official_source_origin_backlink(self):
        for backlink in ("", "https://about.other-company.test"):
            with self.subTest(backlink=backlink):
                reason = self.assert_redirect_rejected(html=redirect_html(backlink=backlink))
                self.assertIn("official source-origin backlink", reason)

    def test_accepts_company_bound_regional_corporate_sibling_backlink(self):
        regional_homepage = "https://www.acme.com.sg"
        selected, trace = self.select(
            request_url=f"{regional_homepage}/careers",
            homepage_url=regional_homepage,
            html=redirect_html(backlink="https://www.acme.com/about"),
        )

        self.assertEqual(selected, CAREER_URL)
        self.assertEqual(
            trace["generic_career_redirect_verification"][0]["official_backlinks"],
            ["https://www.acme.com/about"],
        )

    def test_rejects_unrelated_and_lookalike_corporate_sibling_backlinks(self):
        regional_homepage = "https://www.acme.com.sg"
        unsafe_siblings = (
            "https://www.other.com/about",
            "https://www.acme-careers.com/about",
            "https://www.acmes.com/about",
            "https://www.acme.evil.com/about",
        )
        for backlink in unsafe_siblings:
            with self.subTest(backlink=backlink):
                reason = self.assert_redirect_rejected(
                    request_url=f"{regional_homepage}/careers",
                    homepage_url=regional_homepage,
                    html=redirect_html(backlink=backlink),
                )
                self.assertIn("official source-origin backlink", reason)

    def test_rejects_sibling_brand_not_bound_to_company_identity(self):
        regional_homepage = "https://www.acme.com.sg"
        reason = self.assert_redirect_rejected(
            request_url=f"{regional_homepage}/careers",
            homepage_url=regional_homepage,
            company_name="Other Corporation",
            html=redirect_html(
                company="Other Corporation",
                backlink="https://www.acme.com/about",
            ),
        )
        self.assertIn("official source-origin backlink", reason)

    def test_same_origin_career_links_do_not_count_as_corporate_backlink(self):
        regional_homepage = "https://www.acme.com.sg"
        same_brand_career_url = "https://careers.acme.com/careers"
        reason = self.assert_redirect_rejected(
            request_url=f"{regional_homepage}/careers",
            homepage_url=regional_homepage,
            final_url=same_brand_career_url,
            html=redirect_html(
                same_brand_career_url,
                backlink="",
            ),
        )
        self.assertIn("official source-origin backlink", reason)

    def test_rejects_unsafe_company_bound_sibling_backlinks(self):
        regional_homepage = "https://www.acme.com.sg"
        unsafe_siblings = (
            "http://www.acme.com/about",
            "https://user:secret@www.acme.com/about",
            "https://www.acme.com:8443/about",
            "https://www.acme.com/blog",
            "https://news.acme.com/about",
            "https://www.acme.com/about?continue=https%3A%2F%2Fevil.example",
        )
        for backlink in unsafe_siblings:
            with self.subTest(backlink=backlink):
                reason = self.assert_redirect_rejected(
                    request_url=f"{regional_homepage}/careers",
                    homepage_url=regional_homepage,
                    html=redirect_html(backlink=backlink),
                )
                self.assertIn("official source-origin backlink", reason)

    def test_data_attribute_does_not_count_as_official_backlink(self):
        html = redirect_html(backlink="").replace(
            "</body>",
            f'<div data-url="{OFFICIAL_URL}"></div></body>',
        )
        reason = self.assert_redirect_rejected(html=html)
        self.assertIn("official source-origin backlink", reason)

    def test_ordinary_cross_site_career_copy_still_fails(self):
        html = "<html><title>Acme Careers</title><body>Search jobs and open positions</body></html>"
        reason = self.assert_redirect_rejected(html=html)
        self.assertIn("canonical or og:url identity", reason)


if __name__ == "__main__":
    unittest.main()
