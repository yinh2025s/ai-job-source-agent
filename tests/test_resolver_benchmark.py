import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from resolver_benchmark import run_benchmark


class ResolverBenchmarkTests(unittest.TestCase):
    def test_fixed_benchmark_resolves_exact_domains_and_rejects_parent_domains(self):
        report = run_benchmark(
            ROOT / "samples" / "resolver_benchmark_companies.json",
            ROOT / "samples" / "resolver_benchmark_expectations.json",
            ROOT / "samples" / "resolver_sites",
        )

        self.assertEqual(report["total"], 6)
        self.assertEqual(report["passed"], 6, json.dumps(report, indent=2))
        by_name = {result["company_name"]: result for result in report["results"]}
        self.assertEqual(by_name["Google DeepMind"]["actual_official_domain"], "deepmind.google")
        self.assertTrue(by_name["Google DeepMind"]["rejection_checks"]["google.com"])
        self.assertIsNone(by_name["Northstar Labs"]["actual_official_domain"])
        self.assertTrue(by_name["Northstar Labs"]["rejection_checks"]["northstar.com"])
        self.assertTrue(all(not result["company_website_url"] for result in report["results"]))

    def test_cli_is_offline_and_writes_a_deterministic_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"
            command = [sys.executable, str(ROOT / "scripts" / "resolver_benchmark.py")]
            subprocess.run(command + ["--output", str(first)], cwd=ROOT, check=True, capture_output=True, text=True)
            subprocess.run(command + ["--output", str(second)], cwd=ROOT, check=True, capture_output=True, text=True)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["passed"], 6)


if __name__ == "__main__":
    unittest.main()
