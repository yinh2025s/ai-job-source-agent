import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.linkedin import load_company_inputs
from scripts.export_replay_input import export_replay_records, main


def _stage(stage, status, reason_code=None):
    return {"stage": stage, "status": status, "reason_code": reason_code}


class ExportReplayInputTests(unittest.TestCase):
    def test_exports_clean_replay_record_from_prior_result(self):
        records = [
            {
                "company_name": "Example Robotics",
                "company_website_url": "https://example-robotics.test",
                "linkedin_job_title": "AI Engineer",
                "linkedin_job_location": "New York, NY",
                "career_page_url": "https://jobs.lever.co/example-robotics",
                "job_list_page_url": "https://jobs.lever.co/example-robotics",
                "pipeline_status": "partial",
                "stages": [
                    _stage("linkedin_discovery", "success"),
                    _stage("website_resolution", "success"),
                    _stage("career_discovery", "success"),
                    _stage("job_board_discovery", "success"),
                    _stage("opening_match", "partial", "OPENING_NOT_FOUND"),
                ],
            }
        ]
        args = SimpleNamespace(
            input="/tmp/results.json",
            pipeline_status=None,
            stage="opening_match",
            stage_status=["partial"],
            reason_code=["OPENING_NOT_FOUND"],
            provider=["lever"],
            include_missing_website=False,
            limit=None,
        )

        exported = export_replay_records(records, args)

        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["job_title"], "AI Engineer")
        self.assertEqual(exported[0]["career_root_url"], "https://jobs.lever.co/example-robotics")
        self.assertEqual(exported[0]["source"], "replay_input")
        self.assertEqual(exported[0]["source_trace"]["replay"]["provider"], "lever")

    def test_missing_website_is_skipped_unless_requested(self):
        records = [
            {
                "company_name": "Unknown Startup",
                "company_website_url": "",
                "linkedin_company_url": "https://www.linkedin.com/company/unknown-startup",
                "pipeline_status": "failed",
                "stages": [_stage("website_resolution", "failed", "WEBSITE_NOT_RESOLVED")],
            }
        ]
        args = SimpleNamespace(
            input="/tmp/results.json",
            pipeline_status=["failed"],
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            include_missing_website=False,
            limit=None,
        )

        self.assertEqual(export_replay_records(records, args), [])

        args.include_missing_website = True
        exported = export_replay_records(records, args)

        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["linkedin_company_url"], "https://www.linkedin.com/company/unknown-startup")

    def test_exported_records_can_be_loaded_as_company_inputs(self):
        records = [
            {
                "company_name": "PostHog",
                "company_website_url": "https://posthog.com",
                "career_page_url": "https://posthog.com/careers/jobs",
                "linkedin_job_title": "AI Engineer",
                "pipeline_status": "partial",
                "stages": [_stage("job_board_discovery", "success")],
            }
        ]
        args = SimpleNamespace(
            input="/tmp/results.json",
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            include_missing_website=False,
            limit=None,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            path.write_text(json.dumps(export_replay_records(records, args)), encoding="utf-8")
            companies = load_company_inputs(path)

        self.assertEqual(companies[0].company_name, "PostHog")
        self.assertEqual(companies[0].career_root_url, "https://posthog.com/careers/jobs")
        self.assertEqual(companies[0].job_title, "AI Engineer")

    def test_cli_writes_replay_file(self):
        records = [
            {
                "company_name": "Anthropic",
                "company_website_url": "https://www.anthropic.com",
                "career_page_url": "https://job-boards.greenhouse.io/anthropic",
                "pipeline_status": "partial",
                "stages": [_stage("job_board_discovery", "success")],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "results.json"
            output_path = Path(directory) / "replay.json"
            input_path.write_text(json.dumps(records), encoding="utf-8")

            import sys

            old_argv = sys.argv
            try:
                sys.argv = [
                    "export_replay_input.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ]
                main()
            finally:
                sys.argv = old_argv

            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exported[0]["company_name"], "Anthropic")


if __name__ == "__main__":
    unittest.main()
