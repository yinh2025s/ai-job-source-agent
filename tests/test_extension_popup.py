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
            "stale_output_reset",
            "scan_not_ready_retry",
            "stale_run_clear",
            "transient_polling_retry",
            "malformed_response",
            "clickable_safe_links",
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
