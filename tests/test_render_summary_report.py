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
    "terminal_outcome_counts": {"exact_opening": 1, "verified_no_match": 1},
    "regression": {
        "rates_delta": {"opening": 0.25, "job_list": 0.0},
        "pipeline_status_delta": {"success": 1, "partial": -1},
        "terminal_outcome_delta": {"exact_opening": 1, "retryable_failure": -1},
        "stage_success_delta": {"opening_match": 1, "job_board_discovery": 0},
    },
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
    "provider_stage_status_counts": {
        "lever": {
            "linkedin_discovery": {"success": 1},
            "website_resolution": {"success": 1},
            "hiring_identity_resolution": {"success": 1},
            "career_discovery": {"success": 1},
            "job_board_discovery": {"success": 1},
            "opening_match": {"partial": 1},
            "result_validation": {"success": 1},
        },
        "greenhouse": {
            "linkedin_discovery": {"success": 1},
            "website_resolution": {"success": 1},
            "hiring_identity_resolution": {"success": 1},
            "career_discovery": {"success": 1},
            "job_board_discovery": {"success": 1},
            "opening_match": {"success": 1, "failed": 2},
            "result_validation": {"success": 1},
        },
    },
    "provider_reason_code_counts": {
        "lever": {"OPENING_NOT_FOUND": 1, "FETCH_FAILED": 2},
        "greenhouse": {},
    },
    "failure_clusters": [
        {
            "stage": "opening_match",
            "provider": "lever",
            "reason_code": "OPENING_NOT_FOUND",
            "company_count": 2,
            "retryable_count": 0,
            "company_names": ["B", "C"],
            "inventory_disposition_counts": {
                "verified_inventory_no_match": 2,
            },
            "terminal_outcome_counts": {"verified_no_match": 2},
        }
    ],
    "reason_code_counts": {"OPENING_NOT_FOUND": 1},
    "checkpoint_action_counts": {"save": 3, "restore": 1, "invalidate_from": 1},
    "checkpoint_stage_counts": {"career_discovery": 3, "website_resolution": 2},
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
        self.assertIn("## Regression", report)
        self.assertIn("## Terminal Outcomes", report)
        self.assertIn("| exact_opening | 1 |", report)
        self.assertIn("| opening | +0.25 |", report)
        self.assertIn("| partial | -1 |", report)
        self.assertIn("| retryable_failure | -1 |", report)
        self.assertIn("| S6 opening_match | +1 |", report)
        self.assertIn("## Stage Durations", report)
        self.assertIn("| S6 opening_match | 2 | 70 | 80 |", report)
        self.assertIn("| opening | 50.0% |", report)
        self.assertIn("## Provider Stage Reliability", report)
        self.assertIn("| greenhouse | 1 OK | 1 OK | 1 OK | 1 OK | 1 OK | 1 OK, 2 FAIL | 1 OK |", report)
        self.assertLess(report.index("| greenhouse | 1 OK"), report.index("| lever | 1 OK"))
        self.assertIn("## Provider Reason Codes", report)
        self.assertIn("| lever | FETCH_FAILED | 2 |", report)
        self.assertLess(report.index("| lever | FETCH_FAILED | 2 |"), report.index("| lever | OPENING_NOT_FOUND | 1 |"))
        self.assertIn("## Actionable Failure Clusters", report)
        self.assertIn(
            "| 1 | opening_match | lever | OPENING_NOT_FOUND | 2 | 0 | verified_no_match:2 | verified_inventory_no_match:2 | B, C |",
            report,
        )
        self.assertIn("| B | lever | partial | OPENING_NOT_FOUND", report)
        self.assertIn("## Checkpoint Activity", report)
        self.assertIn("| Action | save | 3 |", report)
        self.assertIn("| Stage | S4 career_discovery | 3 |", report)
        self.assertLess(report.index("| Action | save | 3 |"), report.index("| Action | invalidate_from | 1 |"))
        self.assertIn("## Expectations", report)

    def test_provider_reliability_sections_handle_missing_data(self):
        report = render_markdown_report({})

        self.assertIn("## Provider Stage Reliability", report)
        self.assertIn("| none | - | - | - | - | - | - | - |", report)
        self.assertIn("## Provider Reason Codes", report)
        self.assertIn("| none | none | 0 |", report)
        self.assertIn("## Actionable Failure Clusters", report)
        self.assertIn("| 0 | none | none | none | 0 | 0 | - | - | - |", report)
        self.assertIn("## Checkpoint Activity", report)
        self.assertEqual(report.count("| none | none | 0 |"), 3)

    def test_render_company_identity_drift_when_available(self):
        summary = {
            "regression": {
                "company_identity_drift": {
                    "comparison_status": "available",
                    "added_companies": ["Zulu", "Alpha"],
                    "removed_companies": ["Removed"],
                    "changed_companies": ["Zulu", "Alpha"],
                    "changed_fields": {
                        "opening.canonical_url": ["Zulu"],
                        "job_board.tenant": ["Alpha"],
                    },
                }
            }
        }

        report = render_markdown_report(summary)

        self.assertIn("### Company Identity Drift", report)
        self.assertIn("| Added | 2 | Alpha, Zulu |", report)
        self.assertIn("| Removed | 1 | Removed |", report)
        self.assertIn("| Changed | 2 | Alpha, Zulu |", report)
        self.assertIn("| job_board.tenant | 1 | Alpha |", report)
        self.assertIn("| opening.canonical_url | 1 | Zulu |", report)
        self.assertLess(report.index("| job_board.tenant"), report.index("| opening.canonical_url"))

    def test_render_company_identity_drift_unavailable_for_legacy_baseline(self):
        summary = {
            "regression": {
                "company_identity_drift": {
                    "comparison_status": "not_available",
                    "added_companies": [],
                    "removed_companies": [],
                    "changed_companies": [],
                    "changed_fields": {},
                }
            }
        }

        report = render_markdown_report(summary)

        self.assertIn("### Company Identity Drift", report)
        self.assertIn("Not available: the baseline does not contain company identity data.", report)
        self.assertNotIn("| Added | 0 |", report)

    def test_render_failed_identity_expectations_without_sensitive_details(self):
        summary = {
            "expectation_checks": {
                "total": 3,
                "passed": 1,
                "failed": 2,
                "checks": [
                    {
                        "company_name": "Zulu",
                        "passed": False,
                        "failures": ["stage:opening_match=partial", "identity:opening_url_mismatch"],
                        "actual_identity": {"runtime_id": "secret-runtime-id"},
                    },
                    {
                        "company_name": "Alpha",
                        "passed": False,
                        "failures": [
                            "identity:website_url_mismatch",
                            "identity:job_board_tenant_mismatch",
                        ],
                        "trace": {"token": "sensitive-token"},
                    },
                    {"company_name": "Passing", "passed": True, "failures": []},
                ],
            }
        }

        report = render_markdown_report(summary)

        self.assertIn("### Failed Identity Expectations", report)
        self.assertIn(
            "| Alpha | identity:job_board_tenant_mismatch, identity:website_url_mismatch |",
            report,
        )
        self.assertIn("| Zulu | identity:opening_url_mismatch |", report)
        self.assertLess(report.index("| Alpha |"), report.index("| Zulu |"))
        self.assertNotIn("secret-runtime-id", report)
        self.assertNotIn("sensitive-token", report)
        self.assertNotIn("stage:opening_match=partial", report)

    def test_new_identity_sections_handle_absent_fields(self):
        report = render_markdown_report({"regression": {}, "expectation_checks": {"total": 0}})

        self.assertNotIn("Company Identity Drift", report)
        self.assertNotIn("Failed Identity Expectations", report)

    def test_identity_detail_lists_use_report_row_limit(self):
        summary = {
            "regression": {
                "company_identity_drift": {
                    "comparison_status": "available",
                    "added_companies": ["Zulu", "Alpha"],
                    "removed_companies": [],
                    "changed_companies": [],
                    "changed_fields": {},
                }
            },
            "expectation_checks": {
                "total": 2,
                "passed": 0,
                "failed": 2,
                "checks": [
                    {
                        "company_name": "Alpha",
                        "passed": False,
                        "failures": ["identity:z_failure", "identity:a_failure"],
                    },
                    {
                        "company_name": "Zulu",
                        "passed": False,
                        "failures": ["identity:opening_url_mismatch"],
                    },
                ],
            },
        }

        report = render_markdown_report(summary, max_matrix_rows=1)

        self.assertIn("| Added | 2 | Alpha, ... 1 more |", report)
        self.assertIn("| Alpha | identity:a_failure, ... 1 more |", report)
        self.assertIn("| ... 1 more rows | |", report)
        self.assertNotIn("identity:z_failure", report)

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
