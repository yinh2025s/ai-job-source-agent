import unittest
from dataclasses import replace

from job_source_agent.direct_candidate_discovery import (
    ExternalApplyDiscovery,
    WebsiteCareerDiscovery,
)
from job_source_agent.provider_candidates import CandidateDiscoveryRequest
from job_source_agent.providers import ProviderRegistry
from job_source_agent.providers.domain import DomainProviderAdapter


class DirectCandidateDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry(
            (
                DomainProviderAdapter("ashby", ("ashbyhq.com",)),
                DomainProviderAdapter("greenhouse", ("greenhouse.io",)),
            )
        )
        self.request = CandidateDiscoveryRequest(
            company_name="Example",
            target_title="AI Engineer",
            target_location="Shanghai",
        )

    def test_external_apply_has_highest_priority_and_declared_provenance(self):
        result = ExternalApplyDiscovery(self.registry).discover(
            replace(
                self.request,
                external_apply_url="https://jobs.ashbyhq.com/example/123",
            )
        )

        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.source_kind, "external_apply")
        self.assertEqual(candidate.priority, 500)
        self.assertEqual(candidate.source_url, "https://jobs.ashbyhq.com/example/123")
        self.assertEqual(candidate.provider_hint, "ashby")
        self.assertEqual(result.trace["source"], "external_apply")
        self.assertNotIn("verified", result.trace["candidates"][0])

    def test_website_and_career_links_require_explicit_provider_url_evidence(self):
        result = WebsiteCareerDiscovery(self.registry).discover(
            replace(
                self.request,
                company_website_url="https://example.com/careers",
                career_page_url="https://boards.greenhouse.io/example",
            )
        )

        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.source_kind, "first_party_ats_link")
        self.assertEqual(candidate.source_url, "https://boards.greenhouse.io/example")
        self.assertEqual(candidate.provider_hint, "greenhouse")
        self.assertEqual(candidate.priority, 400)

    def test_website_source_omits_non_ats_url(self):
        result = WebsiteCareerDiscovery(self.registry).discover(
            replace(
                self.request,
                company_website_url="https://example.com/jobs",
            )
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["candidate_count"], 0)

    def test_discovery_safely_skips_malformed_and_private_urls(self):
        discovery = ExternalApplyDiscovery(self.registry)
        for url in ("not a url", "http://jobs.ashbyhq.com/example", "https://127.0.0.1/jobs"):
            with self.subTest(url=url):
                result = discovery.discover(
                    replace(self.request, external_apply_url=url)
                )
                self.assertEqual(result.candidates, ())

    def test_absent_urls_are_ignored(self):
        self.assertEqual(ExternalApplyDiscovery(self.registry).discover(self.request).candidates, ())
        self.assertEqual(WebsiteCareerDiscovery(self.registry).discover(self.request).candidates, ())


if __name__ == "__main__":
    unittest.main()
