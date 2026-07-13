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
