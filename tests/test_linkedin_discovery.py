import unittest
from pathlib import Path

from job_source_agent.linkedin_discovery import (
    LinkedInJobPosting,
    linkedin_postings_to_company_inputs,
    parse_linkedin_job_cards,
)


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

    def test_public_posting_trace_is_listed_unknown_with_external_url(self):
        company = linkedin_postings_to_company_inputs(
            [self._posting(external_apply_url="https://jobs.example.com/openings/123")]
        )[0]

        self.assertEqual(company.external_apply_url, "https://jobs.example.com/openings/123")
        self.assertEqual(
            company.source_trace["linkedin_posting"],
            {
                "availability": "listed",
                "apply_mode": "unknown",
                "evidence_source": "public_search_card",
                "job_url": "https://www.linkedin.com/jobs/view/ai-engineer-123",
            },
        )

    def test_empty_external_url_does_not_infer_native_apply(self):
        company = linkedin_postings_to_company_inputs([self._posting(external_apply_url="")])[0]

        self.assertIsNone(company.external_apply_url)
        self.assertEqual(company.source_trace["linkedin_posting"]["apply_mode"], "unknown")
        self.assertNotEqual(company.source_trace["linkedin_posting"]["apply_mode"], "native")

    def test_public_posting_trace_preserves_unrelated_source_trace_keys(self):
        posting = self._posting()
        posting.source_trace = {
            "request": {"page": 2},
            "linkedin_posting": {"legacy_status": "visible"},
        }

        company = linkedin_postings_to_company_inputs([posting])[0]

        self.assertEqual(company.source_trace["request"], {"page": 2})
        self.assertEqual(
            company.source_trace["linkedin_posting"],
            {
                "availability": "listed",
                "apply_mode": "unknown",
                "evidence_source": "public_search_card",
                "job_url": "https://www.linkedin.com/jobs/view/ai-engineer-123",
            },
        )
        self.assertEqual(posting.source_trace["linkedin_posting"], {"legacy_status": "visible"})

    @staticmethod
    def _posting(external_apply_url: str = "") -> LinkedInJobPosting:
        return LinkedInJobPosting(
            job_id="123",
            job_title="AI Engineer",
            company_name="Example AI",
            linkedin_job_url="https://www.linkedin.com/jobs/view/ai-engineer-123",
            linkedin_company_url="https://www.linkedin.com/company/example-ai",
            location="New York, NY",
            external_apply_url=external_apply_url,
        )


if __name__ == "__main__":
    unittest.main()
