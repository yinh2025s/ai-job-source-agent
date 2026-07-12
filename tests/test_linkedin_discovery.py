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
        self.assertEqual(postings[0].linkedin_company_url, "https://www.linkedin.com/company/example-ai")

    def test_rejects_external_job_and_company_links(self):
        html = """
        <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:99">
          <a class="base-card__full-link" href="https://tracking.example/jobs/view/99"></a>
          <h3 class="base-search-card__title">AI Engineer</h3>
          <a class="hidden-nested-link" href="https://media.licdn.com/company/example">Example AI</a>
        </div>
        """

        self.assertEqual(parse_linkedin_job_cards(html), [])

    def test_strips_tracking_query_from_linkedin_source_urls(self):
        html = """
        <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:99">
          <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/ai-engineer-99?trackingId=abc"></a>
          <h3 class="base-search-card__title">AI Engineer</h3>
          <a class="hidden-nested-link" href="https://ky.linkedin.com/company/example-ai?trk=jobs">Example AI</a>
        </div>
        """

        posting = parse_linkedin_job_cards(html)[0]
        self.assertEqual(posting.linkedin_job_url, "https://www.linkedin.com/jobs/view/ai-engineer-99")
        self.assertEqual(posting.linkedin_company_url, "https://www.linkedin.com/company/example-ai")


if __name__ == "__main__":
    unittest.main()
