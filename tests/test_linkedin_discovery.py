import unittest
from pathlib import Path
from unittest.mock import Mock

from job_source_agent.linkedin_discovery import (
    LinkedInJobPosting,
    LinkedInJobsDiscoverer,
    LinkedInSearchQuery,
    enrich_public_external_apply_urls,
    linkedin_postings_to_company_inputs,
    parse_linkedin_job_cards,
)
from job_source_agent.web import Page


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

    def test_opt_in_conversion_preserves_multiple_distinct_jobs_per_company(self):
        second = self._posting()
        second.job_id = "456"
        second.job_title = "ML Engineer"
        second.linkedin_job_url = "https://www.linkedin.com/jobs/view/ml-engineer-456"

        default = linkedin_postings_to_company_inputs([self._posting(), second])
        posting_cohort = linkedin_postings_to_company_inputs(
            [self._posting(), second, second],
            preserve_job_postings=True,
        )

        self.assertEqual(len(default), 1)
        self.assertEqual([item.job_title for item in posting_cohort], ["AI Engineer", "ML Engineer"])

    def test_collect_benchmark_cohort_is_distinct_by_posting_not_company(self):
        discoverer = LinkedInJobsDiscoverer(Mock())
        first = self._posting()
        second = self._posting()
        second.job_id = "456"
        second.job_title = "ML Engineer"
        second.linkedin_job_url = "https://www.linkedin.com/jobs/view/ml-engineer-456"
        discoverer.search = Mock(side_effect=[[first], [first, second]])

        cohort = discoverer.collect_benchmark_cohort(
            [LinkedInSearchQuery("AI"), LinkedInSearchQuery("ML")],
            cohort_size=100,
        )

        self.assertEqual([posting.job_id for posting in cohort], ["123", "456"])
        self.assertEqual([posting.company_name for posting in cohort], ["Example AI", "Example AI"])
        self.assertEqual(cohort[1].source_trace["benchmark_collection"]["cohort_ordinal"], 1)
        self.assertEqual(cohort[1].source_trace["benchmark_collection"]["query_index"], 1)

    def test_collect_benchmark_cohort_defaults_to_100_postings(self):
        postings = []
        for index in range(101):
            posting = self._posting()
            posting.job_id = str(index)
            posting.linkedin_job_url = f"https://www.linkedin.com/jobs/view/role-{index}"
            postings.append(posting)
        discoverer = LinkedInJobsDiscoverer(Mock())
        discoverer.search = Mock(return_value=postings)

        cohort = discoverer.collect_benchmark_cohort([LinkedInSearchQuery("AI")])

        self.assertEqual(len(cohort), 100)
        self.assertEqual(len({posting.job_id for posting in cohort}), 100)

    def test_external_apply_enrichment_is_bounded_and_classifies_unavailable(self):
        fetcher = Mock()
        fetcher.fetch.return_value = Page(
            url="https://www.linkedin.com/jobs/view/ai-engineer-123",
            html='<a href="https://jobs.example.com/openings/123">Apply now</a>',
        )
        second = self._posting()
        second.job_id = "456"
        second.linkedin_job_url = "https://www.linkedin.com/jobs/view/ml-engineer-456"

        enriched = enrich_public_external_apply_urls(
            [self._posting(), second],
            fetcher,
            max_detail_fetches=1,
        )

        self.assertEqual(enriched[0].external_apply_url, "https://jobs.example.com/openings/123")
        self.assertEqual(enriched[0].source_trace["linkedin_job_detail"]["status"], "found")
        self.assertEqual(enriched[1].external_apply_url, "")
        self.assertEqual(enriched[1].source_trace["linkedin_job_detail"]["status"], "not_attempted")
        fetcher.fetch.assert_called_once()

    def test_external_apply_enrichment_marks_missing_link_unavailable(self):
        fetcher = Mock()
        fetcher.fetch.return_value = Page(
            url="https://www.linkedin.com/jobs/view/ai-engineer-123",
            html="<main>No public apply link</main>",
        )

        enriched = enrich_public_external_apply_urls([self._posting()], fetcher)

        self.assertEqual(enriched[0].source_trace["linkedin_job_detail"]["status"], "unavailable")
        self.assertEqual(
            enriched[0].source_trace["linkedin_job_detail"]["reason"],
            "no_visible_external_apply_link",
        )

    def test_external_apply_enrichment_does_not_fetch_unsafe_job_url(self):
        fetcher = Mock()
        posting = self._posting(external_apply_url="https://127.0.0.1/private")
        posting.linkedin_job_url = "https://jobs.internal/private"

        enriched = enrich_public_external_apply_urls([posting], fetcher)

        self.assertEqual(enriched[0].external_apply_url, "")
        self.assertEqual(enriched[0].source_trace["linkedin_job_detail"]["reason"], "unsafe_job_url")
        fetcher.fetch.assert_not_called()

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
