import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_summary_report import main, render_markdown_report


SUMMARY = {
    "total": 2,
    "pipeline_success": 1,
    "pipeline_partial": 1,
    "pipeline_failed": 0,
    "with_job_list": 2,
    "with_opening": 1,
    "elapsed_sec": 12.3,
    "rates": {"website": 1.0, "job_list": 1.0, "opening": 0.5},
    "stage_funnel": {
        "linkedin_discovery": {"success": 2},
        "website_resolution": {"success": 2},
        "hiring_identity_resolution": {"success": 2},
        "career_discovery": {"success": 2},
        "job_board_discovery": {"success": 2},
        "opening_match": {"success": 1, "partial": 1},
        "result_validation": {"success": 2},
    },
    "stage_duration_ms": {
        "linkedin_discovery": {"count": 2, "p50": 0, "p95": 0},
        "website_resolution": {"count": 2, "p50": 10, "p95": 20},
        "hiring_identity_resolution": {"count": 2, "p50": 1, "p95": 2},
        "career_discovery": {"count": 2, "p50": 30, "p95": 40},
        "job_board_discovery": {"count": 2, "p50": 50, "p95": 60},
        "opening_match": {"count": 2, "p50": 70, "p95": 80},
        "result_validation": {"count": 2, "p50": 0, "p95": 0},
    },
    "provider_counts": {"greenhouse": 1, "lever": 1},
    "reason_code_counts": {"OPENING_NOT_FOUND": 1},
    "expectation_checks": {"total": 2, "passed": 2, "failed": 0},
    "company_stage_matrix": [
        {
            "company_name": "A",
            "provider": "greenhouse",
            "pipeline_status": "success",
            "reason_code": None,
            "linkedin_discovery": "success",
            "website_resolution": "success",
            "hiring_identity_resolution": "success",
            "career_discovery": "success",
            "job_board_discovery": "success",
            "opening_match": "success",
            "result_validation": "success",
        },
        {
            "company_name": "B",
            "provider": "lever",
            "pipeline_status": "partial",
            "reason_code": "OPENING_NOT_FOUND",
            "linkedin_discovery": "success",
            "website_resolution": "success",
            "hiring_identity_resolution": "success",
            "career_discovery": "success",
            "job_board_discovery": "success",
            "opening_match": "partial",
            "result_validation": "success",
        },
    ],
}


class RenderSummaryReportTests(unittest.TestCase):
    def test_render_markdown_report_includes_core_sections(self):
        report = render_markdown_report(SUMMARY, title="Demo Report")

        self.assertIn("# Demo Report", report)
        self.assertIn("## Stage Funnel", report)
        self.assertIn("## Stage Durations", report)
        self.assertIn("| S6 opening_match | 2 | 70 | 80 |", report)
        self.assertIn("| opening | 50.0% |", report)
        self.assertIn("| B | lever | partial | OPENING_NOT_FOUND", report)
        self.assertIn("## Expectations", report)

    def test_cli_writes_report_file(self):
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.json"
            output_path = Path(directory) / "report.md"
            summary_path.write_text(json.dumps(SUMMARY), encoding="utf-8")

            import sys

            old_argv = sys.argv
            try:
                sys.argv = [
                    "render_summary_report.py",
                    "--summary",
                    str(summary_path),
                    "--output",
                    str(output_path),
                    "--title",
                    "CLI Report",
                ]
                main()
            finally:
                sys.argv = old_argv

            report = output_path.read_text(encoding="utf-8")

        self.assertIn("# CLI Report", report)


if __name__ == "__main__":
    unittest.main()
