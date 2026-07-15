import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_blind_holdout_once import BlindExecutionError, _create_ledger_once, _live_command


class RunBlindHoldoutOnceTests(unittest.TestCase):
    def test_ledger_is_fail_closed_after_first_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            _create_ledger_once(path, {"run_id": "first"})
            with self.assertRaisesRegex(BlindExecutionError, "rerun is forbidden"):
                _create_ledger_once(path, {"run_id": "second"})
            self.assertEqual(json.loads(path.read_text())["run_id"], "first")

    def test_command_is_serial_and_does_not_build_replay(self):
        config = {
            "cohort_size": 40, "fetch_timeout_seconds": 8, "fetch_retries": 1,
            "retry_base_delay_seconds": 0.25, "career_search_timeout_seconds": 7,
            "max_career_search_queries": 3, "verify_limit": 3,
            "max_career_candidates": 12, "max_career_fetches": 12,
            "max_career_transport_calls": 32, "max_ats_board_fetches": 5,
            "max_job_pages": 8, "max_job_board_attempts": 3,
            "company_time_budget_seconds": 60, "website_time_budget_seconds": 20,
            "workers": 1, "render_js": False, "skip_sitemap": False,
        }
        root = Path("/tmp/blind")
        command = _live_command(config, root / "cohort.json", {
            "results": root / "results.json", "trace": root / "trace.json",
            "summary": root / "summary.json", "snapshots": root / "snapshots",
            "checkpoints": root / "checkpoints", "batch": root / "batch",
        })
        self.assertIn("--workers", command)
        self.assertEqual(command[command.index("--workers") + 1], "1")
        self.assertNotIn("--replay-bundle-dir", command)
        self.assertIn("--no-resume", command)


if __name__ == "__main__":
    unittest.main()
