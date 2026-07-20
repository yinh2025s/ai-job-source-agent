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


class _MappingFetcher(Fetcher):
    def __init__(self, pages=None, errors=None):
        super().__init__(offline=True)
        self.pages = pages or {}
        self.errors = errors or {}
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        if url in self.errors:
            raise FetchError(self.errors[url])
        return Page(url=url, html=self.pages.get(url, ""))


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
    WEBSITE_URL = "https://example.com/"
    JOB_URL = "https://www.linkedin.com/jobs/view/software-engineer-789"

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

    def test_strong_verified_website_evidence_triggers_job_detail_probe(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: (
                    "<main>We are a global executive search firm serving leaders.</main>"
                ),
                self.JOB_URL: _job_page(
                    "We are recruiting for our client, a healthcare company.",
                    "Acme Search",
                ),
            }
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme Search",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "agency_unresolved")
        self.assertTrue(
            any("bounded probe triggered" in reason for reason in result.reasons)
        )
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL, self.JOB_URL])

    def test_hiring_publisher_and_talent_solutions_site_close_unresolved_client(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: "<main><h1>Smart Talent Solutions</h1></main>",
            },
            errors={self.JOB_URL: "HTTP Error 999: Request denied"},
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Great Value Hiring",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "agency_unresolved")
        self.assertTrue(
            any("talent intermediary" in reason for reason in result.reasons)
        )
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL, self.JOB_URL])

    def test_public_website_metadata_can_trigger_job_detail_probe(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: (
                    "<head>"
                    "<title>Staffing Solutions &amp; Executive Search</title>"
                    '<meta name="description" content="A provider of staffing solutions">'
                    "</head><body><div id=\"app\"></div></body>"
                ),
                self.JOB_URL: _job_page(
                    "We are recruiting for our client, a healthcare company.",
                    "Acme Search",
                ),
            }
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme Search",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "agency_unresolved")
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL, self.JOB_URL])

    def test_untrusted_metadata_and_scripts_do_not_trigger_probe(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: (
                    '<meta charset="utf-8">'
                    '<meta name="keywords" content="staffing agency firm">'
                    '<script>const description = "executive search firm";</script>'
                    "<main>We build payment software.</main>"
                )
            }
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "not_applicable")
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL])

    def test_ordinary_verified_website_does_not_fetch_job_detail(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: (
                    "<main>We build payment software and hire our own product team.</main>"
                )
            }
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "not_applicable")
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL])

    def test_verified_website_failure_does_not_fetch_job_detail(self):
        fetcher = _MappingFetcher(errors={self.WEBSITE_URL: "temporary timeout"})

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "unavailable")
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL])

    def test_invalid_job_url_does_not_fetch_verified_website(self):
        fetcher = _MappingFetcher(errors={self.WEBSITE_URL: "must not fetch"})

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme",
            "https://example.com/jobs/software-engineer-789",
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "unavailable")
        self.assertEqual(fetcher.calls, [])

    def test_intermediary_internal_role_remains_publisher_unconfirmed(self):
        fetcher = _MappingFetcher(
            pages={
                self.WEBSITE_URL: (
                    "<main>Talent solutions for our clients across technology.</main>"
                ),
                self.JOB_URL: _job_page(
                    "Join our finance team and improve our internal operations.",
                    "Acme Search",
                ),
            }
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme Search",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "publisher_unconfirmed")
        self.assertEqual(fetcher.calls, [self.WEBSITE_URL, self.JOB_URL])

    def test_name_marker_keeps_direct_job_detail_probe_behavior(self):
        fetcher = _MappingFetcher(
            pages={
                self.JOB_URL: _job_page(
                    "Join our internal recruiting operations team.",
                    "Acme Staffing",
                )
            },
            errors={self.WEBSITE_URL: "website must not be fetched"},
        )

        result = LinkedInPostingIdentityProbe(fetcher).probe(
            "Acme Staffing",
            self.JOB_URL,
            website_url=self.WEBSITE_URL,
        )

        self.assertEqual(result.classification, "publisher_unconfirmed")
        self.assertEqual(fetcher.calls, [self.JOB_URL])

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
