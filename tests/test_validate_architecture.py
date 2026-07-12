import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_architecture import validate_architecture


ROOT = Path(__file__).resolve().parents[1]


class ValidateArchitectureTests(unittest.TestCase):
    def test_current_native_adapters_pass_extension_contracts(self):
        report = validate_architecture()

        self.assertTrue(report["valid"])
        self.assertIn("greenhouse", report["native_adapters"])
        self.assertEqual(report["issues"], [])

    def test_cli_writes_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "architecture.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_architecture.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0)
        self.assertTrue(report["valid"])


if __name__ == "__main__":
    unittest.main()

