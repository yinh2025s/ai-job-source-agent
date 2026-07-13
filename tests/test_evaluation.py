import unittest
from pathlib import Path

import json

from job_source_agent.evaluation import compare_summaries, evaluate_expectations, summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def test_summary_tracks_funnel_and_provider_counts(self):
        results = [
            {
                "company_website_url": "https://jobs.ashbyhq.com/acme",
                "career_page_url": "https://jobs.ashbyhq.com/acme",
                "job_list_page_url": "https://jobs.ashbyhq.com/acme",
                "open_position_url": "https://jobs.ashbyhq.com/acme/abc",
                "status": "success",
                "error": None,
            },
            {
                "company_website_url": "https://example.com",
                "career_page_url": None,
                "job_list_page_url": None,
                "open_position_url": None,
                "status": "failed",
                "error": "career_page_not_found",
            },
        ]

        summary = summarize_results(results, elapsed_sec=1.2)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["with_opening"], 1)
        self.assertEqual(summary["rates"]["opening"], 0.5)
        self.assertEqual(summary["provider_counts"]["ashby"], 1)
        self.assertEqual(summary["failure_stage_counts"]["career_page"], 1)
        self.assertEqual(summary["stage_funnel"]["career_discovery"]["not_recorded"], 2)
        self.assertEqual(summary["stage_duration_ms"]["career_discovery"]["count"], 0)

    def test_fixed_benchmark_reaches_openings_offline(self):
        companies = load_company_inputs(ROOT / "samples" / "benchmark_companies.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company).result_record() for company in companies]
        summary = summarize_results(results)

        self.assertEqual(summary["total"], 13)
        self.assertEqual(summary["with_opening"], 13)
        self.assertEqual(summary["provider_counts"]["icims"], 3)
        self.assertEqual(summary["provider_counts"]["workday"], 1)
        self.assertEqual(summary["provider_counts"]["bamboohr"], 1)
        self.assertEqual(summary["provider_counts"]["rippling"], 1)
        self.assertEqual(summary["stage_funnel"]["opening_match"]["success"], 13)
        self.assertEqual(summary["pipeline_status_counts"]["success"], 13)
        self.assertEqual(len(summary["company_stage_matrix"]), 13)

        expectations = json.loads((ROOT / "samples" / "benchmark_expectations.json").read_text(encoding="utf-8"))
        checks = evaluate_expectations(results, expectations)

        self.assertEqual(checks["passed"], 13)
        self.assertEqual(checks["failed"], 0)

    def test_summary_comparison_reports_rate_and_stage_deltas(self):
        baseline = {
            "rates": {"opening": 0.4},
            "pipeline_status_counts": {"success": 4, "failed": 6},
            "stage_funnel": {"opening_match": {"success": 4}},
        }
        current = {
            "rates": {"opening": 0.7},
            "pipeline_status_counts": {"success": 7, "failed": 3},
            "stage_funnel": {"opening_match": {"success": 7}},
        }

        comparison = compare_summaries(current, baseline)

        self.assertEqual(comparison["rates_delta"]["opening"], 0.3)
        self.assertEqual(comparison["pipeline_status_delta"]["success"], 3)
        self.assertEqual(comparison["stage_success_delta"]["opening_match"], 3)

    def test_summary_tracks_checkpoint_activity_from_result_trace(self):
        results = [
            {
                "trace": {
                    "checkpoint_events": [
                        {"stage": "website_resolution", "action": "restore"},
                        {"stage": "career_discovery", "action": "save"},
                        {"stage": "career_discovery", "action": "save"},
                    ]
                }
            },
            {
                "trace": {
                    "checkpoint_events": [
                        {"stage": "career_discovery", "action": "invalidate_from"},
                        {"stage": "", "action": "save"},
                        "invalid",
                    ]
                }
            },
        ]

        summary = summarize_results(results)

        self.assertEqual(
            summary["checkpoint_action_counts"],
            {"restore": 1, "save": 3, "invalidate_from": 1},
        )
        self.assertEqual(
            summary["checkpoint_stage_counts"],
            {"website_resolution": 1, "career_discovery": 3},
        )

    def test_summary_checkpoint_activity_is_backward_compatible_without_trace(self):
        summary = summarize_results([{}, {"trace": None}, {"trace": {}}])

        self.assertEqual(summary["checkpoint_action_counts"], {})
        self.assertEqual(summary["checkpoint_stage_counts"], {})

    def test_summary_groups_opening_availability_diagnostics(self):
        results = [
            {
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                        "evidence": [
                            {
                                "type": "availability_diagnostic",
                                "disposition": "verified_inventory_no_match",
                            }
                        ],
                    }
                ]
            },
            {
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "evidence": [
                            {
                                "type": "availability_diagnostic",
                                "disposition": "discovery_incomplete",
                            }
                        ],
                    }
                ]
            },
        ]

        summary = summarize_results(results)

        self.assertEqual(
            summary["availability_diagnostic_counts"],
            {"verified_inventory_no_match": 1, "discovery_incomplete": 1},
        )

    def test_stage_provider_takes_precedence_over_external_apply_url_host(self):
        result = {
            "open_position_url": "https://app.careerpuck.com/job-board/lyft/job/123",
            "stages": [
                {
                    "stage": "opening_match",
                    "status": "success",
                    "provider": "greenhouse",
                }
            ],
        }

        summary = summarize_results([result])

        self.assertEqual(summary["provider_counts"], {"greenhouse": 1})


if __name__ == "__main__":
    unittest.main()
