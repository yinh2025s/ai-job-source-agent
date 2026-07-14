import unittest

from job_source_agent.homepage_navigation import (
    HOMEPAGE_NAVIGATION_SCHEMA_VERSION,
    HomepageNavigationEvidence,
    evidence_from_verified_homepage,
)
from job_source_agent.web import Page


class HomepageNavigationEvidenceTests(unittest.TestCase):
    def test_extracts_only_query_free_url_semantic_career_links(self):
        evidence = evidence_from_verified_homepage(
            Page(
                url="https://company.example/",
                final_url="https://company.example/",
                html=(
                    '<a href="/about">Careers</a>'
                    '<a href="/careers">About us</a>'
                    '<a href="https://jobs.lever.co/company">Open roles</a>'
                    '<a href="/jobs?token=secret">Jobs</a>'
                    '<div data-jobs-url="https://company.example/hidden-jobs"></div>'
                ),
            ),
            homepage_url="https://company.example/",
        )

        self.assertEqual(
            evidence,
            HomepageNavigationEvidence(
                homepage_url="https://company.example/",
                candidate_urls=(
                    "https://jobs.lever.co/company",
                    "https://company.example/careers",
                ),
            ),
        )
        self.assertNotIn("Careers", str(evidence.to_checkpoint_payload()))

    def test_checkpoint_payload_round_trips_strictly(self):
        evidence = HomepageNavigationEvidence(
            homepage_url="https://company.example/",
            candidate_urls=("https://company.example/careers",),
        )

        payload = evidence.to_checkpoint_payload()

        self.assertEqual(payload["schema_version"], HOMEPAGE_NAVIGATION_SCHEMA_VERSION)
        self.assertEqual(HomepageNavigationEvidence.from_checkpoint_payload(payload), evidence)
        self.assertTrue(evidence.matches("https://company.example/"))
        self.assertEqual(evidence.raw_links()[0].origin, "verified_homepage_navigation")

    def test_rejects_unknown_fields_unsafe_urls_duplicates_and_oversized_lists(self):
        valid = {
            "schema_version": HOMEPAGE_NAVIGATION_SCHEMA_VERSION,
            "homepage_url": "https://company.example/",
            "candidate_urls": ["https://company.example/careers"],
        }
        cases = [
            {**valid, "raw_html": "<html>secret</html>"},
            {**valid, "schema_version": 999},
            {**valid, "homepage_url": "http://company.example/"},
            {**valid, "homepage_url": "https://localhost/"},
            {**valid, "homepage_url": "https://127.0.0.1/"},
            {**valid, "homepage_url": "https://user@company.example/"},
            {**valid, "candidate_urls": ["https://company.example/jobs?q=engineer"]},
            {**valid, "candidate_urls": ["https://company.example/<html>"]},
            {
                **valid,
                "candidate_urls": [
                    "https://company.example/careers",
                    "https://company.example/careers",
                ],
            },
            {
                **valid,
                "candidate_urls": [
                    f"https://company.example/careers-{index}" for index in range(9)
                ],
            },
        ]

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    HomepageNavigationEvidence.from_checkpoint_payload(payload)

    def test_returns_none_when_verified_page_has_no_url_semantic_candidate(self):
        evidence = evidence_from_verified_homepage(
            Page(
                url="https://company.example/",
                html='<a href="/about">Careers</a>',
            ),
            homepage_url="https://company.example/",
        )

        self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
