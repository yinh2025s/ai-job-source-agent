import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POPUP_SCRIPT = ROOT / "extension" / "popup.js"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "popup_harness.js"


class ExtensionPopupTests(unittest.TestCase):
    def test_popup_behaviors(self):
        scenarios = [
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
            "external_without_target",
            "stale_run_clear",
            "transient_polling_retry",
            "restored_running_visible",
            "malformed_response",
            "clickable_safe_links",
            "scanned_apply_fallback",
            "button_recovery",
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
