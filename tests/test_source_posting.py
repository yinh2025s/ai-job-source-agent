import unittest

from job_source_agent.source_posting import (
    canonical_linkedin_job_url,
    explicit_closed_source_status,
    trusted_linkedin_native_posting,
)


class SourcePostingTests(unittest.TestCase):
    def test_authenticated_active_native_evidence_is_trusted(self):
        evidence = trusted_linkedin_native_posting(
            {
                "linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": "https://linkedin.com/jobs/view/123/?trk=source",
                }
            },
            expected_job_url="https://www.linkedin.com/jobs/view/123",
        )

        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.job_url, "https://www.linkedin.com/jobs/view/123")

    def test_public_listing_never_proves_native_apply(self):
        evidence = trusted_linkedin_native_posting(
            {
                "linkedin_posting": {
                    "availability": "listed",
                    "apply_mode": "unknown",
                    "evidence_source": "public_search_card",
                    "job_url": "https://www.linkedin.com/jobs/view/123",
                }
            }
        )

        self.assertIsNone(evidence)

    def test_mismatched_job_url_is_rejected(self):
        evidence = trusted_linkedin_native_posting(
            {
                "linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": "https://www.linkedin.com/jobs/view/456",
                }
            },
            expected_job_url="https://www.linkedin.com/jobs/view/123",
        )

        self.assertIsNone(evidence)

    def test_unknown_values_and_malformed_urls_are_inconclusive(self):
        self.assertIsNone(trusted_linkedin_native_posting({"linkedin_posting": {}}))
        self.assertIsNone(canonical_linkedin_job_url("https://www.linkedin.com:bad/jobs/view/123"))
        self.assertIsNone(canonical_linkedin_job_url("https://evil.example/jobs/view/123"))

    def test_closed_status_precedes_apply_mode(self):
        status = explicit_closed_source_status(
            {
                "linkedin_posting": {
                    "availability": "closed",
                    "apply_mode": "linkedin_native",
                }
            }
        )

        self.assertEqual(status, "closed")

    def test_inaccessible_status_is_not_treated_as_closed(self):
        status = explicit_closed_source_status(
            {"linkedin_posting": {"availability": "unavailable"}}
        )

        self.assertIsNone(status)


if __name__ == "__main__":
    unittest.main()
