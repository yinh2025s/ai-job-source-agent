import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.checkpoint import checkpoint_metadata
from scripts.validate_replay_input import main, validate_replay_records


def _record():
    record = {
        "company_name": "Example Robotics",
        "company_website_url": "https://example.com",
        "job_title": "AI Engineer",
        "source": "replay_input",
    }
    record["checkpoint"] = checkpoint_metadata(record)
    return record


class ValidateReplayInputTests(unittest.TestCase):
    def test_validate_replay_records_accepts_matching_metadata(self):
        summary = validate_replay_records([_record()])

        self.assertEqual(summary["compatible"], 1)
        self.assertEqual(summary["incompatible"], 0)

    def test_validate_replay_records_rejects_changed_input_fingerprint(self):
        record = _record()
        record["job_title"] = "Product Manager"

        summary = validate_replay_records([record])

        self.assertEqual(summary["compatible"], 0)
        self.assertEqual(summary["checks"][0]["failures"][0]["field"], "input_fingerprint")

    def test_cli_exits_nonzero_for_incompatible_replay_file(self):
        record = _record()
        record["checkpoint"]["adapter_version"] = "old"
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "replay.json"
            summary_path = Path(directory) / "summary.json"
            input_path.write_text(json.dumps([record]), encoding="utf-8")

            import sys

            old_argv = sys.argv
            try:
                sys.argv = [
                    "validate_replay_input.py",
                    "--input",
                    str(input_path),
                    "--summary-output",
                    str(summary_path),
                ]
                with self.assertRaises(SystemExit):
                    main()
            finally:
                sys.argv = old_argv

            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["incompatible"], 1)


if __name__ == "__main__":
    unittest.main()
