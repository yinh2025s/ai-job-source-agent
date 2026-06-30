import unittest
from pathlib import Path

from job_source_agent.linkedin_discovery import parse_linkedin_job_cards


ROOT = Path(__file__).resolve().parents[1]


class LinkedInDiscoveryTests(unittest.TestCase):
    def test_parse_public_jobs_search_card(self):
        html = (ROOT / "samples" / "linkedin_jobs_search.html").read_text(encoding="utf-8")

        postings = parse_linkedin_job_cards(html)

        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].job_id, "12345")
        self.assertEqual(postings[0].job_title, "AI Engineer")
        self.assertEqual(postings[0].company_name, "Example AI")
        self.assertEqual(postings[0].location, "New York, NY")


if __name__ == "__main__":
    unittest.main()
