import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_SCRIPT = ROOT / "extension" / "background.js"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "background_harness.js"


class ExtensionBackgroundTests(unittest.TestCase):
    def test_background_capture_behaviors(self):
        scenarios = [
            "workday_capture",
            "safety_unwrap",
            "unrelated_ignored",
            "wrong_source_rejected",
            "unsafe_urls_rejected",
            "expiry",
            "trigger_failure_clears_pending",
            "same_tab_capture",
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                completed = subprocess.run(
                    ["node", str(HARNESS), str(BACKGROUND_SCRIPT), scenario],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(json.loads(completed.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
