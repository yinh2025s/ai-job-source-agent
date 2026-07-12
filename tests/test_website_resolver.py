import unittest
import time

from job_source_agent.web import FetchError, Fetcher, Page
from job_source_agent.website_resolver import CompanyWebsiteResolver, clean_search_url, is_blocked_domain


class WebsiteResolverTests(unittest.TestCase):
    def test_linkedin_static_asset_domains_are_blocked(self):
        self.assertTrue(is_blocked_domain("https://media.licdn.com"))
        self.assertTrue(is_blocked_domain("https://static.licdn.com"))
        self.assertTrue(is_blocked_domain("https://dms.licdn.com"))
        self.assertTrue(is_blocked_domain("https://challenges.cloudflare.com"))

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

    def test_linkedin_slug_candidates_strip_common_suffixes(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        candidates = resolver._linkedin_slug_domain_candidates("https://www.linkedin.com/company/brexhq")

        self.assertIn("https://brex.com", candidates)

    def test_fast_verified_domain_is_selected_before_search(self):
        class FastDomainFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if "linkedin.com" in url or "bing.com" in url or "duckduckgo.com" in url:
                    raise AssertionError(f"slow resolver path should not run: {url}")
                if url.rstrip("/") == "https://lyft.com":
                    return Page(url=url, final_url="https://www.lyft.com/", html="<html><head><title>Lyft</title></head></html>")
                raise FetchError("not this candidate")

        fetcher = FastDomainFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        website_url, trace = resolver.resolve("Lyft", "https://www.linkedin.com/company/lyft")

        self.assertEqual(website_url, "https://www.lyft.com/")
        self.assertIn("fast verified domain", trace["selected"]["reasons"])
        self.assertFalse(any("bing.com" in call for call in fetcher.calls))

    def test_verified_candidate_uses_company_canonical_domain(self):
        class CanonicalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        '<html><head><link rel="canonical" href="https://www.matrixspace.com/"></head>'
                        '<body>MatrixSpace careers</body></html>'
                    ),
                )

        resolver = CompanyWebsiteResolver(CanonicalFetcher(offline=True))

        candidate = resolver._score_candidate("https://matrixspace.ai", "MatrixSpace", verify=True)

        self.assertEqual(candidate.url, "https://www.matrixspace.com/")
        self.assertIn("homepage canonical URL", candidate.reasons)
        self.assertIn("homepage verified", candidate.reasons)

    def test_resolver_verifies_the_highest_scoring_candidate_first(self):
        class RankedCanonicalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url or "bing.com" in url:
                    raise FetchError("no external candidates")
                if url == "https://matrixspace.ai":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<html><head><link rel="canonical" href="https://www.matrixspace.com/"></head>'
                            '<body>MatrixSpace careers</body></html>'
                        ),
                    )
                raise FetchError(f"unexpected URL: {url}")

        resolver = CompanyWebsiteResolver(RankedCanonicalFetcher(offline=True), verify_limit=1)

        website_url, trace = resolver.resolve(
            "MatrixSpace",
            "https://www.linkedin.com/company/matrixspaceai",
        )

        self.assertEqual(website_url, "https://www.matrixspace.com/")
        self.assertIn("homepage canonical URL", trace["selected"]["reasons"])

    def test_unverified_domain_guess_is_not_selected_after_verified_candidate_fails(self):
        class FailedVerificationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html='<a href="https://cricut.com">Official</a><a href="https://cricut.ai">Other</a>',
                    )
                if "bing.com" in url:
                    return Page(url=url, final_url=url, html="<html></html>")
                raise FetchError("verification timed out")

        resolver = CompanyWebsiteResolver(FailedVerificationFetcher(offline=True), verify_limit=1)

        website_url, trace = resolver.resolve(
            "Cricut",
            "https://www.linkedin.com/company/cricut",
        )

        self.assertIsNone(website_url)
        self.assertNotIn("selected", trace)

    def test_duckduckgo_search_is_used_when_bing_has_no_results(self):
        class SearchFallbackFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "bing.com" in url:
                    return Page(url=url, final_url=url, html="<html></html>")
                if "duckduckgo.com" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<a class="result__a" '
                            'href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmodmed.com%2Fabout">ModMed</a>'
                        ),
                    )
                if url == "https://modmed.com":
                    return Page(url=url, final_url=url, html="<html><body>ModMed healthcare</body></html>")
                raise FetchError(f"unexpected URL: {url}")

        resolver = CompanyWebsiteResolver(SearchFallbackFetcher(offline=True), verify_limit=1)

        website_url, trace = resolver.resolve("ModMed")

        self.assertEqual(website_url, "https://modmed.com")
        self.assertIn("homepage verified", trace["selected"]["reasons"])

    def test_bing_rss_returns_direct_official_candidate(self):
        class BingRssFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel><item><title>ONEOK, Inc.</title>"
                            "<link>https://www.oneok.com/</link></item></channel></rss>"
                        ),
                    )
                if url.rstrip("/") == "https://www.oneok.com":
                    return Page(url=url, final_url=url, html="<html><body>ONEOK energy</body></html>")
                raise FetchError(f"unexpected URL: {url}")

        resolver = CompanyWebsiteResolver(BingRssFetcher(offline=True), verify_limit=1)

        website_url, _trace = resolver.resolve("ONEOK")

        self.assertEqual(website_url, "https://www.oneok.com")

    def test_absolute_bing_redirect_decodes_base64_target(self):
        redirect = (
            "https://www.bing.com/ck/a?u="
            "a1aHR0cHM6Ly93d3cub25lb2suY29tLw=="
        )

        self.assertEqual(clean_search_url(redirect), "https://www.oneok.com")

    def test_top_website_candidates_are_verified_concurrently(self):
        class SlowFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                time.sleep(0.2)
                return Page(url=url, final_url=url, html="<html><body>Acme</body></html>")

        resolver = CompanyWebsiteResolver(SlowFetcher(offline=True), verify_limit=3)
        started = time.monotonic()

        candidates = resolver._rank_and_verify_candidates(
            ["https://acme.com", "https://acme.ai", "https://acme.io"],
            "Acme",
            None,
        )

        self.assertLess(time.monotonic() - started, 0.45)
        self.assertTrue(all("homepage verified" in candidate.reasons for candidate in candidates))

    def test_short_company_name_does_not_match_inside_unrelated_text(self):
        class UnrelatedFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html="<html><head><title>Someone Else</title></head><body>Someone careers</body></html>",
                )

        resolver = CompanyWebsiteResolver(UnrelatedFetcher(offline=True))

        candidate = resolver._score_candidate("https://someone.com", "One", verify=True)

        self.assertIn("ambiguous company name", candidate.reasons)
        self.assertIn("company token missing from homepage", candidate.reasons)
        self.assertNotIn("homepage title confirms company identity", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_search_snippet_can_confirm_an_ambiguous_company_name(self):
        class SearchEvidenceFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel><item><title>Software company directory</title>"
                            "<description>Official website for Ada</description>"
                            "<link>https://ada.com/</link></item></channel></rss>"
                        ),
                    )
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if url.rstrip("/") == "https://ada.com":
                    return Page(url=url, final_url=url, html="<html><body>Build better software</body></html>")
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(SearchEvidenceFetcher(offline=True), verify_limit=2)

        website_url, trace = resolver.resolve("Ada")

        self.assertEqual(website_url, "https://ada.com")
        self.assertIn("search result confirms company identity", trace["selected"]["reasons"])

    def test_linkedin_slug_confirms_exact_short_name_domain(self):
        class GenericHomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(url=url, final_url=url, html="<html><body>Secure cloud content</body></html>")

        resolver = CompanyWebsiteResolver(GenericHomepageFetcher(offline=True))
        linkedin_url = "https://www.linkedin.com/company/box"

        exact = resolver._score_candidate(
            "https://box.com",
            "Box",
            linkedin_company_url=linkedin_url,
            verify=True,
        )
        unrelated = resolver._score_candidate(
            "https://boxoffice.com",
            "Box",
            linkedin_company_url=linkedin_url,
            verify=True,
        )

        self.assertIn("LinkedIn slug confirms domain", exact.reasons)
        self.assertNotIn("LinkedIn slug confirms domain", unrelated.reasons)
        self.assertEqual(resolver._select_verified_candidate([exact, unrelated]), exact)

    def test_canonical_domain_confirms_short_company_identity(self):
        class CanonicalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html='<html><head><link rel="canonical" href="https://ada.com/"></head></html>',
                )

        resolver = CompanyWebsiteResolver(CanonicalFetcher(offline=True))

        candidate = resolver._score_candidate("https://ada.ai", "Ada", verify=True)

        self.assertEqual(candidate.url, "https://ada.com/")
        self.assertIn("homepage canonical confirms company identity", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)


if __name__ == "__main__":
    unittest.main()
