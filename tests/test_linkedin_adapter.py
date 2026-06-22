import unittest
from pathlib import Path

from job_source_agent.linkedin import load_company_inputs


ROOT = Path(__file__).resolve().parents[1]


class LinkedInAdapterTests(unittest.TestCase):
    def test_saved_html_can_seed_company_record(self):
        records = load_company_inputs(ROOT / "samples" / "linkedin_html_input.json")

        self.assertEqual(records[0].company_name, "Example Robotics")
        self.assertEqual(records[0].company_website_url, "https://example-robotics.test")


if __name__ == "__main__":
    unittest.main()
