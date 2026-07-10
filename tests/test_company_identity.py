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


if __name__ == "__main__":
    unittest.main()
