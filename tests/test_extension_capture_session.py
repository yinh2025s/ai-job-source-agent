import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SESSION_SCRIPT = ROOT / "extension" / "capture_session.js"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "capture_session_harness.js"


class ExtensionCaptureSessionTests(unittest.TestCase):
    def test_capture_session_behaviors(self):
        scenarios = [
            "lifecycle",
            "cancellation_expiry_and_recovery",
            "duplicate_and_identity_fail_closed",
            "arm_evidence_and_shape",
            "rejected_urls_are_not_returned",
            "late_replay_and_validation",
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                completed = subprocess.run(
                    ["node", str(HARNESS), str(CAPTURE_SESSION_SCRIPT), scenario],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(json.loads(completed.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
