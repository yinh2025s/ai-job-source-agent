import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.cli import main


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_offline_cli_uses_pipeline_application_and_writes_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            trace = Path(directory) / "trace.json"
            checkpoints = Path(directory) / "checkpoints"

            main(
                [
                    "--input",
                    str(ROOT / "samples" / "linkedin_jobs.json"),
                    "--fixtures-dir",
                    str(ROOT / "samples" / "sites"),
                    "--offline",
                    "--checkpoint-dir",
                    str(checkpoints),
                    "--output",
                    str(output),
                    "--trace-output",
                    str(trace),
                ]
            )

            results = json.loads(output.read_text(encoding="utf-8"))
            traces = json.loads(trace.read_text(encoding="utf-8"))

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["status"] == "success" for result in results))
        self.assertTrue(all(len(result["stages"]) == 7 for result in results))
        self.assertTrue(all("stages" in record["trace"] for record in traces))

    def test_resume_and_rerun_require_checkpoint_directory(self):
        common = [
            "--input",
            str(ROOT / "samples" / "linkedin_jobs.json"),
            "--offline",
        ]
        with self.assertRaisesRegex(SystemExit, "require --checkpoint-dir"):
            main(common + ["--resume-from-stage", "career_discovery"])
        with self.assertRaisesRegex(SystemExit, "require --checkpoint-dir"):
            main(common + ["--rerun-stage", "career_discovery"])


if __name__ == "__main__":
    unittest.main()
