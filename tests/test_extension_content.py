import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTENT_SCRIPT = ROOT / "extension" / "content.js"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "content_harness.js"


class ExtensionContentTests(unittest.TestCase):
    def test_hidden_cards_are_ignored_without_viewport_filtering(self):
        response = self._collect("hidden_cards")

        self.assertTrue(response["ok"])
        self.assertEqual(len(response["records"]), 1)
        self.assertEqual(response["records"][0]["company_name"], "Visible Offscreen")
        self.assertEqual(
            response["records"][0]["linkedin_job_url"],
            "https://www.linkedin.com/jobs/view/106",
        )

    def test_current_card_selectors_skip_hidden_matches(self):
        record = self._collect("selector_fallback")["records"][0]

        self.assertEqual(record["job_title"], "Visible Platform Engineer")
        self.assertEqual(record["company_name"], "Visible Systems")
        self.assertEqual(record["job_location"], "Worldwide")
        self.assertEqual(record["linkedin_job_url"], "https://www.linkedin.com/jobs/view/202")
        self.assertEqual(
            record["linkedin_company_url"],
            "https://www.linkedin.com/company/visible-systems",
        )

    def test_detail_and_apply_selectors_skip_hidden_elements_and_ancestors(self):
        record = self._collect("visible_detail")["records"][0]

        self.assertEqual(record["company_name"], "Detail Systems")
        self.assertEqual(record["job_title"], "Staff AI Engineer")
        self.assertEqual(record["job_location"], "Shanghai, China")
        self.assertEqual(record["external_apply_url"], "https://careers.detail.example/jobs/777")
        self.assertEqual(
            record["linkedin_company_url"],
            "https://www.linkedin.com/company/detail-systems",
        )

    def test_visible_enabled_native_apply_emits_active_native_evidence(self):
        record = self._collect("evidence_native")["records"][0]

        self.assertEqual(record["external_apply_url"], None)
        self.assertEqual(
            record["source_trace"]["linkedin_posting"],
            {
                "availability": "active",
                "apply_mode": "linkedin_native",
                "evidence_source": "authenticated_detail_dom",
                "job_url": "https://www.linkedin.com/jobs/view/808",
            },
        )

    def test_visible_external_apply_emits_active_external_evidence(self):
        record = self._collect("evidence_external")["records"][0]

        self.assertEqual(
            record["external_apply_url"],
            "https://careers.evidence.example/jobs/808",
        )
        self.assertEqual(record["source_trace"]["linkedin_posting"]["availability"], "active")
        self.assertEqual(record["source_trace"]["linkedin_posting"]["apply_mode"], "external")

    def test_explicit_closed_banner_emits_closed_evidence(self):
        posting = self._collect("evidence_closed")["records"][0]["source_trace"][
            "linkedin_posting"
        ]

        self.assertEqual(posting["availability"], "closed")
        self.assertEqual(posting["apply_mode"], "unknown")

    def test_missing_apply_controls_do_not_infer_native_apply(self):
        posting = self._collect("evidence_missing")["records"][0]["source_trace"][
            "linkedin_posting"
        ]

        self.assertEqual(posting["availability"], "unknown")
        self.assertEqual(posting["apply_mode"], "unknown")

    def test_hidden_and_disabled_apply_controls_do_not_infer_native_apply(self):
        record = self._collect("evidence_hidden_disabled")["records"][0]
        posting = record["source_trace"]["linkedin_posting"]

        self.assertEqual(record["external_apply_url"], None)
        self.assertEqual(posting["availability"], "unknown")
        self.assertEqual(posting["apply_mode"], "unknown")

    def _collect(self, scenario: str) -> dict:
        completed = subprocess.run(
            ["node", str(HARNESS), str(CONTENT_SCRIPT), scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
