import unittest
from pathlib import Path

from job_source_agent.evaluation import summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def test_summary_tracks_funnel_and_provider_counts(self):
        results = [
            {
                "company_website_url": "https://jobs.ashbyhq.com/acme",
                "career_page_url": "https://jobs.ashbyhq.com/acme",
                "job_list_page_url": "https://jobs.ashbyhq.com/acme",
                "open_position_url": "https://jobs.ashbyhq.com/acme/abc",
                "status": "success",
                "error": None,
            },
            {
                "company_website_url": "https://example.com",
                "career_page_url": None,
                "job_list_page_url": None,
                "open_position_url": None,
                "status": "failed",
                "error": "career_page_not_found",
            },
        ]

        summary = summarize_results(results, elapsed_sec=1.2)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["with_opening"], 1)
        self.assertEqual(summary["rates"]["opening"], 0.5)
        self.assertEqual(summary["provider_counts"]["ashby"], 1)
        self.assertEqual(summary["failure_stage_counts"]["career_page"], 1)

    def test_fixed_benchmark_reaches_openings_offline(self):
        companies = load_company_inputs(ROOT / "samples" / "benchmark_companies.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company).result_record() for company in companies]
        summary = summarize_results(results)

        self.assertEqual(summary["total"], 10)
        self.assertEqual(summary["with_opening"], 10)
        self.assertEqual(summary["provider_counts"]["icims"], 2)
        self.assertEqual(summary["provider_counts"]["workday"], 1)


if __name__ == "__main__":
    unittest.main()
