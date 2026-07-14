import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_source_agent.cli import main
from job_source_agent.composition import build_application


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

    def test_cli_passes_explicit_linkedin_evidence_cache_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            trace = Path(directory) / "trace.json"
            evidence_cache = Path(directory) / "shared-evidence.json"
            with patch(
                "job_source_agent.cli.build_application",
                wraps=build_application,
            ) as build:
                main(
                    [
                        "--input",
                        str(ROOT / "samples" / "linkedin_jobs.json"),
                        "--fixtures-dir",
                        str(ROOT / "samples" / "sites"),
                        "--offline",
                        "--linkedin-evidence-cache",
                        str(evidence_cache),
                        "--output",
                        str(output),
                        "--trace-output",
                        str(trace),
                    ]
                )

        self.assertEqual(
            build.call_args.kwargs["linkedin_evidence_cache_path"],
            str(evidence_cache),
        )

    def test_rerun_checkpoint_prefix_error_exits_without_writing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            trace = Path(directory) / "trace.json"
            checkpoints = Path(directory) / "checkpoints"

            with self.assertRaisesRegex(
                SystemExit,
                r"Cannot rerun from career_discovery:.*linkedin_discovery",
            ):
                main(
                    [
                        "--input",
                        str(ROOT / "samples" / "linkedin_jobs.json"),
                        "--fixtures-dir",
                        str(ROOT / "samples" / "sites"),
                        "--offline",
                        "--checkpoint-dir",
                        str(checkpoints),
                        "--rerun-stage",
                        "career_discovery",
                        "--output",
                        str(output),
                        "--trace-output",
                        str(trace),
                    ]
                )

            self.assertFalse(output.exists())
            self.assertFalse(trace.exists())


if __name__ == "__main__":
    unittest.main()
