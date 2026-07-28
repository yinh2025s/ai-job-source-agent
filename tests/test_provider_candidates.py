import unittest

from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.provider_candidates import (
    MAX_PROVIDER_CANDIDATES,
    ProviderCandidate,
    ProviderCandidatePool,
    ProviderPublishedBoardEmployerEvidence,
    ProviderPublishedEmployerEvidence,
    VerifiedProviderCandidate,
    provider_employer_matches_company,
)


def candidate(url, source_kind, **kwargs):
    defaults = {
        "source_url": url,
        "company_name": "Example",
        "target_title": "AI Engineer",
    }
    defaults.update(kwargs)
    return ProviderCandidate(url=url, source_kind=source_kind, **defaults)


class ProviderCandidateTests(unittest.TestCase):
    def test_provider_board_employer_evidence_is_typed_and_canonical(self):
        evidence = ProviderPublishedBoardEmployerEvidence(
            employer_name="City of Example",
            display_name="City of Example Human Resources",
            evidence_url="https://www.governmentjobs.com/careers/example/",
            extraction_method="governmentjobs_agency_heading",
        )

        self.assertEqual(
            evidence.evidence_url,
            "https://www.governmentjobs.com/careers/example",
        )
        self.assertEqual(
            evidence.to_trace_payload()["display_name"],
            "City of Example Human Resources",
        )

    def test_verified_probe_can_carry_provider_published_employer_evidence(self):
        evidence = ProviderPublishedEmployerEvidence(
            employer_name="Slant",
            descriptor_terms=("crm",),
            evidence_url="https://api.ashbyhq.com/posting-api/job-board/slant",
            opening_url="https://jobs.ashbyhq.com/slant/role-123",
            extraction_method="about_heading_self_description",
        )

        raw = candidate(
            "https://jobs.ashbyhq.com/slant",
            "verified_tenant_probe",
            provider_hint="ashby",
            provider_employer_evidence=evidence,
        )

        self.assertEqual(raw.provider_employer_evidence, evidence)
        self.assertEqual(
            raw.to_trace_payload()["provider_employer_evidence"]["descriptor_terms"],
            ["crm"],
        )

    def test_provider_employer_evidence_is_verified_opening_only_and_fail_closed(self):
        evidence = ProviderPublishedEmployerEvidence(
            employer_name="Other",
            descriptor_terms=(),
            evidence_url="https://api.ashbyhq.com/posting-api/job-board/other",
            opening_url="https://jobs.ashbyhq.com/other/role-123",
            extraction_method="about_heading_self_description",
        )
        with self.assertRaisesRegex(ValueError, "verified provider opening"):
            candidate(
                "https://jobs.ashbyhq.com/other",
                "targeted_board_search",
                source_url="https://www.bing.com/search?q=other",
                query="Other jobs",
                result_rank=1,
                provider_employer_evidence=evidence,
            )

        targeted = candidate(
            "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123",
            "targeted_opening_search",
            source_url="https://www.bing.com/search?q=other",
            query="Other role",
            result_rank=1,
            provider_employer_evidence=ProviderPublishedEmployerEvidence(
                employer_name="Other",
                descriptor_terms=(),
                evidence_url="https://recruiting.paylocity.com/Recruiting/Jobs/Details/123",
                opening_url="https://recruiting.paylocity.com/Recruiting/Jobs/Details/123",
                extraction_method="paylocity_detail_page_data",
            ),
        )
        self.assertEqual(targeted.provider_employer_evidence.employer_name, "Other")

    def test_provider_employer_binding_requires_every_missing_identity_token(self):
        evidence = ProviderPublishedEmployerEvidence(
            employer_name="Example",
            descriptor_terms=("ai", "crm"),
            evidence_url="https://api.ashbyhq.com/posting-api/job-board/example",
            opening_url="https://jobs.ashbyhq.com/example/role-123",
            extraction_method="about_heading_self_description",
        )

        self.assertTrue(provider_employer_matches_company("Example CRM", evidence))
        self.assertTrue(provider_employer_matches_company("Example", evidence))
        self.assertFalse(provider_employer_matches_company("Example Bank", evidence))
        self.assertFalse(provider_employer_matches_company("Other CRM", evidence))
        self.assertFalse(provider_employer_matches_company("Example CRM Advisors", evidence))
        with self.assertRaisesRegex(ValueError, "not normalized"):
            ProviderPublishedEmployerEvidence(
                employer_name="Other",
                descriptor_terms=("CRM",),
                evidence_url="https://api.ashbyhq.com/posting-api/job-board/other",
                opening_url="https://jobs.ashbyhq.com/other/role-123",
                extraction_method="about_heading_self_description",
            )

    def test_pool_orders_by_evidence_strength_and_deduplicates(self):
        weak = candidate(
            "https://jobs.ashbyhq.com/example/",
            "targeted_board_search",
            source_url="https://www.bing.com/search?q=example",
            query='site:jobs.ashbyhq.com "Example"',
            result_rank=2,
            provider_hint="ashby",
        )
        strong = candidate(
            "https://jobs.ashbyhq.com/example",
            "external_apply",
            provider_hint="ashby",
        )
        other = candidate(
            "https://boards.greenhouse.io/example",
            "first_party_ats_link",
            provider_hint="greenhouse",
        )

        pool = ProviderCandidatePool.build([weak, other, strong])

        self.assertEqual(pool.candidates, (strong, other))
        self.assertFalse(pool.truncated)

    def test_search_metadata_is_required_and_cannot_leak_to_direct_sources(self):
        with self.assertRaisesRegex(ValueError, "require query and result rank"):
            candidate(
                "https://jobs.lever.co/example",
                "targeted_board_search",
                source_url="https://www.bing.com/search?q=example",
            )
        with self.assertRaisesRegex(ValueError, "cannot carry search metadata"):
            candidate(
                "https://jobs.lever.co/example",
                "external_apply",
                query="Example",
                result_rank=1,
            )

    def test_pool_is_bounded_and_reports_truncation(self):
        candidates = [
            candidate(
                f"https://jobs.example.com/company-{index}",
                "guessed_path",
            )
            for index in range(MAX_PROVIDER_CANDIDATES + 3)
        ]

        pool = ProviderCandidatePool.build(candidates)

        self.assertEqual(len(pool.candidates), MAX_PROVIDER_CANDIDATES)
        self.assertTrue(pool.truncated)

    def test_candidate_rejects_private_credentials_and_sensitive_queries(self):
        rejected = (
            "https://127.0.0.1/jobs",
            "https://user:pass@jobs.example.com/jobs",
            "https://jobs.example.com/jobs?access_token=secret-value",
            "https://jobs.example.com/jobs#fragment",
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "public HTTPS"):
                    candidate(url, "external_apply")

    def test_verified_candidate_rejects_provider_hint_conflict(self):
        raw = candidate(
            "https://jobs.ashbyhq.com/example",
            "targeted_board_search",
            source_url="https://www.bing.com/search?q=example",
            query='site:jobs.ashbyhq.com "Example"',
            result_rank=1,
            provider_hint="greenhouse",
        )
        discovered = DiscoveredJobBoard(
            JobBoard("https://jobs.ashbyhq.com/example", "ashby", "example"),
            "url_evidence",
            "https://jobs.ashbyhq.com/example",
        )

        with self.assertRaisesRegex(ValueError, "conflicts"):
            VerifiedProviderCandidate(raw, discovered)

    def test_ranking_is_not_a_verification_claim(self):
        raw = candidate(
            "https://jobs.ashbyhq.com/example",
            "external_apply",
            provider_hint="ashby",
        )

        self.assertFalse(hasattr(raw, "verified"))
        self.assertFalse(hasattr(raw, "provider_identity"))


if __name__ == "__main__":
    unittest.main()
