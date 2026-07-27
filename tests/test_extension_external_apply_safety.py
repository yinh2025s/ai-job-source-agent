import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "extension" / "external_apply_safety.js"
HARNESS = ROOT / "tests" / "fixtures" / "extension" / "external_apply_safety_harness.js"


class ExtensionExternalApplySafetyTests(unittest.TestCase):
    def test_sanitizes_public_targets_and_unwraps_linkedin_safety_redirect(self):
        redirect = (
            "https://www.linkedin.com/safety/go/?url="
            "https%3A%2F%2Fjobs.example.com%2Fopening%3Futm_source%3Dlinkedin"
        )

        self.assertEqual(
            self._sanitize(["https://jobs.example.com/opening?source=linkedin", redirect]),
            [
                "https://jobs.example.com/opening?source=linkedin",
                "https://jobs.example.com/opening",
            ],
        )

    def test_rejects_non_https_private_linkedin_lookalike_and_sensitive_targets(self):
        values = [
            "http://jobs.example.com/opening",
            "https://127.0.0.1/opening",
            "https://www.linkedin.com/jobs/view/123",
            "https://linkedin-careers.example/opening",
            "https://jobs.example.com/opening#apply",
            "https://jobs.example.com/opening?auth_token=secret",
            "https://jobs.example.com/opening?state=secret",
            "https://192.0.2.1/opening",
            "https://user:pass@jobs.example.com/opening",
            "not a url",
        ]

        self.assertEqual(self._sanitize(values), [""] * len(values))

    def _sanitize(self, values: list[str]) -> list[str]:
        completed = subprocess.run(
            ["node", str(HARNESS), str(SCRIPT), json.dumps(values)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
