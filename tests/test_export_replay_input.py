import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.checkpoint import CHECKPOINT_SCHEMA_VERSION, checkpoint_metadata
from job_source_agent.linkedin import load_company_inputs
from scripts.export_replay_input import export_replay_records, main


def _stage(stage, status, reason_code=None):
    return {"stage": stage, "status": status, "reason_code": reason_code}


class ExportReplayInputTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "input": "/tmp/results.json",
            "pipeline_status": None,
            "stage": None,
            "stage_status": None,
            "reason_code": None,
            "provider": None,
            "include_missing_website": False,
            "limit": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

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
        self.assertEqual(
            exported[0]["checkpoint"]["checkpoint_schema_version"],
            CHECKPOINT_SCHEMA_VERSION,
        )
        self.assertRegex(exported[0]["checkpoint"]["input_fingerprint"], r"^[0-9a-f]{64}$")

    def test_preserves_only_stable_linkedin_posting_evidence_from_trace_output(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        records = [{
            "company_name": "Native Apply",
            "company_website_url": "https://native.example",
            "linkedin_job_url": job_url,
            "pipeline_status": "partial",
            "trace": {
                "source_trace": {
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                        "observed_at": "2026-07-14T00:00:00Z",
                        "raw_html": "<html>authenticated content</html>",
                        "token": "secret",
                    },
                    "cookies": ["li_at=secret"],
                    "request": {"authorization": "secret"},
                }
            },
        }]

        exported = export_replay_records(records, self._args())
        source_trace = exported[0]["source_trace"]

        self.assertEqual(
            source_trace["linkedin_posting"],
            {
                "availability": "active",
                "apply_mode": "linkedin_native",
                "evidence_source": "authenticated_detail_dom",
                "job_url": job_url,
            },
        )
        self.assertEqual(set(source_trace), {"linkedin_posting", "replay"})
        self.assertNotIn("raw_html", json.dumps(exported))
        self.assertNotIn("secret", json.dumps(exported))
        self.assertEqual(exported[0]["checkpoint"], checkpoint_metadata(exported[0]))

    def test_preserves_backward_compatible_closed_source_posting_fields(self):
        records = [{
            "company_name": "Closed Role",
            "company_website_url": "https://closed.example",
            "source_trace": {
                "posting_status": "closed",
                "source_posting": {
                    "status": "expired",
                    "availability": "unavailable",
                    "payload": {"private": True},
                },
            },
        }]

        exported = export_replay_records(records, self._args())

        self.assertEqual(exported[0]["source_trace"]["posting_status"], "closed")
        self.assertEqual(
            exported[0]["source_trace"]["source_posting"],
            {"status": "expired", "availability": "unavailable"},
        )
        self.assertNotIn("payload", json.dumps(exported))

    def test_preserves_only_typed_expected_replay_transition(self):
        record = {
            "company_name": "Example Robotics",
            "company_website_url": "https://example.test",
            "pipeline_status": "partial",
            "replay_expected_transition": {
                "pipeline_status": "success",
                "failure_stage": {
                    "stage": "opening_match",
                    "status": "success",
                    "reason_code": None,
                    "raw_html": "must not persist",
                },
                "token": "must not persist",
            },
            "stages": [_stage("opening_match", "partial", "OPENING_NOT_FOUND")],
        }

        exported = export_replay_records([record], self._args())

        transition = exported[0]["source_trace"]["replay"]["expected_transition"]
        self.assertEqual(
            transition,
            {
                "pipeline_status": "success",
                "failure_stage": {
                    "stage": "opening_match",
                    "status": "success",
                    "reason_code": None,
                },
            },
        )
        self.assertNotIn("must not persist", json.dumps(exported))

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

    def test_external_apply_is_replayable_without_company_website(self):
        external = "https://jobs.ashbyhq.com/example/12345678-1234-1234-1234-123456789012"
        records = [{
            "company_name": "Example",
            "external_apply_url": external,
            "linkedin_job_title": "AI Engineer",
            "pipeline_status": "success",
            "stages": [_stage("job_board_discovery", "success")],
        }]
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

        exported = export_replay_records(records, args)

        self.assertEqual(exported[0]["external_apply_url"], external)
        self.assertRegex(exported[0]["checkpoint"]["input_fingerprint"], r"^[0-9a-f]{64}$")

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
        self.assertEqual(companies[0].source, "replay_input")

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
