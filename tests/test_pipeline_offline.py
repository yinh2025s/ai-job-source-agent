import unittest
from pathlib import Path

from job_source_agent.linkedin import load_company_inputs
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class OfflinePipelineTests(unittest.TestCase):
    def test_sample_jobs_discover_successfully(self):
        companies = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company) for company in companies]

        self.assertEqual([result.status for result in results], ["success", "success"])
        self.assertEqual(results[0].career_page_url, "https://jobs.lever.co/aurora-data")
        self.assertIn("d9d64766", results[0].open_position_url)
        self.assertEqual(results[1].career_page_url, "https://nimbus-robotics.example/careers")
        self.assertIn("5012345001", results[1].open_position_url)


if __name__ == "__main__":
    unittest.main()
