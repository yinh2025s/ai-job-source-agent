import unittest
from pathlib import Path

from job_source_agent.acquired_brand_portal import (
    AcquiredBrandPortalEvidence,
    parse_acquired_brand_portal_evidence,
)
from job_source_agent.web import Page


class AcquiredBrandPortalTests(unittest.TestCase):
    def page(self, body: str) -> Page:
        return Page(url="https://source.example/careers/", html=body)

    def callout(
        self,
        relationship: str = "Source Brand is now a Parent Corp company.",
        href: str = "https://jobs.parent.example/search",
        label: str = "Search All Jobs",
    ) -> str:
        return (
            '<section class="career-callout">'
            f"<div><p>{relationship}</p></div>"
            f'<div><a href="{href}">{label}</a></div>'
            "</section>"
        )

    def test_extracts_generic_cyberark_shaped_callout(self):
        evidence = parse_acquired_brand_portal_evidence(
            self.page(self.callout()),
            "source-brand",
        )

        self.assertEqual(
            evidence,
            AcquiredBrandPortalEvidence(
                source_brand="Source Brand",
                parent_brand="Parent Corp",
                target_url="https://jobs.parent.example/search",
                evidence_url="https://source.example/careers/",
            ),
        )

    def test_supports_acquired_by_and_deduplicates_identical_callouts(self):
        callout = self.callout("Source Brand was acquired by Parent Corp.")

        evidence = parse_acquired_brand_portal_evidence(
            self.page(callout + callout),
            "SOURCE BRAND",
        )

        self.assertEqual(evidence.parent_brand, "Parent Corp")

    def test_parses_read_only_cyberark_snapshot(self):
        snapshot = Path(
            "/private/tmp/holdout85-snapshots/sites/www.cyberark.com/careers/index.html"
        )
        if not snapshot.exists():
            self.skipTest("holdout snapshot is not available")

        evidence = parse_acquired_brand_portal_evidence(
            Page(url="https://www.cyberark.com/careers/", html=snapshot.read_text()),
            "CyberArk",
        )

        self.assertEqual(evidence.parent_brand, "Palo Alto Networks")
        self.assertEqual(evidence.target_url, "https://jobs.paloaltonetworks.com/en/")

    def test_rejects_loose_cooccurrence_and_wrong_source(self):
        cases = [
            (
                "<section><p>Source Brand is now a Parent Corp company.</p></section>"
                '<section><a href="https://jobs.parent.example/">Search All Jobs</a></section>',
                "Source Brand",
            ),
            (self.callout(), "Source"),
            (self.callout("Other Brand is now a Parent Corp company."), "Source Brand"),
        ]
        for html, source in cases:
            with self.subTest(html=html, source=source):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(self.page(html), source)
                )

    def test_rejects_hidden_ignored_and_non_relationship_content(self):
        wrappers = [
            '<div hidden>{}</div>',
            '<div aria-hidden="true">{}</div>',
            '<div style="display: none">{}</div>',
            '<script>{}</script>',
            '<style>{}</style>',
            '<template>{}</template>',
            '<noscript>{}</noscript>',
            '<div class="news-release">{}</div>',
        ]
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(
                        self.page(wrapper.format(self.callout())),
                        "Source Brand",
                    )
                )
        for statement in [
            "Source Brand partners with Parent Corp.",
            "Source Brand is powered by Parent Corp.",
            "Source Brand and Parent Corp offer opportunities.",
        ]:
            with self.subTest(statement=statement):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(
                        self.page(self.callout(statement)),
                        "Source Brand",
                    )
                )

    def test_rejects_ambiguous_parent_or_target(self):
        conflicting_parent = self.callout() + self.callout(
            "Source Brand was acquired by Different Parent.",
            "https://jobs.different.example/",
        )
        conflicting_target = self.callout() + self.callout(
            href="https://careers.parent.example/"
        )

        for html in [conflicting_parent, conflicting_target]:
            with self.subTest(html=html):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(
                        self.page(html),
                        "Source Brand",
                    )
                )

    def test_requires_explicit_search_all_jobs_anchor(self):
        for label in ["Jobs", "Search Jobs", "View All Jobs", "Search All Opportunities"]:
            with self.subTest(label=label):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(
                        self.page(self.callout(label=label)),
                        "Source Brand",
                    )
                )

    def test_url_safety_matrix(self):
        unsafe_urls = [
            "http://jobs.parent.example/",
            "https://user@jobs.parent.example/",
            "https://jobs.parent.example:444/",
            "https://jobs.parent.example/#openings",
            "https://jobs.parent.example/?token=secret",
            "https://jobs.parent.example/?redirect=https://evil.example/",
            "https://jobs.parent.example/?redirect_url=https://evil.example/",
            "https://jobs.parent.example/?return_url=https://evil.example/",
            "https://jobs.parent.example/%0aheader",
            "https://localhost/jobs",
            "https://127.0.0.1/jobs",
            "https://10.0.0.1/jobs",
        ]
        for href in unsafe_urls:
            with self.subTest(href=href):
                self.assertIsNone(
                    parse_acquired_brand_portal_evidence(
                        self.page(self.callout(href=href)),
                        "Source Brand",
                    )
                )

        evidence = parse_acquired_brand_portal_evidence(
            self.page(self.callout(href="https://jobs.parent.example:443/search?lang=en")),
            "Source Brand",
        )
        self.assertEqual(
            evidence.target_url,
            "https://jobs.parent.example:443/search?lang=en",
        )

    def test_fails_closed_when_container_or_input_exceeds_bounds(self):
        oversized_container = self.callout(
            relationship=("culture " * 200) + "Source Brand is now a Parent Corp company."
        )
        oversized_page = self.callout() + (" " * 1_000_001)

        self.assertIsNone(
            parse_acquired_brand_portal_evidence(
                self.page(oversized_container),
                "Source Brand",
            )
        )
        self.assertIsNone(
            parse_acquired_brand_portal_evidence(
                self.page(oversized_page),
                "Source Brand",
            )
        )


if __name__ == "__main__":
    unittest.main()
