from __future__ import annotations

import unittest

from job_source_agent.candidate_reasoning_contracts import CandidateEvidence
from job_source_agent.web import FetchError, Fetcher, Page
from job_source_agent.website_resolver import CompanyWebsiteResolver


class CandidateReasoningResolverAdapterTests(unittest.TestCase):
    def test_second_candidate_is_selected_only_after_deterministic_verification(self):
        class FetcherWithVerifiedSecond(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == "https://acme.com":
                    return Page(url=url, final_url=url, html="<title>Wrong Company</title>")
                if url == "https://acme.org":
                    return Page(
                        url=url,
                        final_url="https://acme.org/",
                        html=(
                            "<title>Acme</title><body>Acme official website"
                            "<a href='/careers'>Careers</a></body>"
                        ),
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = FetcherWithVerifiedSecond()
        resolver = CompanyWebsiteResolver(fetcher, verify_limit=3)
        candidates = (
            CandidateEvidence("wrong", "https://acme.com", "Acme", "", "search", "q1", 1),
            CandidateEvidence("right", "https://acme.org", "Acme", "", "search", "q1", 2),
        )

        website, trace, navigation = resolver.resolve_ranked_existing_candidates_with_navigation_evidence(
            candidates, "Acme"
        )

        self.assertEqual(website, "https://acme.org/")
        self.assertEqual(fetcher.calls, ["https://acme.com", "https://acme.org"])
        self.assertEqual(trace["candidate_route"], "llm_ranked_existing_website_candidates")
        self.assertIn("homepage verified", trace["selected"]["reasons"])
        self.assertIsNotNone(navigation)

    def test_high_ranked_unrelated_candidate_is_rejected_after_fetch(self):
        class UnrelatedFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                return Page(url=url, final_url=url, html="<title>Other Corporation</title>")

        fetcher = UnrelatedFetcher()
        website, trace = CompanyWebsiteResolver(fetcher).resolve_ranked_existing_candidates(
            ("https://other.example",), "Acme"
        )

        self.assertIsNone(website)
        self.assertEqual(fetcher.calls, ["https://other.example"])
        self.assertNotIn("selected", trace)
        self.assertIn("company token missing from homepage", trace["candidates"][0]["reasons"])

    def test_same_name_candidate_with_conflicting_region_is_rejected(self):
        class SameNameWrongRegionFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(url=url, final_url=url, html="<title>Acme</title><body>Acme</body>")

        website, trace = CompanyWebsiteResolver(
            SameNameWrongRegionFetcher(offline=True)
        ).resolve_ranked_existing_candidates(
            ("https://acme.co.uk",),
            "Acme",
            job_location="Seattle, WA",
        )

        self.assertIsNone(website)
        self.assertTrue(any(
            reason.startswith("regional website conflicts with job location:")
            for reason in trace["candidates"][0]["reasons"]
        ))

    def test_wrong_parent_identity_is_rejected(self):
        class ParentFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html="<title>Acme Group</title><body>Acme Group corporate website</body>",
                )

        website, trace = CompanyWebsiteResolver(
            ParentFetcher(offline=True)
        ).resolve_ranked_existing_candidates(
            ("https://acme-group.example",), "Acme Health"
        )

        self.assertIsNone(website)
        self.assertIn(
            "parent/group website requires downstream hiring relationship evidence",
            trace["candidates"][0]["reasons"],
        )

    def test_news_and_product_deep_links_are_rejected_without_fetch(self):
        class NeverFetch(Fetcher):
            def fetch(self, url, data=None, headers=None):
                raise AssertionError(f"non-company page was fetched: {url}")

        website, trace = CompanyWebsiteResolver(
            NeverFetch(offline=True)
        ).resolve_ranked_existing_candidates(
            (
                "https://acme.example/news/acme-launches",
                "https://acme.example/products/widget",
            ),
            "Acme",
        )

        self.assertIsNone(website)
        self.assertEqual(trace["candidates"], [])
        self.assertEqual(len(trace["rejected_candidates"]), 2)
        self.assertTrue(all(
            item["reason"] == "search result is a news, editorial, or product deep link"
            for item in trace["rejected_candidates"]
        ))

    def test_invalid_or_provider_candidates_are_never_fetched_or_selected(self):
        class NeverFetch(Fetcher):
            def fetch(self, url, data=None, headers=None):
                raise AssertionError(f"unsafe candidate was fetched: {url}")

        website, trace = CompanyWebsiteResolver(NeverFetch(offline=True)).resolve_ranked_existing_candidates(
            ("javascript:alert(1)", "https://boards.greenhouse.io/acme", "https://linkedin.com/company/acme"),
            "Acme",
        )

        self.assertIsNone(website)
        self.assertEqual(trace["candidates"], [])
        self.assertEqual(len(trace["rejected_candidates"]), 3)

    def test_more_than_three_candidates_is_rejected_before_any_fetch(self):
        resolver = CompanyWebsiteResolver(Fetcher(offline=True))

        with self.assertRaisesRegex(ValueError, "at most three"):
            resolver.resolve_ranked_existing_candidates(
                (
                    "https://one.example",
                    "https://two.example",
                    "https://three.example",
                    "https://four.example",
                ),
                "Acme",
            )


if __name__ == "__main__":
    unittest.main()
