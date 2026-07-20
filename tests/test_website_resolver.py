import unittest
import time

from job_source_agent.request_identity import build_request_identity
from job_source_agent.web import FetchError, Fetcher, Page, domain_of
from job_source_agent.website_resolver import (
    CompanyWebsiteResolver,
    SearchEvidence,
    WebsiteCandidate,
    _alternate_apex_www_candidate,
    _corporate_group_root_candidates,
    _linkedin_json_ld_websites,
    _regional_root_candidates,
    _regional_sibling_root_candidates,
    clean_search_url,
    is_blocked_domain,
    tokenize_company_name,
    url_region,
)


class WebsiteResolverTests(unittest.TestCase):
    def test_parenthetical_b_corp_certification_is_not_brand_identity(self):
        class CertificationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == "https://group.loccitane.com":
                    return page
                raise FetchError("unexpected candidate")

        page = Page(
            url="https://group.loccitane.com",
            html=(
                "<html><head><title>Group L'OCCITANE</title></head>"
                "<body><h1>L'OCCITANE Group</h1><a href='/careers'>Careers</a></body></html>"
            ),
            final_url="https://group.loccitane.com",
        )
        resolver = CompanyWebsiteResolver(
            CertificationFetcher(offline=True),
            verify_limit=1,
        )

        website, trace = resolver.resolve(
            "L'OCCITANE Group (B Corp)",
            "https://www.linkedin.com/company/l-occitane-group",
            stored_candidate_url="https://group.loccitane.com",
        )

        self.assertEqual(website, "https://group.loccitane.com")
        self.assertIn("stored company evidence revalidated", trace["selected"]["reasons"])

    def test_stored_company_candidate_is_revalidated_before_blocked_linkedin(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class StoredCandidateFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if domain_of(url) == "acme.example":
                    return Page(
                        url=url,
                        final_url="https://acme.example/",
                        html="<title>Acme</title><body>Acme official company</body>",
                    )
                if "linkedin.com" in url:
                    raise AssertionError("stored revalidation must not fetch LinkedIn first")
                raise FetchError("not this candidate")

        fetcher = StoredCandidateFetcher()
        website_url, trace = CompanyWebsiteResolver(fetcher).resolve(
            "Acme",
            linkedin_url,
            stored_candidate_url="https://acme.example",
        )

        self.assertEqual(website_url, "https://acme.example/")
        self.assertEqual(fetcher.calls, ["https://acme.example"])
        self.assertIn(
            "stored company evidence revalidated",
            trace["selected"]["reasons"],
        )

    def test_full_linkedin_slug_can_replace_ambiguous_stored_company_site(self):
        linkedin_url = "https://www.linkedin.com/company/join-blossom-health"
        official_url = "https://www.joinblossomhealth.com/"

        class StoredBlossomFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if domain_of(url) == "blossom.net":
                    return Page(
                        url=url,
                        final_url="https://www.blossom.net/",
                        html="<title>Blossom</title><body>Blossom products</body>",
                    )
                if domain_of(url) == "joinblossomhealth.com":
                    return Page(
                        url=url,
                        final_url=official_url,
                        html=(
                            "<title>Blossom Health | Online Psychiatry</title>"
                            "<body>Blossom Health psychiatrists and careers</body>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = StoredBlossomFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=2,
        ).resolve(
            "Blossom",
            linkedin_url,
            "United States",
            stored_candidate_url="https://www.blossom.net",
        )

        self.assertEqual(website_url, official_url)
        self.assertTrue(
            any(domain_of(call) == "joinblossomhealth.com" for call in fetcher.calls)
        )
        self.assertIn(
            "LinkedIn slug challenge replaced stored company evidence",
            trace["selected"]["reasons"],
        )

    def test_transport_fallback_only_removes_www_from_verified_candidate(self):
        self.assertEqual(
            _alternate_apex_www_candidate("https://www.example.com/about"),
            "https://example.com/about",
        )
        self.assertIsNone(
            _alternate_apex_www_candidate("https://careers.example.com/about")
        )
        self.assertIsNone(_alternate_apex_www_candidate("https://example.com/about"))

    def test_url_region_accepts_only_leading_language_region_locales(self):
        self.assertEqual(url_region("https://example.com/en-be/jobs"), "be")
        self.assertEqual(url_region("https://example.com/es-es/jobs"), "es")
        self.assertEqual(url_region("https://example.cn/jobs"), "cn")
        self.assertIsNone(url_region("https://example.com/jobs/en-be/openings"))
        self.assertIsNone(url_region("https://example.com/careers/us/engineering"))

    def test_us_job_rejects_conflicting_cctld_and_uses_global_company_site(self):
        class RegionalDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if domain_of(url) == "maison.com":
                    return Page(
                        url=url,
                        final_url="https://www.maison.com/",
                        html="<title>Maison</title><body>Maison official website</body>",
                    )
                if domain_of(url) == "maison.cn":
                    return Page(
                        url=url,
                        final_url="https://www.maison.cn/",
                        html="<title>Maison China</title><body>Maison official website</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            RegionalDomainFetcher(offline=True), verify_limit=3
        ).resolve(
            "Maison",
            "https://www.linkedin.com/company/maison",
            "New York, NY",
            "https://maison.cn",
        )

        self.assertEqual(website_url, "https://www.maison.com/")
        china_candidate = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "maison.cn"
        )
        self.assertIn(
            "regional website conflicts with job location: cn vs us",
            china_candidate["reasons"],
        )

    def test_china_job_can_select_matching_cctld(self):
        class ChinaDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "maison.cn":
                    return Page(
                        url=url,
                        final_url="https://maison.cn/",
                        html="<title>Maison China</title><body>Maison official website</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            ChinaDomainFetcher(offline=True), verify_limit=1
        ).resolve("Maison", job_location="Shanghai, China", preferred_url="https://maison.cn")

        self.assertEqual(website_url, "https://maison.cn/")
        self.assertIn(
            "regional website matches job location: cn",
            trace["selected"]["reasons"],
        )

    def test_official_conflicting_cctld_does_not_publish_regional_inventory(self):
        linkedin_url = "https://www.linkedin.com/company/maison"

        class OfficialRegionalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Maison",'
                            '"sameAs":"https://maison.cn"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "maison.cn":
                    return Page(
                        url=url,
                        final_url="https://maison.cn/",
                        html=(
                            "<title>Maison China</title><body>Maison"
                            '<a href="/careers">China careers</a></body>'
                        ),
                    )
                raise FetchError("not this candidate")

        website_url, trace, navigation_evidence = CompanyWebsiteResolver(
            OfficialRegionalFetcher(offline=True), verify_limit=3
        ).resolve_with_navigation_evidence(
            "Maison",
            linkedin_url,
            "New York, NY",
        )

        self.assertEqual(website_url, "https://maison.cn/")
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )
        self.assertIsNone(navigation_evidence)

    def test_navigation_evidence_comes_only_from_the_selected_verified_homepage(self):
        class CandidateFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://www.acme.com/",
                        html=(
                            "<html><head><title>Acme</title></head><body>Acme"
                            '<a href="/careers">Internal recruiting notes</a>'
                            "</body></html>"
                        ),
                    )
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html=(
                            "<html><head><title>Acme</title></head><body>Acme"
                            '<a href="/careers">Unselected careers</a>'
                            "</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = CandidateFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        website_url, _trace, navigation_evidence = (
            resolver.resolve_with_navigation_evidence("Acme")
        )

        self.assertEqual(website_url, "https://www.acme.com/")
        self.assertIsNotNone(navigation_evidence)
        assert navigation_evidence is not None
        self.assertEqual(navigation_evidence.homepage_url, website_url)
        self.assertEqual(
            navigation_evidence.candidate_urls,
            ("https://www.acme.com/careers",),
        )
        self.assertNotIn("https://acme.ai/careers", navigation_evidence.candidate_urls)
        self.assertFalse(hasattr(navigation_evidence, "text"))
        self.assertNotIn("https://www.acme.com/", fetcher.calls)

    def test_preferred_parked_domain_falls_back_to_verified_official_site(self):
        class MigratedDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "old-acme.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<html data-adblockkey="abc"><head>'
                            '<title>old-acme.com - Resources and Information</title>'
                            '</head><body><img src="https://img.sedoparking.com/logo.png"></body></html>'
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://www.acme.com/",
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(MigratedDomainFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Acme",
            preferred_url="https://old-acme.com",
        )

        self.assertEqual(website_url, "https://www.acme.com/")
        parked = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "old-acme.com"
        )
        self.assertIn("parked domain rejected", parked["reasons"])

    def test_historical_domain_yields_to_linkedin_json_ld_official_website(self):
        linkedin_url = "https://www.linkedin.com/company/eightpoint"

        class MigratedIdentityFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Eightpoint",'
                            '"sameAs":"https://eightpoint.io/li"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "eightpoint.com":
                    return Page(
                        url=url,
                        final_url="https://eightpoint.com/",
                        html="<title>Eightpoint</title>",
                    )
                if domain_of(url) == "eightpoint.io":
                    return Page(
                        url=url,
                        final_url="https://eightpoint.io/",
                        html="<title>Eightpoint</title>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(MigratedIdentityFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Eightpoint",
            linkedin_url,
            preferred_url="https://eightpoint.com/",
        )

        self.assertEqual(website_url, "https://eightpoint.io/")
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_linkedin_json_ld_ignores_different_organization_identity(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class UnrelatedIdentityFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Other Company",'
                            '"sameAs":"https://other.example"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://acme.com/",
                        html="<title>Acme</title>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(UnrelatedIdentityFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Acme",
            linkedin_url,
            preferred_url="https://acme.com/",
        )

        self.assertEqual(website_url, "https://acme.com/")
        self.assertNotIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_linkedin_official_website_supports_punctuated_brand_domain(self):
        linkedin_url = "https://www.linkedin.com/company/m-r-walls"

        class PunctuatedBrandFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"M|R Walls",'
                            '"sameAs":"http://mrwalls.io"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "mrwalls.com":
                    return Page(
                        url=url,
                        final_url="https://mrwalls.com/",
                        html="<title>M R Walls</title>",
                    )
                if domain_of(url) == "mrwalls.io":
                    return Page(
                        url=url,
                        final_url="https://mrwalls.io/",
                        html="<title>M R Walls</title>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(PunctuatedBrandFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "M|R Walls",
            linkedin_url,
            preferred_url="https://mrwalls.com",
        )

        self.assertEqual(website_url, "https://mrwalls.io/")
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_linkedin_bare_same_as_is_normalized_as_official_website(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Organization","name":"Nevis",'
            '"sameAs":"www.neviswealth.com"}'
            "</script>"
        )

        self.assertEqual(
            _linkedin_json_ld_websites(html, "Nevis"),
            ["https://www.neviswealth.com"],
        )

    def test_thin_linkedin_page_retries_and_official_url_survives_homepage_block(self):
        linkedin_url = "https://www.linkedin.com/company/velox"

        class ThrottledLinkedInFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == linkedin_url:
                    return Page(url=url, html="<html>temporary throttle</html>")
                if url == f"{linkedin_url}/":
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"VELOX",'
                            '"sameAs":"https://www.velox.com"}'
                            "</script>"
                        ),
                    )
                raise FetchError("homepage or search blocked")

        fetcher = ThrottledLinkedInFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        website_url, trace = resolver.resolve(
            "VELOX",
            linkedin_url,
            "Boise, ID",
        )

        self.assertEqual(website_url, "https://www.velox.com")
        self.assertIn(f"{linkedin_url}/", fetcher.calls)
        self.assertIn(
            "LinkedIn official website accepted without homepage response",
            trace["selected"]["reasons"],
        )

    def test_linkedin_fetch_error_retries_trailing_slash_variant(self):
        linkedin_url = "https://www.linkedin.com/company/velox"

        class TransientLinkedInFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == linkedin_url:
                    raise FetchError("transient public-page failure")
                if url == f"{linkedin_url}/":
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"VELOX",'
                            '"sameAs":"https://www.velox.com"}'
                            "</script>"
                        ),
                    )
                raise FetchError("homepage or search blocked")

        fetcher = TransientLinkedInFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve("VELOX", linkedin_url, "Boise, ID")

        self.assertEqual(website_url, "https://www.velox.com")
        linkedin_calls = [url for url in fetcher.calls if "linkedin.com" in url]
        self.assertEqual(linkedin_calls, [linkedin_url, f"{linkedin_url}/"])
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_linkedin_official_evidence_reuses_failed_fast_verification_before_search(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class OfficialEvidenceFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.homepage_calls = []

            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":"https://www.acme.com"}'
                            "</script>"
                        ),
                    )
                if "bing.com" in url or "duckduckgo.com" in url:
                    raise AssertionError("official evidence should resolve before search")
                self.homepage_calls.append(url)
                raise FetchError("homepage unavailable")

        fetcher = OfficialEvidenceFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve("Acme", linkedin_url, "Austin, TX")

        self.assertEqual(website_url, "https://www.acme.com")
        self.assertEqual(
            sum(domain_of(url) == "acme.com" for url in fetcher.homepage_calls),
            1,
        )
        self.assertEqual(fetcher.homepage_calls.count("https://acme.com"), 1)
        self.assertIn(
            "LinkedIn official website accepted without homepage response",
            trace["selected"]["reasons"],
        )

    def test_cached_official_website_beats_stale_preferred_domain_during_throttle(self):
        linkedin_url = "https://www.linkedin.com/company/m-r-walls"

        class EvidenceStore:
            def load(self, company_name, linkedin_company_url):
                self.loaded = (company_name, linkedin_company_url)
                return ("https://mrwalls.io",)

            def save(self, company_name, linkedin_company_url, official_website_urls):
                raise AssertionError("throttled page must not overwrite cached evidence")

        class ThrottledMigrationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("public company page throttled")
                if domain_of(url) == "mrwalls.io":
                    return Page(
                        url=url,
                        final_url="https://mrwalls.io/",
                        html=(
                            '<html><head><title>M|R Walls</title>'
                            '<link rel="canonical" href="https://mrwalls.io/"></head>'
                            "<body>M|R Walls</body></html>"
                        ),
                    )
                if domain_of(url) == "mrwalls.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>M R Walls</title></head><body>M R Walls</body></html>",
                    )
                raise FetchError("not this candidate")

        store = EvidenceStore()
        website_url, trace = CompanyWebsiteResolver(
            ThrottledMigrationFetcher(offline=True),
            verify_limit=3,
            linkedin_evidence_store=store,
        ).resolve(
            "M|R Walls",
            linkedin_url,
            "Santa Monica, CA",
            preferred_url="https://mrwalls.com",
        )

        self.assertEqual(website_url, "https://mrwalls.io/")
        self.assertEqual(trace["linkedin_official_evidence_source"], "cache")
        self.assertIn(
            "candidate source: linkedin_cached_official_website",
            trace["selected"]["reasons"],
        )
        self.assertEqual(store.loaded, ("M|R Walls", linkedin_url))

    def test_identity_separator_loads_official_website_before_single_fast_domain_wins(self):
        linkedin_url = "https://www.linkedin.com/company/m-r-walls"

        class SeparatorAmbiguityFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"M|R Walls",'
                            '"sameAs":"https://mrwalls.io"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "mrwalls.com":
                    return Page(
                        url=url,
                        final_url="https://mrwalls.com/",
                        html="<html><title>M R Walls</title><body>M R Walls</body></html>",
                    )
                if domain_of(url) == "mrwalls.io":
                    return Page(
                        url=url,
                        final_url="https://mrwalls.io/",
                        html="<html><title>M|R Walls</title><body>M|R Walls</body></html>",
                    )
                raise FetchError("not this candidate")

        fetcher = SeparatorAmbiguityFetcher()
        website_url, trace = CompanyWebsiteResolver(fetcher, verify_limit=1).resolve(
            "M|R Walls",
            linkedin_url,
            "Santa Monica, CA",
        )

        self.assertEqual(website_url, "https://mrwalls.io/")
        self.assertTrue(any("linkedin.com" in call for call in fetcher.calls))
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_plain_company_name_keeps_fast_path_without_linkedin_fetch(self):
        linkedin_url = "https://www.linkedin.com/company/ordinary-systems"

        class PlainNameFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if "linkedin.com" in url:
                    raise AssertionError("plain company should keep the fast path")
                if domain_of(url) == "ordinarysystems.com":
                    return Page(
                        url=url,
                        final_url="https://ordinarysystems.com/",
                        html="<html><title>Ordinary Systems</title></html>",
                    )
                raise FetchError("not this candidate")

        fetcher = PlainNameFetcher()
        website_url, _trace = CompanyWebsiteResolver(fetcher, verify_limit=3).resolve(
            "Ordinary Systems",
            linkedin_url,
        )

        self.assertEqual(website_url, "https://ordinarysystems.com/")
        self.assertFalse(any("linkedin.com" in call for call in fetcher.calls))

    def test_live_linkedin_official_website_is_saved_for_future_runs(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class RecordingStore:
            def __init__(self):
                self.saved = None

            def load(self, company_name, linkedin_company_url):
                return ()

            def save(self, company_name, linkedin_company_url, official_website_urls):
                self.saved = (company_name, linkedin_company_url, official_website_urls)

        class OfficialPageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":"https://acme.example"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.example":
                    return Page(url=url, html="<html><title>Acme</title><body>Acme</body></html>")
                raise FetchError("not this candidate")

        store = RecordingStore()
        website_url, trace = CompanyWebsiteResolver(
            OfficialPageFetcher(offline=True),
            linkedin_evidence_store=store,
        ).resolve("Acme", linkedin_url, preferred_url="https://old-acme.example")

        self.assertEqual(website_url, "https://acme.example")
        self.assertEqual(trace["linkedin_official_evidence_source"], "live")
        self.assertEqual(store.saved, ("Acme", linkedin_url, ("https://acme.example",)))

    def test_exact_disambiguating_slug_selects_verified_extended_brand(self):
        class ExtendedBrandFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "neviswealth.com":
                    return Page(
                        url=url,
                        final_url="https://www.neviswealth.com/",
                        html="<html><body>Nevis wealth management</body></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(ExtendedBrandFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Nevis",
            "https://www.linkedin.com/company/neviswealth",
            "New York, NY",
        )

        self.assertEqual(website_url, "https://www.neviswealth.com/")
        self.assertIn(
            "LinkedIn slug exactly matches domain",
            trace["selected"]["reasons"],
        )

    def test_try_slug_generates_candidate_without_confirming_arbitrary_tld(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        candidates = resolver._linkedin_slug_domain_candidates(
            "https://www.linkedin.com/company/trymirage"
        )
        scored = resolver._score_candidate(
            "https://mirage.app",
            "Mirage",
            linkedin_company_url="https://www.linkedin.com/company/trymirage",
            verify=False,
        )

        self.assertIn("https://mirage.app", candidates)
        self.assertIn("LinkedIn slug confirms domain", scored.reasons)
        self.assertIn("LinkedIn marketing-prefix slug is TLD-ambiguous", scored.reasons)

    def test_find_slug_does_not_allow_speculative_bare_brand_domain_to_win(self):
        linkedin_url = "https://www.linkedin.com/company/find-mirage"

        class AmbiguousBrandFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "mirage.com":
                    return Page(
                        url=url,
                        final_url="https://mirage.com/",
                        html=(
                            '<html><head><title>Enterprise platform</title>'
                            '<link rel="canonical" href="https://mirage.com/"></head>'
                            '<body>Technology for event-driven enterprises</body></html>'
                        ),
                    )
                raise FetchError("identity evidence unavailable")

        resolver = CompanyWebsiteResolver(
            AmbiguousBrandFetcher(offline=True),
            verify_limit=3,
        )

        website_url, trace = resolver.resolve("Mirage", linkedin_url, "United States")

        self.assertIsNone(website_url)
        mirage_candidate = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "mirage.com"
        )
        self.assertIn(
            "LinkedIn marketing-prefix slug is TLD-ambiguous",
            mirage_candidate["reasons"],
        )

    def test_marketing_prefix_slug_loads_official_linkedin_evidence_before_fast_selection(self):
        linkedin_url = "https://www.linkedin.com/company/trymirage"

        class MirageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Mirage",'
                            '"sameAs":"https://mirage.app"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "mirage.ai":
                    return Page(
                        url=url,
                        final_url="https://mirage.ai/",
                        html="<html><head><title>Mirage AI</title></head><body>Mirage</body></html>",
                    )
                if domain_of(url) == "mirage.app":
                    return Page(
                        url=url,
                        final_url="https://mirage.app/",
                        html="<html><head><title>Mirage</title></head><body>Mirage</body></html>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            MirageFetcher(offline=True),
            verify_limit=3,
        ).resolve("Mirage", linkedin_url, "New York, NY")

        self.assertEqual(website_url, "https://mirage.app/")
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_parked_infrastructure_marker_is_not_inferred_from_generic_copy(self):
        class LegitimateFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        "<html><head><title>Acme Resources and Information</title></head>"
                        "<body>Company resources and information for customers.</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(LegitimateFetcher(offline=True))

        candidate = resolver._score_candidate("https://acme.com", "Acme", verify=True)

        self.assertIn("homepage verified", candidate.reasons)
        self.assertNotIn("parked domain rejected", candidate.reasons)

    def test_squarespace_parking_template_is_rejected(self):
        class ParkingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        "<html><head><title>Coming Soon</title>"
                        '<script src="//assets.squarespace.com/universal/scripts-compressed/'
                        'parking-page-example-min.en-US.js"></script></head>'
                        "<body>We're under construction.</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ParkingFetcher(offline=True))

        candidate = resolver._score_candidate(
            "https://acmeconstruction.com", "Acme Construction", verify=True
        )

        self.assertIn("parked domain rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)

    def test_spaceship_domain_sale_template_is_rejected(self):
        class ParkingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        "<html><head><title>general-motors.com for sale | Spaceship.com</title>"
                        '<meta name="description" content="general-motors.com is for sale on '
                        'Spaceship. Secure checkout and quick transfer."></head>'
                        "<body>Own general-motors.com today.</body></html>"
                    ),
                )

        candidate = CompanyWebsiteResolver(
            ParkingFetcher(offline=True)
        )._score_candidate(
            "https://general-motors.com",
            "General Motors",
            verify=True,
        )

        self.assertIn("parked domain rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)

    def test_us_job_location_recovers_same_host_us_root_after_foreign_redirect(self):
        us_root = "https://www.deloitte.com/us/en.html"

        class RegionalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "format=rss" in url:
                    raise AssertionError("same-host regional recovery should run before search")
                if url.rstrip("/") in {"https://deloitte.com", "https://www.deloitte.com"}:
                    return Page(
                        url=url,
                        final_url="https://www.deloitte.com/southeast-asia/en.html",
                        html=(
                            "<html><head><title>Deloitte</title></head><body>Deloitte"
                            '<a href="https://www.deloitte.com/us/en.html">United States</a>'
                            "</body></html>"
                        ),
                    )
                if url == us_root:
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>Deloitte US</title></head><body>Deloitte careers</body></html>",
                    )
                raise FetchError(f"not this candidate: {url}")

        resolver = CompanyWebsiteResolver(RegionalFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Deloitte",
            "https://www.linkedin.com/company/deloitte",
            "Grand Rapids, MI",
        )

        self.assertEqual(website_url, us_root)
        self.assertEqual(trace["target_region"], "us")
        selected_reasons = trace["selected"]["reasons"]
        self.assertIn("regional website matches job location: us", selected_reasons)
        self.assertIn("verified regional root recovery", selected_reasons)
        self.assertTrue(
            any(
                "regional website conflicts with job location: sea vs us" in item["reasons"]
                for item in trace["candidates"]
            )
        )

    def test_us_job_location_recovers_modern_en_us_storefront(self):
        us_root = "https://skims.com/en-us"

        class RegionalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if url.rstrip("/") in {"https://skims.com", "https://www.skims.com"}:
                    return Page(
                        url=url,
                        final_url="https://skims.com/en-sg",
                        html=(
                            "<title>SKIMS</title><body>SKIMS"
                            '<a href="https://skims.com/en-us">United States</a>'
                            "</body>"
                        ),
                    )
                if url.rstrip("/") in {us_root, "https://www.skims.com/en-us"}:
                    return Page(
                        url=url,
                        final_url=us_root,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"SKIMS"}</script>'
                            "<title>SKIMS US</title><body>SKIMS "
                            '<a href="https://skims.pinpointhq.com/">Careers</a>'
                            "</body>"
                        ),
                    )
                raise FetchError(f"not this candidate: {url}")

        resolver = CompanyWebsiteResolver(RegionalFetcher(offline=True), verify_limit=3)

        website_url, _trace = resolver.resolve(
            "SKIMS",
            "https://www.linkedin.com/company/skimsbody",
            "Los Angeles, CA",
            "https://skims.com",
        )

        self.assertEqual(website_url, us_root)

    def test_us_location_follows_declared_same_site_gateway_locale(self):
        gateway_url = "https://caudalie.com/en-fr"
        us_url = "https://us.caudalie.com/"

        class CaudalieGatewayFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") in {"https://caudalie.com", "https://www.caudalie.com"}:
                    return Page(
                        url=url,
                        final_url=gateway_url,
                        html=(
                            "<title>Caudalie France</title><body>Caudalie"
                            f'<a href="{us_url}">United States</a>'
                            "</body>"
                        ),
                    )
                if url == us_url:
                    return Page(
                        url=url,
                        final_url=us_url,
                        html="<title>Caudalie United States</title><body>Caudalie</body>",
                    )
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                raise FetchError(f"not this candidate: {url}")

        fetcher = CaudalieGatewayFetcher()
        website_url, trace = CompanyWebsiteResolver(fetcher, verify_limit=3).resolve(
            "Caudalie",
            "https://www.linkedin.com/company/caudalie",
            "New York, NY",
        )

        self.assertEqual(website_url, us_url)
        self.assertIn(us_url, fetcher.calls)
        gateway = next(
            candidate
            for candidate in trace["candidates"]
            if candidate["url"] == gateway_url
        )
        self.assertIn("regional gateway declares US locale link", gateway["reasons"])

    def test_us_location_recovers_root_from_hreflang_alternate(self):
        gateway_url = "https://www.lacoste.com/fr/"
        candidate = WebsiteCandidate(
            gateway_url,
            1,
            [
                "homepage verified",
                "homepage title confirms company identity",
                "regional website conflicts with job location: fr vs us",
            ],
            Page(
                url=gateway_url,
                final_url=gateway_url,
                html=(
                    '<LINK HREF="https://www.lacoste.com/" '
                    'HREFLANG="EN-us" REL="ALTERNATE">'
                ),
            ),
        )

        self.assertEqual(
            _regional_root_candidates([candidate], "New York, NY"),
            ["https://www.lacoste.com/"],
        )

    def test_us_location_recovers_us_path_from_hreflang_alternate(self):
        gateway_url = "https://www.skims.com/en-sg"
        candidate = WebsiteCandidate(
            gateway_url,
            1,
            [
                "homepage verified",
                "homepage title confirms company identity",
                "regional website conflicts with job location: sg vs us",
            ],
            Page(
                url=gateway_url,
                final_url=gateway_url,
                html='<link rel="alternate" href="/us/" hreflang="en-US">',
            ),
        )

        self.assertEqual(
            _regional_root_candidates([candidate], "Los Angeles, CA"),
            ["https://www.skims.com/us/"],
        )

    def test_us_location_follows_hreflang_from_verified_deployment_gateway(self):
        gateway_url = "https://prod-deleg-sfcc.lacoste.com/id/en/"
        candidate = WebsiteCandidate(
            gateway_url,
            1,
            [
                "homepage verified",
                "homepage title confirms company identity",
                "deployment hostname",
            ],
            Page(
                url=gateway_url,
                final_url=gateway_url,
                html=(
                    '<link rel="alternate" href="https://www.lacoste.com/us/" '
                    'hreflang="en-US">'
                ),
            ),
        )

        self.assertEqual(
            _regional_root_candidates([candidate], "New York, NY"),
            ["https://www.lacoste.com/us/"],
        )

    def test_declared_us_root_survives_same_site_geo_redirect(self):
        gateway_url = "https://www.skims.com/en-sg"
        us_url = "https://www.skims.com/"

        class GeoRedirectFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == gateway_url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<title>SKIMS</title><body>SKIMS"
                            '<link rel="alternate" href="https://www.skims.com/" '
                            'hreflang="en-US"></body>'
                        ),
                    )
                if url == us_url:
                    return Page(
                        url=url,
                        final_url=gateway_url,
                        html="<title>SKIMS</title><body>SKIMS</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            GeoRedirectFetcher(offline=True), verify_limit=3
        ).resolve(
            "SKIMS",
            "https://www.linkedin.com/company/skimsbody",
            "Los Angeles, CA",
            stored_candidate_url=gateway_url,
        )

        self.assertEqual(website_url, us_url)
        self.assertIn(
            "declared regional root geo-redirected within verified company site",
            trace["selected"]["reasons"],
        )

    def test_declared_us_root_rejects_cross_site_redirect(self):
        gateway_url = "https://www.skims.com/en-sg"
        us_url = "https://www.skims.com/"

        class CrossSiteRedirectFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == gateway_url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<title>SKIMS</title><body>SKIMS"
                            '<link rel="alternate" href="https://www.skims.com/" '
                            'hreflang="en-US"></body>'
                        ),
                    )
                if url == us_url:
                    return Page(
                        url=url,
                        final_url="https://example.net/en-us",
                        html="<title>SKIMS</title><body>SKIMS</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            CrossSiteRedirectFetcher(offline=True), verify_limit=3
        ).resolve(
            "SKIMS",
            "https://www.linkedin.com/company/skimsbody",
            "Los Angeles, CA",
            preferred_url=gateway_url,
        )

        self.assertIsNone(website_url)
        self.assertTrue(
            any(
                "regional locale identity continuity rejected" in item["reasons"]
                for item in trace["candidates"]
            )
        )

    def test_declared_us_root_survives_access_control_on_current_fetch(self):
        gateway_url = "https://prod-deleg-sfcc.lacoste.com/id/en/"
        us_url = "https://www.lacoste.com/"

        class AccessControlledLocaleFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == gateway_url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<title>Lacoste</title><body>Lacoste"
                            '<link rel="alternate" href="https://www.lacoste.com/" '
                            'hreflang="en-US"></body>'
                        ),
                    )
                if url == us_url:
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            AccessControlledLocaleFetcher(offline=True), verify_limit=3
        ).resolve(
            "Lacoste",
            "https://www.linkedin.com/company/lacoste",
            "New York, NY",
            stored_candidate_url=gateway_url,
        )

        self.assertEqual(website_url, us_url)
        self.assertIn(
            "verified regional gateway declares access-controlled locale root",
            trace["selected"]["reasons"],
        )

    def test_same_brand_global_sibling_survives_access_control_for_current_run(self):
        foreign_url = "https://www.michaelkors.cn/"

        class AccessControlledSiblingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "michaelkors.cn":
                    return Page(
                        url=url,
                        final_url=foreign_url,
                        html=(
                            "<title>Michael Kors China</title>"
                            "<body>Michael Kors official store</body>"
                        ),
                    )
                if domain_of(url) == "michaelkors.com":
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError(f"unexpected URL: {url}")

        website_url, trace = CompanyWebsiteResolver(
            AccessControlledSiblingFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "Michael Kors",
            job_location="New York, NY",
            stored_candidate_url=foreign_url,
        )

        self.assertEqual(website_url, "https://michaelkors.com")
        self.assertIn(
            "verified regional gateway supports access-controlled sibling root",
            trace["selected"]["reasons"],
        )

    def test_readable_global_tld_beats_access_controlled_dot_com_sibling(self):
        foreign_url = "https://brand.cn/"

        class GlobalTldFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "brand.cn":
                    return Page(
                        url=url,
                        final_url=foreign_url,
                        html="<title>Brand China</title><body>Brand official</body>",
                    )
                if domain_of(url) == "brand.com":
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                if domain_of(url) == "brand.global":
                    return Page(
                        url=url,
                        final_url="https://brand.global/",
                        html="<title>Brand</title><body>Brand careers</body>",
                    )
                raise FetchError(f"unexpected URL: {url}")

        website_url, trace = CompanyWebsiteResolver(
            GlobalTldFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "Brand",
            job_location="New York, NY",
            stored_candidate_url=foreign_url,
        )

        self.assertEqual(website_url, "https://brand.global/")
        self.assertIn("homepage verified", trace["selected"]["reasons"])

    def test_us_location_rejects_invalid_hreflang_alternates(self):
        gateway_url = "https://www.example.com/fr/"
        candidate = WebsiteCandidate(
            gateway_url,
            1,
            [
                "homepage verified",
                "homepage title confirms company identity",
                "regional website conflicts with job location: fr vs us",
            ],
            Page(
                url=gateway_url,
                final_url=gateway_url,
                html=(
                    '<link rel="alternate" hreflang="en-US" href="https://">'
                    '<link rel="alternate" hreflang="en-US" href="https://[invalid">'
                    '<link rel="alternate" hreflang="en-US" '
                    'href="https://www.unrelated.example/us/">'
                    '<link rel="alternate" hreflang="en-US" href="/fr/">'
                    '<link rel="alternate" hreflang="en-US" hreflang="en-CA" '
                    'href="/us/">'
                ),
            ),
        )

        self.assertEqual(_regional_root_candidates([candidate], "New York, NY"), [])
        self.assertIn(
            "regional gateway contains no eligible US locale link", candidate.reasons
        )

    def test_verified_foreign_cctld_can_verify_same_brand_global_root(self):
        foreign_url = "https://ysl.cn/"
        global_url = "https://ysl.com/"

        class RegionalSiblingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "ysl.cn":
                    return Page(
                        url=url,
                        final_url=foreign_url,
                        html="<title>YSL</title><body>Saint Laurent official</body>",
                    )
                if domain_of(url) == "ysl.com":
                    return Page(
                        url=url,
                        final_url=global_url,
                        html="<title>Saint Laurent</title><body>YSL official</body>",
                    )
                raise FetchError(f"unexpected URL: {url}")

        website_url, trace = CompanyWebsiteResolver(
            RegionalSiblingFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "Saint Laurent",
            job_location="Wayne, NJ",
            stored_candidate_url=foreign_url,
        )

        self.assertEqual(website_url, global_url)
        self.assertIn(
            "candidate source: regional_sibling_recovery",
            trace["selected"]["reasons"],
        )

    def test_marketplace_subdomain_cannot_generate_global_brand_sibling(self):
        candidate = WebsiteCandidate(
            "https://adidas.jd.com/",
            90,
            [
                "homepage verified",
                "homepage title confirms company identity",
                "regional website conflicts with job location: cn vs us",
            ],
            Page(
                url="https://adidas.jd.com/",
                final_url="https://adidas.jd.com/",
                html="<title>adidas</title><body>adidas marketplace</body>",
            ),
        )

        self.assertEqual(
            _regional_sibling_root_candidates(candidate, "adidas", "Portland, OR"),
            [],
        )

    def test_single_token_brand_can_verify_separate_corporate_group_root(self):
        group_url = "https://adidas-group.com/"

        class CorporateGroupFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "adidas.jd.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<title>adidas</title><body>adidas marketplace</body>",
                    )
                if domain_of(url) == "adidas-group.com":
                    return Page(
                        url=url,
                        final_url=group_url,
                        html=(
                            "<title>adidas Group</title>"
                            "<body>adidas company careers and investor relations</body>"
                        ),
                    )
                raise FetchError(f"unavailable: {url}")

        website_url, trace = CompanyWebsiteResolver(
            CorporateGroupFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "adidas",
            job_location="Portland, OR",
            stored_candidate_url="https://adidas.jd.com",
        )

        self.assertEqual(website_url, group_url)
        self.assertIn(
            "verified corporate group root recovery",
            trace["selected"]["reasons"],
        )
        self.assertEqual(_corporate_group_root_candidates("Acme Labs"), [])

    def test_linkedin_identity_prevents_speculative_group_root_rebinding(self):
        class AmbiguousGroupFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "haystack.deepset.ai":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<title>Haystack</title><body>Haystack platform</body>",
                    )
                if domain_of(url) == "haystack-group.com":
                    return Page(
                        url=url,
                        final_url="https://haystack-group.com/de",
                        html="<title>Haystack</title><body>Haystack company careers</body>",
                    )
                raise FetchError(f"unavailable: {url}")

        website_url, trace = CompanyWebsiteResolver(
            AmbiguousGroupFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "Haystack",
            linkedin_company_url="https://www.linkedin.com/company/wearehaystack",
            stored_candidate_url="https://haystack.deepset.ai",
        )

        self.assertNotEqual(website_url, "https://haystack-group.com/de")
        self.assertFalse(
            any(
                "verified corporate group root recovery" in reason
                for reason in (trace.get("selected") or {}).get("reasons", [])
            )
        )

    def test_us_location_gateway_rejects_cross_site_and_conflicting_locale_links(self):
        gateway_url = "https://caudalie.com/en-fr"
        cross_site_url = "https://us.unrelated.example/"
        conflicting_url = "https://fr.caudalie.com/"

        class RejectedGatewayFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") in {"https://caudalie.com", "https://www.caudalie.com"}:
                    return Page(
                        url=url,
                        final_url=gateway_url,
                        html=(
                            "<title>Caudalie France</title><body>Caudalie"
                            f'<a href="{cross_site_url}">United States</a>'
                            f'<a href="{conflicting_url}">United States</a>'
                            "</body>"
                        ),
                    )
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                raise FetchError(f"not this candidate: {url}")

        fetcher = RejectedGatewayFetcher()
        website_url, trace = CompanyWebsiteResolver(fetcher, verify_limit=3).resolve(
            "Caudalie",
            "https://www.linkedin.com/company/caudalie",
            "New York, NY",
        )

        self.assertIsNone(website_url)
        self.assertNotIn(cross_site_url, fetcher.calls)
        self.assertNotIn(conflicting_url, fetcher.calls)
        foreign = next(
            candidate
            for candidate in trace["candidates"]
            if candidate["url"] == gateway_url
        )
        self.assertIn(
            "regional gateway contains no eligible US locale link",
            foreign["reasons"],
        )

    def test_linkedin_static_asset_domains_are_blocked(self):
        self.assertTrue(is_blocked_domain("https://media.licdn.com"))
        self.assertTrue(is_blocked_domain("https://static.licdn.com"))
        self.assertTrue(is_blocked_domain("https://dms.licdn.com"))
        self.assertTrue(is_blocked_domain("https://challenges.cloudflare.com"))
        self.assertTrue(is_blocked_domain("https://modmed.my.site.com"))
        self.assertTrue(is_blocked_domain("https://standardtemplatelabs-com.l.ink"))
        self.assertTrue(is_blocked_domain("https://bit.ly/example"))

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

    def test_linkedin_slug_candidates_strip_product_suffix_for_abbreviated_brand(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        candidates = resolver._linkedin_slug_domain_candidates(
            "https://www.linkedin.com/company/stlabs-ai"
        )

        self.assertIn("https://stlabs.com", candidates)

    def test_verified_multiword_abbreviation_domain_is_selected(self):
        class AbbreviationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "stlabs.com":
                    return Page(
                        url=url,
                        final_url="https://stlabs.com/",
                        html="<html><head><title>STLabs | Intelligent Service Management</title></head></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(AbbreviationFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Standard Template Labs",
            "https://www.linkedin.com/company/stlabs-ai",
            "New York, NY",
        )

        self.assertEqual(website_url, "https://stlabs.com/")
        self.assertIn("company abbreviation in domain", trace["selected"]["reasons"])
        self.assertIn(
            "homepage title confirms company abbreviation",
            trace["selected"]["reasons"],
        )

    def test_exact_linkedin_slug_domain_wins_for_ambiguous_brand(self):
        class FinchFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url or "bing.com" in url or "duckduckgo.com" in url:
                    raise FetchError("external source unavailable")
                if domain_of(url) == "finch.com":
                    return Page(
                        url=url,
                        final_url="https://finch.com/",
                        html=(
                            '<html><head><title>Finch</title>'
                            '<link rel="canonical" href="https://finch.com/"></head>'
                            '<body>Finch</body></html>'
                        ),
                    )
                if domain_of(url) == "finchlegal.com":
                    return Page(
                        url=url,
                        final_url="https://www.finchlegal.com/",
                        html="<html><head><title>Finch | Legal intelligence</title></head></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(FinchFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Finch",
            "https://www.linkedin.com/company/finchlegal",
            "New York, NY",
        )

        self.assertEqual(website_url, "https://www.finchlegal.com/")
        self.assertIn("LinkedIn slug exactly matches domain", trace["selected"]["reasons"])
        wrong_brand = next(
            candidate for candidate in trace["candidates"] if domain_of(candidate["url"]) == "finch.com"
        )
        self.assertNotIn("LinkedIn slug exactly matches domain", wrong_brand["reasons"])

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
        self.assertFalse(any("linkedin.com" in call for call in fetcher.calls))
        self.assertFalse(any("bing.com" in call for call in fetcher.calls))

    def test_generated_slug_domain_waits_for_independent_official_search_evidence(self):
        class IndependentEvidenceFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "bing.com" in url and "format=rss" in url:
                    return Page(
                        url=url,
                        html=(
                            "<rss><channel><item><link>"
                            "https://www.northwell.edu/"
                            "</link></item></channel></rss>"
                        ),
                    )
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                if domain_of(url) == "northwell-health.com":
                    return Page(
                        url=url,
                        final_url="https://northwell-health.com/",
                        html=(
                            '<html><head><title>Northwell Health</title>'
                            '<link rel="canonical" href="https://northwell-health.com/">'
                            '</head><body>Northwell Health</body></html>'
                        ),
                    )
                if domain_of(url) == "northwell.edu":
                    return Page(
                        url=url,
                        final_url="https://www.northwell.edu/",
                        html=(
                            '<html><head><title>Northwell Health</title>'
                            '<link rel="canonical" href="https://www.northwell.edu/">'
                            '</head><body>Northwell Health</body></html>'
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = IndependentEvidenceFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve(
            "Northwell Health",
            "https://www.linkedin.com/company/northwell-health",
        )

        self.assertEqual(website_url, "https://www.northwell.edu/")
        guessed = next(
            item
            for item in trace["candidates"]
            if domain_of(item["url"]) == "northwell-health.com"
        )
        self.assertIn(
            "fast selection deferred for LinkedIn official evidence: "
            "generated domain lacks independent identity evidence",
            guessed["reasons"],
        )
        self.assertIn("candidate source: search_evidence", trace["selected"]["reasons"])

    def test_linkedin_official_evidence_breaks_verified_fast_domain_tie(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class AmbiguousFastDomainsFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":"https://acme.io"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://acme.com/",
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                if domain_of(url) == "acme.io":
                    return Page(
                        url=url,
                        final_url="https://acme.io/",
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            AmbiguousFastDomainsFetcher(offline=True),
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.io/")
        dot_com = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "acme.com"
        )
        dot_io = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "acme.io"
        )
        self.assertGreater(dot_com["score"], dot_io["score"])
        self.assertIn(
            "fast selection deferred for LinkedIn official evidence: "
            "multiple verified same-brand domains",
            dot_com["reasons"],
        )
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_ambiguous_fast_domains_fall_back_when_linkedin_has_no_official_evidence(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class NoOfficialEvidenceFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html="<html><head><title>Acme | LinkedIn</title></head></html>",
                    )
                if domain_of(url) in {"acme.com", "acme.io"}:
                    return Page(
                        url=url,
                        final_url=f"https://{domain_of(url)}/",
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                if "bing.com" in url or "duckduckgo.com" in url:
                    raise AssertionError(f"search fallback should not run: {url}")
                raise FetchError("not this candidate")

        fetcher = NoOfficialEvidenceFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.com/")
        self.assertTrue(any("linkedin.com" in call for call in fetcher.calls))
        self.assertIn("LinkedIn official evidence unavailable", trace["selected"]["reasons"])
        self.assertIn("fast verified domain", trace["selected"]["reasons"])

    def test_same_brand_ai_organization_does_not_beat_verified_dot_com_without_linkedin_evidence(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class CompetingBrandDomainsFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    raise FetchError("HTTP Error 451", status=451)
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html=(
                            '<link rel="canonical" href="https://acme.ai/">'
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme","url":"https://acme.ai"}'
                            "</script><title>Acme | Official Company</title>"
                            "<body>About Acme. Welcome to Acme.</body>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://acme.com/",
                        html="<title>Acme</title><body>Acme company</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            CompetingBrandDomainsFetcher(offline=True),
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.com/")
        self.assertIn(
            "verified exact-brand .com breaks unresolved same-brand TLD tie",
            trace["selected"]["reasons"],
        )

    def test_parked_dot_com_does_not_win_same_brand_tld_tie(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class ParkedDotComFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    raise FetchError("HTTP Error 451", status=451)
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url="https://acme.com/",
                        html="<title>Acme is for sale</title><body>Buy this domain</body>",
                    )
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme","url":"https://acme.ai"}'
                            "</script><title>Acme</title><body>About Acme</body>"
                        ),
                    )
                raise FetchError("not this candidate")

        website_url, _trace = CompanyWebsiteResolver(
            ParkedDotComFetcher(offline=True),
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.ai/")

    def test_retryable_same_brand_dot_com_failure_rejects_unconfirmed_ai(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class BlockedDotComFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    raise FetchError("HTTP Error 451", status=451)
                if domain_of(url) == "acme.com":
                    raise FetchError("timed out", reason_code="NETWORK_TIMEOUT")
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme","url":"https://acme.ai"}'
                            "</script><title>Acme</title><body>About Acme</body>"
                        ),
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            BlockedDotComFetcher(offline=True),
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertIsNone(website_url)
        ai_candidate = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "acme.ai"
        )
        self.assertIn("same-brand .com verification blocked", ai_candidate["reasons"])
        self.assertEqual(
            trace["resolution_failure"]["reason_code"],
            "NETWORK_TIMEOUT",
        )

    def test_linkedin_official_evidence_accepts_ai_when_same_brand_dot_com_is_blocked(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class OfficialAiFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":"https://acme.ai"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    raise FetchError("timed out", reason_code="NETWORK_TIMEOUT")
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html="<title>Acme</title><body>About Acme</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            OfficialAiFetcher(offline=True),
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.ai/")
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

    def test_verified_non_apex_guess_defers_to_linkedin_official_evidence(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class ProductSubdomainFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":"https://acme.com"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    raise FetchError("apex temporarily unavailable")
                if domain_of(url) in {"acme.ai", "acme.io"}:
                    return Page(
                        url=url,
                        final_url="https://developer.acme.com/",
                        html=(
                            "<html><head><title>Acme Developer Platform</title>"
                            '<link rel="canonical" href="https://developer.acme.com/">'
                            "</head>"
                            "<body>Acme developer APIs</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = ProductSubdomainFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, "https://acme.com")
        self.assertTrue(any("linkedin.com" in call for call in fetcher.calls))
        non_apex = [
            item
            for item in trace["candidates"]
            if domain_of(item["url"]) == "developer.acme.com"
        ]
        self.assertTrue(
            any(
                "fast selection deferred for LinkedIn official evidence: "
                "verified non-apex domain" in item["reasons"]
                for item in non_apex
            ),
            non_apex,
        )
        self.assertIn(
            "LinkedIn company page identifies official website",
            trace["selected"]["reasons"],
        )

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

    def test_search_fetch_error_is_retained_when_fallback_resolves_website(self):
        class SearchFallbackFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "format=rss" in url:
                    raise FetchError(
                        "search rate limited",
                        status=429,
                        reason_code="RATE_LIMITED",
                        retryable=True,
                        request_identity=build_request_identity(url).as_dict(),
                    )
                if "bing.com" in url:
                    return Page(url=url, final_url=url, html="<html></html>")
                if "duckduckgo.com" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<a class="result__a" href="https://acme.example/about">'
                            "Acme official website</a>"
                        ),
                    )
                if domain_of(url) == "acme.example":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                raise FetchError(
                    "HTTP Error 404: Not Found",
                    status=404,
                    reason_code="HTTP_NOT_FOUND",
                    retryable=False,
                )

        website_url, trace = CompanyWebsiteResolver(
            SearchFallbackFetcher(offline=True),
            verify_limit=1,
        ).resolve("Acme")

        self.assertEqual(website_url, "https://acme.example")
        search_failure = next(
            failure
            for failure in trace["fetch_errors"]
            if failure["phase"] == "search" and failure["status"] == 429
        )
        self.assertEqual(search_failure["reason_code"], "RATE_LIMITED")
        self.assertTrue(search_failure["retryable"])
        self.assertEqual(search_failure["evidence_tier"], 2)
        self.assertIsNotNone(search_failure["request_identity"])
        self.assertNotIn("resolution_failure", trace)

    def test_linkedin_fetch_error_is_retained_when_trailing_slash_fallback_succeeds(self):
        linkedin_url = "https://www.linkedin.com/company/acme"

        class LinkedInFallbackFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == linkedin_url:
                    raise FetchError(
                        "LinkedIn temporarily unavailable",
                        reason_code="SERVER_ERROR",
                        retryable=True,
                        status=503,
                        request_identity=build_request_identity(url).as_dict(),
                    )
                if url == f"{linkedin_url}/":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"url":"https://acme.com"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                raise FetchError("HTTP Error 404: Not Found", status=404)

        website_url, trace = CompanyWebsiteResolver(
            LinkedInFallbackFetcher(offline=True),
            verify_limit=1,
        ).resolve("Acme", linkedin_url, preferred_url="https://acme.com")

        self.assertEqual(website_url, "https://acme.com")
        linkedin_failure = next(
            failure
            for failure in trace["fetch_errors"]
            if failure["phase"] == "linkedin_company"
        )
        self.assertEqual(linkedin_failure["url"], linkedin_url)
        self.assertEqual(linkedin_failure["reason_code"], "SERVER_ERROR")
        self.assertEqual(linkedin_failure["evidence_tier"], 1)
        self.assertIsNotNone(linkedin_failure["request_identity"])

    def test_retryable_direct_evidence_blocks_ordinary_not_found_resolution(self):
        preferred_url = "https://acme.example"
        identity = build_request_identity(preferred_url).as_dict()

        class BlockedVerificationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == preferred_url:
                    raise FetchError(
                        "homepage timed out",
                        reason_code="NETWORK_TIMEOUT",
                        retryable=True,
                        request_identity=identity,
                    )
                raise FetchError(
                    "HTTP Error 404: Not Found",
                    status=404,
                    reason_code="HTTP_NOT_FOUND",
                    retryable=False,
                    request_identity=build_request_identity(url).as_dict(),
                )

        website_url, trace = CompanyWebsiteResolver(
            BlockedVerificationFetcher(offline=True),
            verify_limit=1,
        ).resolve("Acme", preferred_url=preferred_url)

        self.assertIsNone(website_url)
        self.assertNotIn("selected", trace)
        failure = trace["resolution_failure"]
        self.assertEqual(failure["kind"], "verification_blocked")
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(failure["retryable"])
        self.assertEqual(failure["evidence_tier"], 1)
        self.assertEqual(failure["request_identity"], identity)

    def test_forbidden_direct_evidence_is_retained_after_fallbacks_are_exhausted(self):
        preferred_url = "https://acme.example"

        class ForbiddenVerificationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == preferred_url:
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError(
                    "HTTP Error 404: Not Found",
                    status=404,
                    reason_code="HTTP_NOT_FOUND",
                    retryable=False,
                )

        website_url, trace = CompanyWebsiteResolver(
            ForbiddenVerificationFetcher(offline=True),
            verify_limit=1,
        ).resolve("Acme", preferred_url=preferred_url)

        self.assertIsNone(website_url)
        failure = trace["resolution_failure"]
        self.assertEqual(failure["reason_code"], "HTTP_FORBIDDEN")
        self.assertEqual(failure["status"], 403)
        self.assertFalse(failure["retryable"])

    def test_forbidden_speculative_candidate_does_not_block_official_fallback(self):
        linkedin_url = "https://www.linkedin.com/company/acme"
        official_url = "https://official.example"

        class OfficialFallbackFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if domain_of(url) == "acme.com":
                    raise FetchError(
                        "HTTP Error 403: Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                if url in {linkedin_url, f"{linkedin_url}/"}:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Acme",'
                            '"sameAs":["https://official.example"]}'
                            "</script>"
                        ),
                    )
                if url == official_url:
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                raise FetchError("HTTP Error 404: Not Found", status=404)

        fetcher = OfficialFallbackFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=1,
        ).resolve("Acme", linkedin_url)

        self.assertEqual(website_url, official_url)
        self.assertIn(official_url, fetcher.calls)
        self.assertNotIn("resolution_failure", trace)

    def test_forbidden_www_candidate_does_not_probe_apex_as_transport_fallback(self):
        preferred_url = "https://www.acme.example"

        class WafBlockedFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == preferred_url:
                    raise FetchError(
                        "challenge denied",
                        status=403,
                        reason_code="BOT_PROTECTION",
                        retryable=False,
                    )
                raise FetchError("HTTP Error 404: Not Found", status=404)

        fetcher = WafBlockedFetcher()
        CompanyWebsiteResolver(fetcher, verify_limit=1).resolve(
            "Acme",
            preferred_url=preferred_url,
        )

        self.assertNotIn("https://acme.example", fetcher.calls)

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

    def test_top_website_candidates_are_verified_deterministically(self):
        class SlowFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                time.sleep(0.2)
                return Page(url=url, final_url=url, html="<html><body>Acme</body></html>")

        fetcher = SlowFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        candidates = resolver._rank_and_verify_candidates(
            ["https://acme.com", "https://acme.ai", "https://acme.io"],
            "Acme",
            None,
        )

        self.assertEqual(
            fetcher.calls,
            ["https://acme.com", "https://acme.ai", "https://acme.io"],
        )
        self.assertTrue(all("homepage verified" in candidate.reasons for candidate in candidates))

    def test_verified_preferred_candidate_skips_speculative_verification_wave(self):
        class PreferredFirstFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if domain_of(url) == "acme.ai":
                    return Page(
                        url=url,
                        final_url="https://acme.ai/",
                        html="<html><head><title>Acme</title></head><body>Acme</body></html>",
                    )
                time.sleep(0.2)
                raise FetchError("speculative candidate unavailable")

        fetcher = PreferredFirstFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)
        started = time.monotonic()

        candidates = resolver._rank_and_verify_candidates(
            ["https://acme.com", "https://acme.ai", "https://acme.io"],
            "Acme",
            None,
            candidate_sources={"acme.ai": {"preferred_input"}},
        )

        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual(fetcher.calls, ["https://acme.ai"])
        selected = resolver._select_verified_candidate(candidates)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.url, "https://acme.ai/")

    def test_verified_short_brand_preferred_domain_beats_speculative_dot_com(self):
        class ShortBrandFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                domain = domain_of(url)
                if domain == "rivr.ai":
                    return Page(
                        url=url,
                        final_url="https://www.rivr.ai/",
                        html="<html><head><title>RIVR</title></head><body>RIVR robotics</body></html>",
                    )
                if "linkedin.com" in domain or "bing.com" in domain or "duckduckgo.com" in domain:
                    raise FetchError("external evidence unavailable")
                raise AssertionError(f"speculative candidate should not be fetched: {url}")

        fetcher = ShortBrandFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        website_url, trace = resolver.resolve(
            "RIVR",
            preferred_url="https://www.rivr.ai",
        )

        self.assertEqual(website_url, "https://www.rivr.ai/")
        self.assertIn("candidate source: preferred_input", trace["selected"]["reasons"])
        self.assertFalse(any(domain_of(url) == "rivr.com" for url in fetcher.calls))

    def test_short_brand_title_with_legal_entity_suffix_confirms_identity(self):
        class LegalEntityTitleFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://atira.ai/",
                    html=(
                        "<html><head><title>Atira GmbH</title></head>"
                        "<body>Atira builds software.</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(LegalEntityTitleFetcher(offline=True))
        candidate = resolver._score_candidate("https://atira.ai", "Atira", verify=True)

        self.assertIn("homepage title confirms company identity", candidate.reasons)
        self.assertIsNotNone(resolver._select_verified_candidate([candidate]))

    def test_short_brand_title_late_in_bounded_head_confirms_identity(self):
        class LateHeadTitleFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://www.visa.com.sg/",
                    html=(
                        "<html><head>"
                        + '<meta name="padding" content="' + ("x" * 12000) + '">'
                        + "<title>Visa | Global Payments</title></head>"
                        + "<body>Regional navigation</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(LateHeadTitleFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://www.visa.com",
            "Visa",
            verify=True,
        )

        self.assertIn("homepage title confirms company identity", candidate.reasons)
        self.assertNotIn("company token 'visa' in homepage", candidate.reasons)
        self.assertIsNotNone(resolver._select_verified_candidate([candidate]))

    def test_title_after_bounded_head_limit_does_not_confirm_short_brand(self):
        class OversizedHeadFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://www.visa.com.sg/",
                    html=(
                        "<html><head>"
                        + ("x" * 66000)
                        + "<title>Visa | Unbounded Evidence</title></head>"
                        + "<body>Regional navigation</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(OversizedHeadFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://www.visa.com",
            "Visa",
            verify=True,
        )

        self.assertNotIn("homepage title confirms company identity", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

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

    def test_search_snippet_cannot_replace_homepage_identity(self):
        class SearchEvidenceFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.homepage_calls = []

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
                    self.homepage_calls.append(url)
                    return Page(url=url, final_url=url, html="<html><body>Build better software</body></html>")
                raise FetchError("not this candidate")

        fetcher = SearchEvidenceFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=2)

        website_url, trace = resolver.resolve("Ada")

        self.assertIsNone(website_url)
        candidate = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "ada.com"
        )
        self.assertIn("company token missing from homepage", candidate["reasons"])
        self.assertGreaterEqual(len(fetcher.homepage_calls), 1)

    def test_stale_linkedin_slug_dot_com_redirect_shell_is_not_verified(self):
        linkedin_url = "https://www.linkedin.com/company/northstar"

        class StaleShellFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    raise FetchError("LinkedIn unavailable")
                if domain_of(url) == "northstar.com":
                    return Page(
                        url=url,
                        final_url="https://northstar.com/",
                        html=(
                            "<html><head><title>Northstar</title></head><body>"
                            "<script>window.location = 'https://other-company.example/'</script>"
                            "</body></html>"
                        ),
                    )
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, final_url=url, html="<html></html>")
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            StaleShellFetcher(offline=True), verify_limit=3
        ).resolve("Northstar", linkedin_url, preferred_url="https://northstar.com")

        self.assertIsNone(website_url)
        stale = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "northstar.com"
        )
        self.assertIn("cross-origin client redirect is migration hint only", stale["reasons"])
        self.assertNotIn("homepage verified", stale["reasons"])

    def test_same_origin_redirect_shell_follows_one_hop_and_revalidates(self):
        class SameOriginRedirectFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") == "https://northstar.com":
                    return Page(
                        url=url,
                        final_url="https://northstar.com/",
                        html=(
                            '<html><head><meta http-equiv="refresh" '
                            'content="0; url=/company"></head><body></body></html>'
                        ),
                    )
                if url == "https://northstar.com/company":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<html><head><title>Northstar</title></head>"
                            "<body>Northstar builds navigation software.</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = SameOriginRedirectFetcher()
        resolver = CompanyWebsiteResolver(fetcher)
        candidate = resolver._score_candidate("https://northstar.com", "Northstar")

        self.assertEqual(candidate.url, "https://northstar.com/company")
        self.assertIn("same-origin client redirect followed", candidate.reasons)
        self.assertIn("homepage body confirms company identity", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_stash_onload_wrapper_redirect_shell_is_followed_and_revalidated(self):
        shell = (
            "<html><body><script>window.onload=function(){"
            'window.location.href="/lander"'
            "}</script></body></html>"
        )

        class StashWrapperFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == "https://stash.com":
                    return Page(url=url, final_url=url, html=shell)
                if url == "https://stash.com/lander":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<html><head><title>Stash</title></head><body></body></html>",
                    )
                raise FetchError("not this candidate")

        fetcher = StashWrapperFetcher()
        resolver = CompanyWebsiteResolver(fetcher)
        candidate = resolver._score_candidate("https://stash.com", "Stash")

        self.assertEqual(fetcher.calls, ["https://stash.com", "https://stash.com/lander"])
        self.assertEqual(candidate.url, "https://stash.com/lander")
        self.assertIn("same-origin client redirect followed", candidate.reasons)
        self.assertIn("homepage title confirms company identity", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_onload_wrapper_with_other_script_or_logic_is_not_a_redirect_shell(self):
        cases = (
            (
                "additional script",
                "<html><head><title>Stash</title></head><body>"
                "<script>window.analyticsLoaded=true</script>"
                "<script>window.onload=function(){window.location.href='/lander'}</script>"
                "</body></html>",
            ),
            (
                "additional function logic",
                "<html><head><title>Stash</title></head><body><script>"
                "window.onload=function(){console.log('loading');"
                "window.location.replace('/lander')}"
                "</script></body></html>",
            ),
            (
                "visible page content",
                "<html><head><title>Stash</title></head><body>"
                "Stash customer account overview"
                "<script>window.onload=function(){window.location.href='/lander'}</script>"
                "</body></html>",
            ),
        )

        for label, shell in cases:
            with self.subTest(label=label):
                class NonShellFetcher(Fetcher):
                    def __init__(self):
                        super().__init__(offline=True)
                        self.calls = []

                    def fetch(self, url, data=None, headers=None):
                        self.calls.append(url)
                        return Page(url=url, final_url=url, html=shell)

                fetcher = NonShellFetcher()
                candidate = CompanyWebsiteResolver(fetcher)._score_candidate(
                    "https://stash.com", "Stash"
                )

                self.assertEqual(fetcher.calls, ["https://stash.com"])
                self.assertNotIn("same-origin client redirect followed", candidate.reasons)

    def test_cross_origin_redirect_shell_is_not_followed_or_trusted(self):
        class CrossOriginRedirectFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        "<html><head><title>Northstar</title>"
                        '<meta http-equiv="refresh" content="1;url=https://northstar.io/">'
                        "</head><body></body></html>"
                    ),
                )

        fetcher = CrossOriginRedirectFetcher()
        resolver = CompanyWebsiteResolver(fetcher)
        candidate = resolver._score_candidate("https://northstar.com", "Northstar")

        self.assertEqual(fetcher.calls, ["https://northstar.com"])
        self.assertIn("cross-origin client redirect is migration hint only", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_search_migration_candidate_must_fetch_and_confirm_target_identity(self):
        class SearchMigrationFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "old-acme-systems.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<html><body><script>"
                            "window.location.href = 'https://acme-systems.io/'"
                            "</script></body></html>"
                        ),
                    )
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel><item><title>Acme Systems</title>"
                            "<description>Acme Systems official website</description>"
                            "<link>https://acme-systems.io/</link></item></channel></rss>"
                        ),
                    )
                if domain_of(url) == "acme-systems.io":
                    return Page(
                        url=url,
                        final_url="https://acme-systems.io/",
                        html=(
                            '<html><script type="application/ld+json">'
                            '{"@type":"Organization","legalName":"Acme Systems, Inc."}'
                            "</script><body>Engineering reliable systems.</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            SearchMigrationFetcher(offline=True), verify_limit=3
        ).resolve("Acme Systems", preferred_url="https://old-acme-systems.com")

        self.assertEqual(website_url, "https://acme-systems.io/")
        self.assertIn(
            "homepage organization data confirms company identity",
            trace["selected"]["reasons"],
        )
        self.assertIn("candidate source: search_evidence", trace["selected"]["reasons"])

    def test_search_collision_with_mismatched_page_identity_is_rejected(self):
        class SearchCollisionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel><item><title>Acme Labs official website</title>"
                            "<description>Visit Acme Labs online</description>"
                            "<link>https://acme.com/</link></item></channel></rss>"
                        ),
                    )
                if domain_of(url) == "acme.com":
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<html><head><title>Acme Plumbing</title></head>"
                            "<body>Acme Plumbing services</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            SearchCollisionFetcher(offline=True), verify_limit=3
        ).resolve("Acme Labs")

        self.assertIsNone(website_url)
        collision = next(
            item for item in trace["candidates"] if domain_of(item["url"]) == "acme.com"
        )
        self.assertIn("search result confirms company identity", collision["reasons"])
        self.assertNotIn("homepage body confirms company identity", collision["reasons"])

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
        self.assertIsNone(resolver._select_verified_candidate([exact, unrelated]))

    def test_ambiguous_non_com_search_candidate_needs_homepage_identity(self):
        class CleraFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel><item><title>Customer dashboard</title>"
                            "<description>Sign in to manage your account</description>"
                            "<link>https://www.clera.uk/dashboard</link></item></channel></rss>"
                        ),
                    )
                if url.rstrip("/") == "https://www.clera.uk":
                    return Page(
                        url=url,
                        final_url="https://www.clera.uk/dashboard",
                        html="<html><head><title>Dashboard</title></head><body>Sign in</body></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(CleraFetcher(offline=True), verify_limit=3)

        website_url, trace = resolver.resolve(
            "Clera",
            "https://www.linkedin.com/company/getclera",
        )

        self.assertIsNone(website_url)
        candidate = next(item for item in trace["candidates"] if domain_of(item["url"]) == "clera.uk")
        self.assertEqual(candidate["score"], 30)
        self.assertIn("LinkedIn slug confirms domain", candidate["reasons"])
        self.assertIn("company token missing from homepage", candidate["reasons"])
        self.assertIn("candidate source: search_evidence", candidate["reasons"])

    def test_ambiguous_non_com_domain_with_homepage_identity_is_selected(self):
        class BrandHomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html="<html><head><title>Dashboard</title></head><body>Clera account</body></html>",
                )

        resolver = CompanyWebsiteResolver(BrandHomepageFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://www.clera.uk/dashboard",
            "Clera",
            linkedin_company_url="https://www.linkedin.com/company/getclera",
            verify=True,
        )

        self.assertNotIn("company token missing from homepage", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

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

    def test_single_character_brand_can_use_exact_linkedin_slug_domain(self):
        class XHomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://x.com/",
                    html='<html><body><main aria-label="X">X</main></body></html>',
                )

        resolver = CompanyWebsiteResolver(XHomepageFetcher(offline=True))

        candidates = resolver._linkedin_slug_domain_candidates(
            "https://www.linkedin.com/company/x-corp"
        )
        candidate = resolver._score_candidate(
            "https://x.com",
            "X",
            linkedin_company_url="https://www.linkedin.com/company/x-corp",
            verify=True,
        )

        self.assertIn("https://x.com", candidates)
        self.assertIn("LinkedIn slug confirms domain", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_parent_domain_does_not_confirm_only_part_of_multiword_brand(self):
        class GoogleHomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://www.google.com/",
                    html="<html><head><title>Google</title></head><body>Google</body></html>",
                )

        resolver = CompanyWebsiteResolver(GoogleHomepageFetcher(offline=True))

        candidate = resolver._score_candidate(
            "https://google.com",
            "Google DeepMind",
            linkedin_company_url="https://www.linkedin.com/company/googledeepmind",
            verify=True,
        )

        self.assertIn("incomplete company identity", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_parent_group_homepage_is_not_exact_subsidiary_identity(self):
        linkedin_url = "https://www.linkedin.com/company/tata-technologies"

        class TataGroupFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url.rstrip("/") == linkedin_url:
                    return Page(
                        url=url,
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Tata Technologies",'
                            '"sameAs":"https://www.tata.com/"}'
                            "</script>"
                        ),
                    )
                if domain_of(url) == "tata.com":
                    return Page(
                        url=url,
                        final_url="https://www.tata.com/",
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Tata"}'
                            "</script><title>Tata group</title><body>Tata</body>"
                        ),
                    )
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            TataGroupFetcher(offline=True),
            verify_limit=3,
        ).resolve(
            "Tata Technologies",
            linkedin_url,
            preferred_url="https://www.tata.com/",
        )

        self.assertIsNone(website_url)
        parent_candidate = next(
            candidate
            for candidate in trace["candidates"]
            if domain_of(candidate["url"]) == "tata.com"
        )
        self.assertIn(
            "LinkedIn company page identifies official website",
            parent_candidate["reasons"],
        )
        self.assertIn(
            "parent/group website requires downstream hiring relationship evidence",
            parent_candidate["reasons"],
        )

    def test_exact_multiword_brand_homepage_remains_selectable(self):
        class ExactBrandFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "tatatechnologies.com":
                    return Page(
                        url=url,
                        final_url="https://www.tatatechnologies.com/",
                        html=(
                            '<script type="application/ld+json">'
                            '{"@type":"Organization","name":"Tata Technologies"}'
                            "</script><title>Tata Technologies</title>"
                            "<body>Tata Technologies</body>"
                        ),
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(ExactBrandFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://www.tatatechnologies.com/",
            "Tata Technologies",
            verify=True,
        )

        self.assertNotIn(
            "parent/group website requires downstream hiring relationship evidence",
            candidate.reasons,
        )
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_partial_name_canonical_is_not_trusted_for_multiword_brand(self):
        class ParentCanonicalFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        '<html><head><link rel="canonical" href="https://google.com/"></head>'
                        "<body>Google DeepMind</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ParentCanonicalFetcher(offline=True))

        candidate = resolver._score_candidate(
            "https://deepmind.google",
            "Google DeepMind",
            verify=True,
        )

        self.assertEqual(candidate.url, "https://deepmind.google")
        self.assertNotIn("homepage canonical URL", candidate.reasons)

    def test_search_evidence_gets_verification_slot_ahead_of_higher_scoring_guesses(self):
        class SonyFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.homepage_calls = []

            def fetch(self, url, data=None, headers=None):
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                if "format=rss" in url:
                    return Page(
                        url=url,
                        final_url=url,
                        html=(
                            "<rss><channel>"
                            "<item><title>Sony Corporation | Official Website</title>"
                            "<description>Official parent company website for Sony.</description>"
                            "<link>https://www.sony.com/</link></item>"
                            "<item>"
                            "<title>Sony Interactive Entertainment | Official Website</title>"
                            "<description>PlayStation is the official home of Sony Interactive Entertainment.</description>"
                            "<link>https://www.playstation.com/</link>"
                            "</item></channel></rss>"
                        ),
                    )
                self.homepage_calls.append(url)
                if url.rstrip("/") == "https://www.playstation.com":
                    return Page(
                        url=url,
                        final_url="https://www.playstation.com/",
                        html=(
                            "<html><head><title>Sony Interactive Entertainment | PlayStation</title></head>"
                            "<body>Sony Interactive Entertainment official products and careers</body></html>"
                        ),
                    )
                raise FetchError("speculative domain does not exist")

        fetcher = SonyFetcher()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)

        website_url, trace = resolver.resolve("Sony Interactive Entertainment")

        self.assertEqual(website_url, "https://www.playstation.com/")
        self.assertIn("https://www.playstation.com", fetcher.homepage_calls)
        self.assertIn("candidate source: search_evidence", trace["selected"]["reasons"])

    def test_financing_batch_qualifier_is_removed_but_brand_parenthetical_is_preserved(self):
        self.assertEqual(tokenize_company_name("Multifactor (YC F25)"), ["multifactor"])
        self.assertEqual(tokenize_company_name("Multifactor (Seed Funded)"), ["multifactor"])
        self.assertEqual(tokenize_company_name("Acme (North America)"), ["acme", "north", "america"])

    def test_multifactor_yc_qualifier_does_not_pollute_guessed_domains(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        candidates = resolver._guess_domain_candidates("Multifactor (YC F25)")

        self.assertIn("https://multifactor.com", candidates)
        self.assertFalse(any("yc" in candidate or "f25" in candidate for candidate in candidates))

    def test_exact_brand_dot_org_is_verified_after_dot_com_failure(self):
        class DotOrgFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.homepage_domains: list[str] = []

            def fetch(self, url, data=None, headers=None):
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                domain = domain_of(url)
                self.homepage_domains.append(domain)
                if domain == "cedarharbor.com":
                    raise FetchError("domain unavailable")
                if domain == "cedarharbor.org":
                    return Page(
                        url=url,
                        final_url="https://cedarharbor.org/",
                        html=(
                            '<html><head><title>Cedar Harbor</title>'
                            '<link rel="canonical" href="https://cedarharbor.org/">'
                            "</head><body>Cedar Harbor</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = DotOrgFetcher()
        website_url, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=3,
        ).resolve("Cedar Harbor")

        self.assertEqual(website_url, "https://cedarharbor.org/")
        self.assertIn("cedarharbor.org", fetcher.homepage_domains)
        self.assertNotIn("getcedarharbor.com", fetcher.homepage_domains)
        self.assertIn("homepage title confirms company identity", trace["selected"]["reasons"])

    def test_same_name_dot_org_with_conflicting_region_is_rejected(self):
        class RegionalCollisionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                if domain_of(url) == "cedarharbor.org":
                    return Page(
                        url=url,
                        final_url="https://cedarharbor.cn/",
                        html="<title>Cedar Harbor</title><body>Cedar Harbor</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            RegionalCollisionFetcher(offline=True),
            verify_limit=3,
        ).resolve("Cedar Harbor", job_location="Boston, United States")

        self.assertIsNone(website_url)
        dot_org = next(
            candidate
            for candidate in trace["candidates"]
            if domain_of(candidate["url"]) == "cedarharbor.cn"
        )
        self.assertIn(
            "regional website conflicts with job location: cn vs us",
            dot_org["reasons"],
        )

    def test_exact_brand_dot_org_without_identity_evidence_is_unresolved(self):
        class NoIdentityFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "bing.com" in url or "duckduckgo.com" in url:
                    return Page(url=url, html="<html></html>")
                if domain_of(url) == "cedarharbor.org":
                    return Page(
                        url=url,
                        final_url="https://cedarharbor.org/",
                        html="<title>Welcome</title><body>Community resources</body>",
                    )
                raise FetchError("not this candidate")

        website_url, trace = CompanyWebsiteResolver(
            NoIdentityFetcher(offline=True),
            verify_limit=3,
        ).resolve("Cedar Harbor")

        self.assertIsNone(website_url)
        dot_org = next(
            candidate
            for candidate in trace["candidates"]
            if domain_of(candidate["url"]) == "cedarharbor.org"
        )
        self.assertIn("company token missing from homepage", dot_org["reasons"])
        self.assertNotIn("homepage title confirms company identity", dot_org["reasons"])

    def test_guess_candidates_can_use_terminal_technology_token_as_tld(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        candidates = resolver._guess_domain_candidates("P-1 AI")

        self.assertEqual(candidates[0], "https://p1.ai")

    def test_guess_candidates_add_constrained_all_token_edu_acronym(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        institutional = resolver._guess_domain_candidates(
            "Southern New Hampshire University"
        )
        non_institutional = resolver._guess_domain_candidates(
            "Southern New Hampshire Software"
        )

        self.assertEqual(institutional[0], "https://snhu.edu")
        self.assertNotIn("https://snhs.edu", non_institutional)

    def test_generated_brand_tld_and_edu_acronym_are_selectable_when_verified(self):
        class GeneratedCandidateFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                domain = domain_of(url)
                if domain == "p1.ai":
                    return Page(
                        url=url,
                        final_url="https://p1.ai/",
                        html="<html><head><title>P-1 AI</title></head><body>P-1 AI</body></html>",
                    )
                if domain == "snhu.edu":
                    return Page(
                        url=url,
                        final_url="https://www.snhu.edu/",
                        html="<html><head><title>SNHU</title></head><body>SNHU</body></html>",
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(GeneratedCandidateFetcher(offline=True))

        p1_url, _p1_trace = resolver.resolve("P-1 AI")
        snhu_url, snhu_trace = resolver.resolve("Southern New Hampshire University")

        self.assertEqual(p1_url, "https://p1.ai/")
        self.assertEqual(snhu_url, "https://www.snhu.edu/")
        self.assertIn(
            "homepage title confirms company abbreviation",
            snhu_trace["selected"]["reasons"],
        )

    def test_exact_institutional_acronym_edu_can_use_access_denied_evidence(self):
        class AccessControlledInstitutionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) == "snhu.edu":
                    raise FetchError(
                        "Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(
            AccessControlledInstitutionFetcher(offline=True),
            verify_limit=3,
        )

        website_url, trace = resolver.resolve("Southern New Hampshire University")

        self.assertEqual(website_url, "https://snhu.edu")
        self.assertIn(
            "access-controlled institutional acronym",
            trace["selected"]["reasons"],
        )
        self.assertIn(
            "homepage access denied: HTTP_FORBIDDEN (403)",
            trace["selected"]["reasons"],
        )

    def test_institutional_acronym_access_fallback_is_narrow(self):
        cases = (
            ("Southern New Hampshire University", "snhu.edu", 404),
            ("Southern New Hampshire University", "snhu.com", 403),
            ("Royal Art University", "rau.edu", 403),
            ("Southern New Hampshire Software", "snhs.edu", 403),
        )
        for company_name, denied_domain, status in cases:
            with self.subTest(company_name=company_name, domain=denied_domain, status=status):
                class RejectedFallbackFetcher(Fetcher):
                    def fetch(self, url, data=None, headers=None):
                        if domain_of(url) == denied_domain:
                            raise FetchError(
                                "access response",
                                status=status,
                                reason_code=(
                                    "HTTP_FORBIDDEN" if status == 403 else "HTTP_NOT_FOUND"
                                ),
                                retryable=False,
                            )
                        raise FetchError("not this candidate")

                resolver = CompanyWebsiteResolver(
                    RejectedFallbackFetcher(offline=True),
                    verify_limit=12,
                )
                website_url, _trace = resolver.resolve(company_name)
                self.assertIsNone(website_url)

    def test_verified_identity_beats_access_controlled_institutional_acronym(self):
        class CompetingInstitutionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                domain = domain_of(url)
                if domain == "snhu.edu":
                    raise FetchError(
                        "Forbidden",
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    )
                if domain == "southernnewhampshireuniversity.com":
                    return Page(
                        url=url,
                        final_url="https://southernnewhampshireuniversity.com/",
                        html=(
                            "<html><head><title>Southern New Hampshire University</title></head>"
                            "<body>Southern New Hampshire University</body></html>"
                        ),
                    )
                raise FetchError("not this candidate")

        resolver = CompanyWebsiteResolver(
            CompetingInstitutionFetcher(offline=True),
            verify_limit=3,
        )

        website_url, _trace = resolver.resolve("Southern New Hampshire University")

        self.assertEqual(website_url, "https://southernnewhampshireuniversity.com/")

    def test_incomplete_body_only_identity_cannot_select_unrelated_company(self):
        class TokenCollisionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://us.pg.com/",
                    html=(
                        "<html><head><title>P and G consumer products</title></head>"
                        "<body>P describes one topic."
                        + (" unrelated content" * 400)
                        + " Section 1 separately mentions AI.</body>"
                        "</html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(TokenCollisionFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://us.pg.com",
            "P-1 AI",
            verify=True,
        )

        self.assertGreaterEqual(candidate.score, 25)
        self.assertIn("homepage body confirms company identity", candidate.reasons)
        self.assertIn("incomplete company identity", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_verified_preferred_homepage_may_omit_generic_group_suffix(self):
        class GroupHomepageFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) != "bosch.com":
                    raise FetchError("not this candidate")
                return Page(
                    url=url,
                    final_url="https://www.bosch.com/",
                    html="<html><head><title>Bosch</title></head><body>Bosch</body></html>",
                )

        resolver = CompanyWebsiteResolver(GroupHomepageFetcher(offline=True))

        website_url, trace = resolver.resolve(
            "Bosch Group",
            preferred_url="https://www.bosch.com",
        )

        self.assertEqual(website_url, "https://www.bosch.com/")
        self.assertIn(
            "homepage title confirms core company identity",
            trace["selected"]["reasons"],
        )

    def test_verified_preferred_parent_brand_does_not_drop_product_qualifier(self):
        class ParentBrandFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) != "bosch.com":
                    raise FetchError("not this candidate")
                return Page(
                    url=url,
                    final_url="https://www.bosch.com/",
                    html="<html><head><title>Bosch</title></head><body>Bosch</body></html>",
                )

        resolver = CompanyWebsiteResolver(ParentBrandFetcher(offline=True))

        website_url, trace = resolver.resolve(
            "Bosch Home",
            preferred_url="https://www.bosch.com",
        )

        self.assertIsNone(website_url)
        preferred = next(
            candidate
            for candidate in trace["candidates"]
            if domain_of(candidate["url"]) == "bosch.com"
        )
        self.assertIn("incomplete company identity", preferred["reasons"])

    def test_search_evidence_survives_speculative_budget_for_explore30_company_shapes(self):
        cases = (
            ("Hadrian Automation, Inc.", "hadrian.co"),
            ("Paramount Global", "paramount.com"),
            ("DocuSign Agreement Cloud", "docusign.com"),
        )

        for company_name, official_domain in cases:
            with self.subTest(company_name=company_name):
                class EvidenceFetcher(Fetcher):
                    def fetch(self, url, data=None, headers=None):
                        if "linkedin.com" in url:
                            raise FetchError("LinkedIn unavailable")
                        if "format=rss" in url:
                            return Page(
                                url=url,
                                final_url=url,
                                html=(
                                    "<rss><channel><item>"
                                    f"<title>{company_name} Official Website</title>"
                                    f"<description>Official homepage for {company_name}</description>"
                                    f"<link>https://{official_domain}/</link>"
                                    "</item></channel></rss>"
                                ),
                            )
                        if domain_of(url) == official_domain:
                            return Page(
                                url=url,
                                final_url=f"https://{official_domain}/",
                                html=f"<html><head><title>{company_name}</title></head><body>{company_name}</body></html>",
                            )
                        raise FetchError("speculative domain does not exist")

                resolver = CompanyWebsiteResolver(EvidenceFetcher(offline=True), verify_limit=3)

                website_url, trace = resolver.resolve(company_name)

                self.assertEqual(domain_of(website_url or ""), official_domain)
                self.assertIn("candidate source: search_evidence", trace["selected"]["reasons"])

    def test_parked_domain_marketplace_redirect_is_never_selected(self):
        class ParkedDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://domains.atom.com/lpd/name/Paramount.co",
                    html=(
                        "<html><head><title>Paramount.co is for sale</title></head>"
                        "<body>Buy this domain on our domain marketplace.</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ParkedDomainFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://paramount.co",
            "Paramount",
            verify=True,
        )

        self.assertIn("parked domain rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_godaddy_parking_lander_with_company_search_terms_is_rejected(self):
        class GoDaddyParkingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=f"{url.rstrip('/')}/lander",
                    html=(
                        '<script>window.LANDER_SYSTEM="PW"</script>'
                        '<script src="https://img1.wsimg.com/parking-lander/static/js/main.js"></script>'
                        '<body><div>Hugh Chatham Health</div>'
                        '<div>hughchathamhealth.com is parked free, courtesy of GoDaddy.com.</div></body>'
                    ),
                )

        resolver = CompanyWebsiteResolver(GoDaddyParkingFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://hughchathamhealth.com",
            "Hugh Chatham Health",
            verify=True,
        )

        self.assertIn("parked domain rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_ad_iframe_parking_lander_with_company_name_is_rejected(self):
        class AdParkingFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        '<html><head><title>Acme Robotics</title>'
                        '<script src="https://l.cdn-fileserver.com/consent/manager.js"></script>'
                        '</head><body data-mode="vgd_l2type=dmola">'
                        '<div>Acme Robotics</div>'
                        '<iframe src="https://findresultsquick.com/ads/SAFEFRAME.html"></iframe>'
                        '</body></html>'
                    ),
                )

        resolver = CompanyWebsiteResolver(AdParkingFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://acme-robotics.com",
            "Acme Robotics",
            verify=True,
        )

        self.assertIn("parked domain rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_direct_http_website_evidence_is_verified_over_https(self):
        class HttpsOnlyFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == "https://acme.example/about":
                    return Page(
                        url=url,
                        final_url=url,
                        html="<title>Acme</title><main>Acme products</main>",
                    )
                raise FetchError("unexpected transport")

        fetcher = HttpsOnlyFetcher()
        website, _trace = CompanyWebsiteResolver(fetcher).resolve(
            "Acme",
            preferred_url="http://www.acme.example/about",
        )

        self.assertEqual(website, "https://acme.example/about")
        self.assertIn("https://www.acme.example/about", fetcher.calls)
        self.assertIn("https://acme.example/about", fetcher.calls)
        self.assertNotIn("http://www.acme.example/about", fetcher.calls)

    def test_redirect_to_hosted_non_company_destination_is_never_selected(self):
        class HostedRedirectFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://standardtemplatelabs-com.l.ink/",
                    html="<html><head><title>Standard Template Labs</title></head></html>",
                )

        resolver = CompanyWebsiteResolver(HostedRedirectFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://standardtemplatelabs.com",
            "Standard Template Labs",
            verify=True,
        )

        self.assertIn("hosted non-company destination rejected", candidate.reasons)
        self.assertNotIn("homepage verified", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_single_token_product_brand_domain_needs_organizational_evidence(self):
        class ProductDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://www.paramountplus.com/intl/",
                    html=(
                        "<html><head><title>Paramount+ United States</title></head>"
                        "<body>Stream Paramount movies and shows.</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ProductDomainFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://www.paramountplus.com/intl/",
            "Paramount",
            verify=True,
            search_evidence=None,
        )

        self.assertIn("single-token brand extension domain", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_common_organizational_prefix_is_not_treated_as_product_extension(self):
        class CompanyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://getacme.com/",
                    html="<html><head><title>Acme | Official Website</title></head><body>Acme</body></html>",
                )

        resolver = CompanyWebsiteResolver(CompanyFetcher(offline=True))
        candidate = resolver._score_candidate("https://getacme.com", "Acme", verify=True)

        self.assertNotIn("single-token brand extension domain", candidate.reasons)
        self.assertIsNotNone(resolver._select_verified_candidate([candidate]))

    def test_product_subdomain_on_another_company_site_is_not_same_entity(self):
        class ProductSubdomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://haystack.deepset.ai/",
                    html=(
                        "<html><head><title>Haystack by deepset</title></head>"
                        "<body>Haystack documentation and product resources</body></html>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ProductSubdomainFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://haystack.deepset.ai",
            "Haystack",
            linkedin_company_url="https://www.linkedin.com/company/haystack",
            verify=True,
        )

        self.assertIn(
            "registrable domain does not establish company ownership",
            candidate.reasons,
        )
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_extension_domain_with_two_strong_page_signals_is_selectable(self):
        class ExtensionDomainFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://us.squareup.com/",
                    html=(
                        '<script type="application/ld+json">'
                        '{"@type":"Organization","name":"Square"}'
                        "</script><title>Square | Official Website</title>"
                        "<body>Square commerce solutions</body>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ExtensionDomainFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://us.squareup.com/",
            "Square",
            job_location="United States",
            verify=True,
        )

        self.assertIn(
            "registrable domain does not establish company ownership",
            candidate.reasons,
        )
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_regional_extension_site_with_two_strong_page_signals_is_selectable(self):
        class RegionalExtensionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://us.puma.partner-retail.com/en-us/",
                    html=(
                        '<script type="application/ld+json">'
                        '{"@type":"Organization","name":"PUMA"}'
                        "</script><title>PUMA United States</title>"
                        "<body>Official PUMA products and stores</body>"
                    ),
                )

        resolver = CompanyWebsiteResolver(RegionalExtensionFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://us.puma.partner-retail.com/en-us/",
            "PUMA",
            job_location="United States",
            verify=True,
        )

        self.assertIn("regional website matches job location: us", candidate.reasons)
        self.assertEqual(resolver._select_verified_candidate([candidate]), candidate)

    def test_same_token_on_unrelated_site_does_not_establish_ownership(self):
        class MarketplaceFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://adidas.jd.com/",
                    html=(
                        "<title>Adidas deals</title>"
                        "<body>Marketplace promotions and seasonal shopping</body>"
                    ),
                )

        resolver = CompanyWebsiteResolver(MarketplaceFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://adidas.jd.com/",
            "Adidas",
            verify=True,
        )

        self.assertIn(
            "registrable domain does not establish company ownership",
            candidate.reasons,
        )
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_search_snippet_cannot_authorize_extension_domain(self):
        class SnippetOnlyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://square.partner-commerce.com/",
                    html="<title>Payments</title><body>Merchant services</body>",
                )

        resolver = CompanyWebsiteResolver(SnippetOnlyFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://square.partner-commerce.com/",
            "Square",
            verify=True,
            search_evidence=SearchEvidence(
                "https://square.partner-commerce.com/",
                "Square official website",
                "Square merchant services",
            ),
        )

        self.assertIn("search result confirms company identity", candidate.reasons)
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_parent_group_conflict_still_rejects_extension_domain(self):
        class ParentGroupFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://careers.tata.com/",
                    html=(
                        '<script type="application/ld+json">'
                        '{"@type":"Organization","name":"Tata"}'
                        "</script><title>Tata Group</title>"
                        "<body>Tata Technologies careers</body>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ParentGroupFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://careers.tata.com/",
            "Tata Technologies",
            verify=True,
        )

        self.assertIn(
            "parent/group website requires downstream hiring relationship evidence",
            candidate.reasons,
        )
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_region_conflict_still_rejects_strong_extension_identity(self):
        class ConflictingRegionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://cn.puma.partner-retail.com/zh-cn/",
                    html=(
                        '<script type="application/ld+json">'
                        '{"@type":"Organization","name":"PUMA"}'
                        "</script><title>PUMA China</title>"
                        "<body>Official PUMA products and stores</body>"
                    ),
                )

        resolver = CompanyWebsiteResolver(ConflictingRegionFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://cn.puma.partner-retail.com/zh-cn/",
            "PUMA",
            job_location="United States",
            verify=True,
        )

        self.assertIn(
            "regional website conflicts with job location: cn vs us",
            candidate.reasons,
        )
        self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_marketplace_and_deployment_subdomains_are_not_corporate_roots(self):
        cases = (
            (
                "https://adidas.jd.com",
                "Adidas",
                "registrable domain does not establish company ownership",
            ),
            (
                "https://prod-deleg-sfcc.lacoste.com",
                "Lacoste",
                "deployment hostname",
            ),
        )

        for url, company_name, expected_reason in cases:
            with self.subTest(url=url):
                class SubdomainFetcher(Fetcher):
                    def fetch(self, request_url, data=None, headers=None):
                        return Page(
                            url=request_url,
                            final_url=request_url,
                            html=(
                                f"<html><head><title>{company_name}</title></head>"
                                f"<body>{company_name} products</body></html>"
                            ),
                        )

                resolver = CompanyWebsiteResolver(SubdomainFetcher(offline=True))
                candidate = resolver._score_candidate(url, company_name, verify=True)

                self.assertIn(expected_reason, candidate.reasons)
                self.assertIsNone(resolver._select_verified_candidate([candidate]))

    def test_company_owned_subdomain_on_multilabel_cctld_remains_eligible(self):
        class CareersFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://careers.maison.co.uk/",
                    html="<title>Maison Careers</title><body>Maison jobs</body>",
                )

        resolver = CompanyWebsiteResolver(CareersFetcher(offline=True))
        candidate = resolver._score_candidate(
            "https://careers.maison.co.uk",
            "Maison",
            job_location="London, United Kingdom",
            verify=True,
        )

        self.assertNotIn(
            "registrable domain does not establish company ownership",
            candidate.reasons,
        )
        self.assertIsNotNone(resolver._select_verified_candidate([candidate]))

    def test_full_linkedin_slug_keeps_a_verification_slot_against_ambiguous_preferred_site(self):
        linkedin_url = "https://www.linkedin.com/company/join-blossom-health"
        full_slug_site = "https://join-blossom-health.com/"

        class BlossomFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url.rstrip("/") == linkedin_url:
                    return Page(url=url, html="<title>Blossom | LinkedIn</title>")
                if domain_of(url) == "blossom.net":
                    return Page(
                        url=url,
                        final_url="https://blossom.net/",
                        html="<title>Blossom</title><body>Blossom products</body>",
                    )
                if domain_of(url) == "join-blossom-health.com":
                    return Page(
                        url=url,
                        final_url=full_slug_site,
                        html=(
                            "<title>Blossom Health | Official Website</title>"
                            "<body>Blossom Health careers</body>"
                        ),
                    )
                raise FetchError("not this candidate")

        fetcher = BlossomFetcher()
        website_url, trace = CompanyWebsiteResolver(fetcher, verify_limit=2).resolve(
            "Blossom",
            linkedin_url,
            "United States",
            "https://blossom.net",
        )

        self.assertEqual(website_url, full_slug_site)
        self.assertTrue(
            any(domain_of(call) == "join-blossom-health.com" for call in fetcher.calls)
        )
        self.assertIn("full LinkedIn slug matches domain", trace["selected"]["reasons"])

    def test_plain_slug_prefers_verified_dot_com_company_site_over_dot_ai(self):
        class TaskCompanyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if domain_of(url) in {"taskrabbit.com", "taskrabbit.ai"}:
                    return Page(
                        url=url,
                        final_url=f"https://{domain_of(url)}/",
                        html="<title>Taskrabbit</title><body>Taskrabbit</body>",
                    )
                if "linkedin.com" in url:
                    raise FetchError("LinkedIn unavailable")
                raise FetchError("not this candidate")

        website_url, _trace = CompanyWebsiteResolver(
            TaskCompanyFetcher(offline=True), verify_limit=3
        ).resolve(
            "Taskrabbit",
            "https://www.linkedin.com/company/taskrabbit",
            "United States",
        )

        self.assertEqual(website_url, "https://taskrabbit.com/")


if __name__ == "__main__":
    unittest.main()
