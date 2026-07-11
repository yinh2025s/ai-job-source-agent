import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from job_source_agent.linkedin import load_company_inputs


ROOT = Path(__file__).resolve().parents[1]


class LinkedInAdapterTests(unittest.TestCase):
    def test_saved_html_can_seed_company_record(self):
        records = load_company_inputs(ROOT / "samples" / "linkedin_html_input.json")

        self.assertEqual(records[0].company_name, "Example Robotics")
        self.assertEqual(records[0].company_website_url, "https://example-robotics.test")

    def test_result_record_can_be_reused_as_input(self):
        result_record = {
            "company_name": "Example Robotics",
            "company_website_url": "https://example-robotics.test",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "linkedin_job_title": "AI Engineer",
            "linkedin_job_location": "New York, NY",
            "career_page_url": "https://example-robotics.test/careers",
            "status": "success",
            "error": None,
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "previous-results.json"
            path.write_text(json.dumps([result_record]), encoding="utf-8")

            records = load_company_inputs(path)

        self.assertEqual(records[0].job_title, "AI Engineer")
        self.assertEqual(records[0].job_location, "New York, NY")
        self.assertEqual(records[0].career_root_url, "https://example-robotics.test/careers")


if __name__ == "__main__":
    unittest.main()
