import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POPUP_SCRIPT = ROOT / "extension" / "popup.js"
POPUP_HTML = ROOT / "extension" / "popup.html"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "popup_harness.js"


class ExtensionPopupTests(unittest.TestCase):
    def test_popup_exposes_csv_export_command(self):
        markup = POPUP_HTML.read_text(encoding="utf-8")
        self.assertIn('id="exportCsvButton"', markup)
        self.assertIn(">Export CSV</button>", markup)

    def test_popup_behaviors(self):
        scenarios = [
            "fresh_auto_pair",
            "saved_token_no_pair",
            "stale_token_repair",
            "unavailable_bridge",
            "malformed_pair_response",
            "server_error_no_pair",
            "pairing_terminal_messages",
            "manual_fallback",
            "invalid_endpoint_no_fetch",
            "duplicate_submission",
            "submission_failure_visible",
            "executor_failure_queryable",
            "duplicate_while_polling",
            "duplicate_scan",
            "page_success_progress",
            "page_partial",
            "page_missing_details",
            "page_not_ready_no_retry",
            "page_cancellation",
            "page_watchdog",
            "stale_output_reset",
            "scan_not_ready_retry",
            "selected_partial",
            "restored_scan_visible",
            "stale_scan_not_restored",
            "external_without_target",
            "external_capture_final_url",
            "stale_run_clear",
            "transient_polling_retry",
            "restored_running_visible",
            "malformed_response",
            "clickable_safe_links",
            "scanned_apply_fallback",
            "verified_job_detail",
            "verified_details_survive_rescan",
            "csv_export",
            "button_recovery",
            "stale_content_upgrade",
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                completed = subprocess.run(
                    ["node", str(HARNESS), str(POPUP_SCRIPT), scenario],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(json.loads(completed.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
