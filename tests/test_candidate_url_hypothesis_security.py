from __future__ import annotations

import unittest
from dataclasses import fields

from job_source_agent.candidate_reasoning_contracts import (
    CandidateEvidence,
    QueryPlannerDecision,
    URLHypothesis,
)
from job_source_agent.company_discovery_evidence import (
    VerifiedCareerEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.identity_continuity import (
    HiringRelationshipEvidence,
    OpeningSelectionEvidence,
)
from job_source_agent.provider_candidates import (
    ProviderCandidate,
    VerifiedProviderCandidate,
)
from job_source_agent.web import Fetcher, Page
from job_source_agent.website_resolver import CompanyWebsiteResolver


class CandidateURLHypothesisSecurityTests(unittest.TestCase):
    def test_accepts_at_most_three_public_https_hypotheses(self):
        decision = QueryPlannerDecision.from_payload(
            self._planner_payload(
                [
                    self._hypothesis("https://acme.example/"),
                    self._hypothesis(
                        "https://careers.acme.example/jobs",
                        purpose="career_site",
                        confidence="medium",
                    ),
                    self._hypothesis(
                        "https://jobs.acme.example/openings",
                        purpose="provider_site",
                        confidence="low",
                    ),
                ]
            )
        )

        self.assertIsInstance(decision.url_hypotheses, tuple)
        self.assertEqual(
            tuple(item.url for item in decision.url_hypotheses),
            (
                "https://acme.example/",
                "https://careers.acme.example/jobs",
                "https://jobs.acme.example/openings",
            ),
        )

        with self.assertRaisesRegex(ValueError, "url_hypotheses exceeds limit"):
            QueryPlannerDecision.from_payload(
                self._planner_payload(
                    [
                        self._hypothesis(f"https://candidate-{index}.example/")
                        for index in range(4)
                    ]
                )
            )

    def test_rejects_non_https_credentials_ports_fragments_and_sensitive_queries(self):
        unsafe_urls = (
            "http://acme.example/careers",
            "https://user@acme.example/careers",
            "https://user:secret@acme.example/careers",
            "https://acme.example:8443/careers",
            "https://acme.example/careers#openings",
            "https://acme.example/careers?token=secret",
            "https://acme.example/careers?API_KEY=secret",
            "https://acme.example/careers?session=private",
        )

        for url in unsafe_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                QueryPlannerDecision.from_payload(
                    self._planner_payload([self._hypothesis(url)])
                )

    def test_rejects_private_local_and_non_public_ip_hosts(self):
        unsafe_urls = (
            "https://localhost/careers",
            "https://service.local/careers",
            "https://service.internal/careers",
            "https://127.0.0.1/careers",
            "https://10.0.0.1/careers",
            "https://172.16.0.1/careers",
            "https://192.168.1.1/careers",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/careers",
        )

        for url in unsafe_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                QueryPlannerDecision.from_payload(
                    self._planner_payload([self._hypothesis(url)])
                )

    def test_rejects_search_and_social_hosts_including_subdomains(self):
        blocked_urls = (
            "https://google.com/search?q=acme",
            "https://www.bing.com/search?q=acme",
            "https://duckduckgo.com/?q=acme",
            "https://www.linkedin.com/company/acme",
            "https://jobs.facebook.com/acme",
            "https://instagram.com/acme",
            "https://mobile.twitter.com/acme",
            "https://x.com/acme",
        )

        for url in blocked_urls:
            with self.subTest(url=url), self.assertRaisesRegex(
                ValueError, "search or social host"
            ):
                QueryPlannerDecision.from_payload(
                    self._planner_payload([self._hypothesis(url)])
                )

        accepted = QueryPlannerDecision.from_payload(
            self._planner_payload(
                [self._hypothesis("https://careers.google.com/jobs")]
            )
        )
        self.assertEqual(
            accepted.url_hypotheses[0].url,
            "https://careers.google.com/jobs",
        )

    def test_rejects_duplicate_urls_and_unknown_fields(self):
        duplicate = self._hypothesis("https://acme.example/careers")
        with self.assertRaisesRegex(ValueError, "(?i)duplicate"):
            QueryPlannerDecision.from_payload(
                self._planner_payload([duplicate, duplicate.copy()])
            )
        with self.assertRaisesRegex(ValueError, "(?i)duplicate"):
            QueryPlannerDecision.from_payload(
                self._planner_payload(
                    [
                        self._hypothesis("https://ACME.example.:443/careers"),
                        self._hypothesis("https://acme.example/careers"),
                    ]
                )
            )

        unknown_planner_field = self._planner_payload([])
        unknown_planner_field["verified_company"] = True
        with self.assertRaisesRegex(ValueError, "exactly the required fields"):
            QueryPlannerDecision.from_payload(unknown_planner_field)

        unknown_hypothesis_field = self._hypothesis(
            "https://acme.example/careers"
        )
        unknown_hypothesis_field["tenant"] = "acme"
        with self.assertRaisesRegex(ValueError, "exactly the required fields"):
            QueryPlannerDecision.from_payload(
                self._planner_payload([unknown_hypothesis_field])
            )

    def test_hypothesis_has_no_verified_identity_or_opening_authority(self):
        hypothesis = URLHypothesis(
            "https://acme.example/careers",
            "career_site",
            "high",
        )
        self.assertEqual(
            {item.name for item in fields(URLHypothesis)},
            {"url", "purpose", "confidence"},
        )
        for forbidden_attribute in (
            "company_name",
            "relationship_evidence",
            "provider",
            "tenant",
            "opening_id",
            "opening_url",
            "verified",
        ):
            with self.subTest(attribute=forbidden_attribute):
                self.assertFalse(hasattr(hypothesis, forbidden_attribute))

        for authoritative_type in (
            VerifiedWebsiteEvidence,
            VerifiedCareerEvidence,
            VerifiedProviderBoardEvidence,
            HiringRelationshipEvidence,
            ProviderCandidate,
            VerifiedProviderCandidate,
            OpeningSelectionEvidence,
        ):
            with self.subTest(authoritative_type=authoritative_type.__name__):
                self.assertNotIsInstance(hypothesis, authoritative_type)

    def test_hypothesis_reaches_resolver_only_as_candidate_evidence(self):
        class IdentityCheckingFetcher(Fetcher):
            def __init__(self):
                super().__init__(offline=True)
                self.calls: list[str] = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                return Page(
                    url=url,
                    final_url=url,
                    html=(
                        "<title>Other Corporation</title>"
                        "<body>Other Corporation official website</body>"
                    ),
                )

        hypothesis = URLHypothesis(
            "https://acme.example/careers",
            "career_site",
            "high",
        )
        candidate = CandidateEvidence(
            candidate_id="llm-hypothesis-1",
            url=hypothesis.url,
            title="",
            snippet="",
            source="llm_url_hypothesis",
            query_id="llm_hypothesis",
            rank=1,
        )
        self.assertIsInstance(candidate, CandidateEvidence)

        fetcher = IdentityCheckingFetcher()
        website, trace = CompanyWebsiteResolver(
            fetcher,
            verify_limit=1,
        ).resolve_ranked_existing_candidates((candidate,), "Acme")

        self.assertIsNone(website)
        self.assertEqual(fetcher.calls, ["https://acme.example/careers"])
        self.assertNotIn("selected", trace)
        self.assertIn(
            "company token missing from homepage",
            trace["candidates"][0]["reasons"],
        )

    @staticmethod
    def _hypothesis(
        url: str,
        *,
        purpose: str = "official_website",
        confidence: str = "high",
    ) -> dict[str, object]:
        return {
            "url": url,
            "purpose": purpose,
            "confidence": confidence,
        }

    @staticmethod
    def _planner_payload(
        hypotheses: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "schema_version": "1",
            "normalized_company_name": "Acme",
            "core_brand_tokens": ["Acme"],
            "legal_or_descriptive_suffixes": [],
            "possible_aliases": [],
            "queries": [],
            "ambiguous": False,
            "reason_codes": [],
            "url_hypotheses": hypotheses,
        }


if __name__ == "__main__":
    unittest.main()
