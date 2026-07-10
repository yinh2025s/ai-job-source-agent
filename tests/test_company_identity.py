import unittest

from job_source_agent.company_identity import CompanyIdentityResolver


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
        }

        for company_name, career_root in cases.items():
            with self.subTest(company_name=company_name):
                identity, _trace = CompanyIdentityResolver().resolve(company_name)
                self.assertIsNotNone(identity)
                self.assertEqual(identity.career_root_url, career_root)

    def test_meta_rule_does_not_match_metals(self):
        identity, _trace = CompanyIdentityResolver().resolve("NOX METALS")

        self.assertIsNone(identity)


if __name__ == "__main__":
    unittest.main()
