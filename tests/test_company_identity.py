import unittest

from job_source_agent.company_identity import CompanyIdentityResolver
from job_source_agent.posting_identity import PostingIdentityEvidence


class _PostingProbe:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def probe(self, company_name, linkedin_job_url, website_url=None):
        self.calls.append((company_name, linkedin_job_url, website_url))
        return self.evidence


class CompanyIdentityTests(unittest.TestCase):
    def test_instagram_maps_to_meta_careers(self):
        identity, trace = CompanyIdentityResolver().resolve(
            "Instagram",
            "https://www.instagram.com/",
            "https://www.linkedin.com/company/instagram",
        )

        self.assertIsNotNone(identity)
        self.assertEqual(identity.hiring_entity_name, "Meta")
        self.assertEqual(identity.career_root_url, "https://www.metacareers.com/jobs/")
        self.assertEqual(trace["matched_rule"], "instagram")

    def test_known_brand_career_roots(self):
        cases = {
            "Notion": "https://www.notion.com/careers",
            "Netflix": "https://jobs.netflix.com",
            "Hudl": "https://www.hudl.com/jobs#jobs",
            "Snap Inc.": "https://careers.snap.com/",
            "Meta": "https://www.metacareers.com/jobs/",
            "Roku": "https://www.weareroku.com/",
            "The Home Depot": "https://careers.homedepot.com/",
            "Stripe": "https://stripe.com/jobs",
            "Nuro": "https://www.nuro.ai/careers",
            "Morgan Stanley": "https://www.morganstanley.com/careers",
            "Lemonade": "https://www.lemonade.com/careers",
            "Podium": "https://www.podium.com/careers",
            "ParetoHealth": "https://www.paretohealth.com/careers",
            "Anthropic": "https://job-boards.greenhouse.io/anthropic",
            "PostHog": "https://posthog.com/careers/jobs",
            "Ekimetrics": "https://jobs.lever.co/ekimetrics",
            "Brex": "https://www.brex.com/careers",
            "Lyft": "https://job-boards.greenhouse.io/lyft",
            "Michael Kors": "https://capri.wd1.myworkdayjobs.com/Michael_Kors",
            "Saint Laurent": (
                "https://www.kering.com/en/talent/job-offers/saint-laurent-careers/"
            ),
            "Gucci": "https://www.kering.com/en/talent/job-offers/gucci-careers/",
            "adidas": "https://careers.adidas-group.com/",
        }

        for company_name, career_root in cases.items():
            with self.subTest(company_name=company_name):
                identity, _trace = CompanyIdentityResolver().resolve(company_name)
                self.assertIsNotNone(identity)
                self.assertEqual(identity.career_root_url, career_root)

    def test_curated_parent_hiring_rules_retain_official_relationship_evidence(self):
        cases = {
            "Michael Kors": ("Capri Holdings", "official_brand_career_handoff"),
            "Saint Laurent": ("Kering", "official_parent_career_handoff"),
            "Gucci": ("Kering", "official_parent_career_handoff"),
            "adidas": ("adidas", "official_company_career_handoff"),
        }

        for company_name, (parent, method) in cases.items():
            with self.subTest(company_name=company_name):
                identity, trace = CompanyIdentityResolver().resolve(company_name)

                self.assertEqual(identity.hiring_entity_name, parent)
                expected_relationship = (
                    "same_entity" if company_name == "adidas" else "brand_parent"
                )
                self.assertEqual(identity.relationship_type, expected_relationship)
                self.assertTrue(identity.relationship_verified)
                self.assertEqual(identity.verification_method, method)
                self.assertTrue(identity.evidence_url.startswith("https://"))
                self.assertTrue(trace["selected"]["relationship"]["verified"])

    def test_ambiguous_haystack_rule_requires_the_linkedin_company_slug(self):
        resolver = CompanyIdentityResolver()

        unresolved, _trace = resolver.resolve("Haystack")
        identity, trace = resolver.resolve(
            "Haystack",
            linkedin_company_url="https://www.linkedin.com/company/wearehaystack",
        )

        self.assertIsNone(unresolved)
        self.assertEqual(trace["matched_rule"], "wearehaystack")
        self.assertEqual(identity.official_website_url, "https://www.haystackapp.io/")
        self.assertEqual(identity.relationship_type, "same_entity")

    def test_ambiguous_hadrian_rule_uses_linkedin_slug_and_official_ashby_board(self):
        resolver = CompanyIdentityResolver()

        unresolved, _trace = resolver.resolve("Hadrian")
        identity, trace = resolver.resolve(
            "Hadrian",
            linkedin_company_url=(
                "https://www.linkedin.com/company/hadrianautomation"
            ),
        )

        self.assertIsNone(unresolved)
        self.assertEqual(trace["matched_rule"], "hadrianautomation")
        self.assertEqual(identity.official_website_url, "https://www.hadrian.co/")
        self.assertEqual(
            identity.career_root_url,
            "https://jobs.ashbyhq.com/hadrian-automation",
        )

    def test_meta_rule_does_not_match_metals(self):
        identity, _trace = CompanyIdentityResolver().resolve(
            "NOX METALS",
            "https://noxmetals.com",
            "https://www.linkedin.com/company/nox-metals",
        )

        self.assertIsNone(identity)

    def test_verified_alternate_employer_uses_known_identity_rule(self):
        resolver = CompanyIdentityResolver(
            posting_probe=_PostingProbe(
                PostingIdentityEvidence(
                    "alternate_employer",
                    employer_name="ModMed",
                    employer_mentions=12,
                    employer_contexts=4,
                )
            )
        )

        identity, trace = resolver.resolve(
            "Stage 2 Capital",
            "https://stage2.capital",
            linkedin_job_url="https://www.linkedin.com/jobs/view/job-123",
        )

        self.assertIsNotNone(identity)
        self.assertEqual(identity.hiring_entity_name, "ModMed")
        self.assertEqual(
            identity.career_root_url,
            "https://modmed.wd501.myworkdayjobs.com/ModMed12",
        )
        self.assertEqual(trace["matched_rule"], "modmed")

    def test_undisclosed_agency_client_does_not_select_identity(self):
        posting_probe = _PostingProbe(PostingIdentityEvidence("agency_unresolved"))
        resolver = CompanyIdentityResolver(posting_probe=posting_probe)

        identity, trace = resolver.resolve(
            "Aventis Solutions",
            "https://aventissolutions.com",
            linkedin_job_url="https://www.linkedin.com/jobs/view/job-456",
        )

        self.assertIsNone(identity)
        self.assertEqual(
            trace["posting_identity"]["classification"],
            "agency_unresolved",
        )
        self.assertEqual(
            posting_probe.calls,
            [
                (
                    "Aventis Solutions",
                    "https://www.linkedin.com/jobs/view/job-456",
                    "https://aventissolutions.com",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
