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
        self.assertEqual(response["scan_version"], "2")
        self.assertEqual(response["state"], "ready")
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

    def test_external_apply_rejects_unsafe_urls_and_description_links(self):
        record = self._collect("unsafe_external")["records"][0]

        self.assertEqual(record["external_apply_url"], None)
        self.assertEqual(record["source_trace"]["linkedin_posting"]["apply_mode"], "unknown")

    def test_canonical_identity_rejects_forged_host_and_http_scheme(self):
        response = self._collect("forged_identity")

        self.assertEqual(response["records"], [])
        self.assertEqual(response["state"], "not_ready")

    def test_current_job_id_selects_matching_detail_root_not_competing_card(self):
        records = self._collect("selected_detail")["records"]

        self.assertEqual(records[0]["linkedin_job_url"], "https://www.linkedin.com/jobs/view/300")
        self.assertEqual(records[0]["company_name"], "Selected Systems")
        self.assertEqual(records[0]["job_title"], "Selected Detail")
        self.assertEqual(
            records[0]["source_trace"]["dom"]["identity_source"],
            "selected_detail_root",
        )
        self.assertEqual(records[1]["linkedin_job_url"], "https://www.linkedin.com/jobs/view/301")
        self.assertEqual(records[1]["company_name"], "Competing Systems")

    def test_obfuscated_search_ui_uses_selected_semantic_detail_and_unwraps_apply(self):
        record = self._collect("semantic_search_detail")["records"][0]

        self.assertEqual(record["linkedin_job_url"], "https://www.linkedin.com/jobs/view/4420695497")
        self.assertEqual(record["company_name"], "Microsoft")
        self.assertEqual(record["job_title"], "Software Engineer - CTJ - Poly")
        self.assertEqual(record["job_location"], "Reston, VA")
        self.assertEqual(
            record["external_apply_url"],
            "https://apply.careers.microsoft.com/careers/job/1970393556824773?utm_source=linkedin",
        )
        self.assertEqual(
            record["source_trace"]["dom"],
            {
                "scope": "authenticated_detail_dom",
                "root_selector": "selected_job_semantic_detail",
                "identity_source": "selected_detail_semantic_link",
            },
        )

    def test_detail_root_selector_priority_and_safe_dom_provenance(self):
        record = self._collect("selector_priority")["records"][0]

        self.assertEqual(record["company_name"], "Priority First")
        self.assertEqual(
            record["source_trace"]["dom"],
            {
                "scope": "authenticated_detail_dom",
                "root_selector": ".jobs-search__job-details--container",
                "identity_source": "selected_detail_root",
            },
        )
        self.assertNotIn("html", record["source_trace"]["dom"])
        self.assertNotIn("cookies", record["source_trace"]["dom"])
        self.assertNotIn("page_url", record["source_trace"]["dom"])

    def test_readiness_is_not_ready_only_for_empty_linkedin_jobs_route(self):
        self.assertEqual(self._collect("empty_jobs")["state"], "not_ready")
        self.assertEqual(self._collect("empty_non_jobs")["state"], "ready")

    def test_page_scan_collects_cards_deduplicates_urls_and_skips_footer_controls(self):
        response = self._collect("page_success_dedupe")

        self.assertTrue(response["ok"])
        self.assertEqual(response["scan_version"], "3")
        self.assertEqual(response["state"], "ready")
        self.assertEqual(response["candidate_count"], 3)
        self.assertEqual(response["scanned_count"], 3)
        self.assertEqual(response["failure_count"], 0)
        self.assertEqual(response["progress_count"], 3)
        self.assertEqual(
            [record["linkedin_job_url"] for record in response["records"]],
            [
                "https://www.linkedin.com/jobs/view/101",
                "https://www.linkedin.com/jobs/view/102",
            ],
        )
        self.assertEqual(response["records"][0]["job_title"], "Role 101")
        self.assertEqual(response["records"][0]["company_name"], "Company 101")

    def test_page_scan_navigation_timeout_returns_partial(self):
        response = self._collect("page_timeout")

        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], "partial")
        self.assertEqual(response["scanned_count"], 2)
        self.assertEqual(response["failure_count"], 1)
        self.assertEqual(
            [record["linkedin_job_url"] for record in response["records"]],
            ["https://www.linkedin.com/jobs/view/201"],
        )

    def test_page_scan_cancellation_stops_after_current_card(self):
        response = self._collect("page_cancel")

        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], "cancelled")
        self.assertEqual(response["scanned_count"], 1)
        self.assertEqual(response["progress_count"], 1)
        self.assertEqual(response["cancel_response"], {"ok": True, "cancelled": True})
        self.assertEqual(response["page_url"], "https://www.linkedin.com/jobs/search/?currentJobId=302")

    def test_page_scan_accepts_already_selected_first_card_without_timeout(self):
        response = self._collect("page_selected_first")

        self.assertEqual(response["state"], "ready")
        self.assertEqual(response["failure_count"], 0)
        self.assertEqual(response["records"][0]["linkedin_job_url"], "https://www.linkedin.com/jobs/view/601")

    def test_page_scan_limits_candidates_to_thirty_cards(self):
        response = self._collect("page_max_30")

        self.assertEqual(response["state"], "ready")
        self.assertEqual(response["candidate_count"], 30)
        self.assertEqual(response["scanned_count"], 30)
        self.assertEqual(len(response["records"]), 30)

    def test_page_scan_restores_original_selected_job(self):
        response = self._collect("page_restore")

        self.assertEqual(response["state"], "ready")
        self.assertEqual(response["page_url"], "https://www.linkedin.com/jobs/search/?currentJobId=502")

    def test_page_scan_waits_for_each_jobs_delayed_external_apply(self):
        response = self._collect("page_delayed_external")

        self.assertEqual(response["state"], "ready")
        self.assertEqual(
            [record["external_apply_url"] for record in response["records"]],
            [
                "https://careers.example/jobs/701",
                "https://careers.example/jobs/702",
            ],
        )
        self.assertTrue(all(
            record["source_trace"]["linkedin_posting"]["apply_mode"] == "external"
            for record in response["records"]
        ))

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
