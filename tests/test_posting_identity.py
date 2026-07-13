import json
import unittest

from job_source_agent.posting_identity import LinkedInPostingIdentityProbe
from job_source_agent.web import FetchError, Fetcher, Page


class _StaticFetcher(Fetcher):
    def __init__(self, html: str | None = None, error: str | None = None):
        super().__init__(offline=True)
        self.html = html
        self.error = error
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        if self.error:
            raise FetchError(self.error)
        return Page(url=url, html=self.html or "")


def _job_page(description: str, publisher: str) -> str:
    return (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "description": description,
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": publisher,
                },
            }
        )
        + "</script>"
    )


class LinkedInPostingIdentityProbeTests(unittest.TestCase):
    def test_extracts_repeated_alternate_employer_from_employer_contexts(self):
        description = (
            "<strong>Join the Team Modernizing Medicine</strong>"
            "At <strong>ModMed</strong>, we build healthcare software. "
            "When You Join ModMed you can grow. "
            "ModMed Benefits include health coverage. "
            "ModMed will not ask you to purchase equipment."
        )
        fetcher = _StaticFetcher(_job_page(description, "Stage 2 Capital"))
        probe = LinkedInPostingIdentityProbe(fetcher)

        result = probe.probe(
            "Stage 2 Capital",
            "https://www.linkedin.com/jobs/view/machine-learning-engineer-123",
        )

        self.assertEqual(result.classification, "alternate_employer")
        self.assertEqual(result.employer_name, "ModMed")
        self.assertGreaterEqual(result.employer_mentions, 3)
        self.assertGreaterEqual(result.employer_contexts, 2)

    def test_marks_undisclosed_agency_client_without_guessing_employer(self):
        description = (
            "We're partnering with one of the fastest-growing AI scale-ups. "
            "Aventis is working on behalf of its partner."
        )
        probe = LinkedInPostingIdentityProbe(
            _StaticFetcher(_job_page(description, "Aventis Solutions"))
        )

        result = probe.probe(
            "Aventis Solutions",
            "https://www.linkedin.com/jobs/view/ai-engineer-456",
        )

        self.assertEqual(result.classification, "agency_unresolved")
        self.assertIsNone(result.employer_name)

    def test_does_not_fetch_for_ordinary_direct_employer(self):
        fetcher = _StaticFetcher(error="must not fetch")
        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme",
            "https://www.linkedin.com/jobs/view/software-engineer-789",
        )

        self.assertEqual(result.classification, "not_applicable")
        self.assertEqual(fetcher.calls, [])

    def test_optional_probe_failure_does_not_raise(self):
        result = LinkedInPostingIdentityProbe(
            _StaticFetcher(error="temporary timeout")
        ).probe(
            "Acme Staffing",
            "https://www.linkedin.com/jobs/view/software-engineer-789",
        )

        self.assertEqual(result.classification, "unavailable")

    def test_single_technology_mention_is_not_treated_as_employer(self):
        description = (
            "At <strong>AWS</strong>, deploy the service. "
            "AWS knowledge is required and AWS certification is useful."
        )
        result = LinkedInPostingIdentityProbe(
            _StaticFetcher(_job_page(description, "Example Consulting"))
        ).probe(
            "Example Consulting",
            "https://www.linkedin.com/jobs/view/cloud-engineer-123",
        )

        self.assertEqual(result.classification, "publisher_unconfirmed")
        self.assertIsNone(result.employer_name)


if __name__ == "__main__":
    unittest.main()
