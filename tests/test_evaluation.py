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

        self.assertEqual(summary["total"], 23)
        self.assertEqual(summary["with_opening"], 23)
        self.assertEqual(summary["provider_counts"]["icims"], 3)
        self.assertEqual(summary["provider_counts"]["workday"], 1)
        self.assertEqual(summary["provider_counts"]["bamboohr"], 1)
        self.assertEqual(summary["provider_counts"]["rippling"], 1)
        self.assertEqual(summary["provider_counts"]["phenom"], 1)
        self.assertEqual(summary["provider_counts"]["paycom"], 1)
        self.assertEqual(summary["provider_counts"]["ripplehire"], 1)
        self.assertEqual(summary["provider_counts"]["taleo"], 1)
        self.assertEqual(summary["provider_counts"]["eightfold"], 1)
        self.assertEqual(summary["provider_counts"]["jazzhr"], 1)
        self.assertEqual(summary["provider_counts"]["avature"], 1)
        self.assertEqual(summary["provider_counts"]["breezy"], 1)
        self.assertEqual(summary["provider_counts"]["meta_careers"], 1)
        self.assertEqual(summary["provider_counts"]["sitecore_next_jobs"], 1)
        self.assertEqual(summary["stage_funnel"]["opening_match"]["success"], 23)
        self.assertEqual(summary["pipeline_status_counts"]["success"], 23)
        self.assertEqual(len(summary["company_stage_matrix"]), 23)

        expectations = json.loads((ROOT / "samples" / "benchmark_expectations.json").read_text(encoding="utf-8"))
        checks = evaluate_expectations(results, expectations)

        self.assertEqual(checks["passed"], 23)
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

    def test_summary_groups_s5_source_posting_disposition_without_changing_s6_counts(self):
        results = [
            {
                "company_name": "LinkedIn Native Co",
                "status": "partial",
                "pipeline_status": "partial",
                "stages": [
                    {
                        "stage": "job_board_discovery",
                        "status": "partial",
                        "reason_code": "LINKEDIN_NATIVE_ONLY",
                        "evidence": [
                            {
                                "type": "source_posting_availability",
                                "disposition": "linkedin_native_only",
                                "availability": "active",
                                "apply_mode": "linkedin_native",
                                "evidence_source": "authenticated_detail_dom",
                                "source_posting_url": "https://www.linkedin.com/jobs/view/123",
                            }
                        ],
                    },
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                        "evidence": [
                            {
                                "type": "availability_diagnostic",
                                "disposition": "discovery_incomplete",
                            }
                        ],
                    },
                ],
            }
        ]

        summary = summarize_results(results)

        self.assertEqual(
            summary["source_posting_disposition_counts"],
            {"linkedin_native_only": 1},
        )
        self.assertEqual(
            summary["availability_diagnostic_counts"],
            {"discovery_incomplete": 1},
        )
        self.assertEqual(
            summary["company_stage_matrix"][0]["reason_code"],
            "LINKEDIN_NATIVE_ONLY",
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

    def test_summary_builds_stable_actionable_failure_clusters(self):
        results = [
            {
                "company_name": "Zulu Co",
                "career_page_url": "https://example.com/careers",
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                        "retryable": False,
                        "evidence": [
                            {
                                "type": "availability_diagnostic",
                                "disposition": "verified_inventory_no_match",
                            }
                        ],
                    },
                    {
                        "stage": "result_validation",
                        "status": "success",
                        "reason_code": "IGNORED_SUCCESS_REASON",
                    },
                ],
            },
            {
                "company_name": "alpha Co",
                "job_list_page_url": "https://jobs.ashbyhq.com/alpha",
                "trace": {
                    "stages": {
                        "opening_match": {
                            "availability_diagnostic": {
                                "disposition": "discovery_incomplete",
                            }
                        }
                    }
                },
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "failed",
                        "reason_code": "OPENING_NOT_FOUND",
                        "retryable": True,
                        "evidence": "malformed",
                    }
                ],
            },
            {
                "company_name": "No Context Co",
                "stages": [
                    {
                        "stage": "career_discovery",
                        "status": "unsupported",
                        "reason_code": "CAREER_VARIANT_UNSUPPORTED",
                    },
                    {
                        "stage": "website_resolution",
                        "status": "failed",
                    },
                ],
            },
        ]

        expected = [
            {
                "stage": "career_discovery",
                "provider": "unknown",
                "reason_code": "CAREER_VARIANT_UNSUPPORTED",
                "company_count": 1,
                "retryable_count": 0,
                "company_names": ["No Context Co"],
                "inventory_disposition_counts": {},
            },
            {
                "stage": "opening_match",
                "provider": "ashby",
                "reason_code": "OPENING_NOT_FOUND",
                "company_count": 1,
                "retryable_count": 1,
                "company_names": ["alpha Co"],
                "inventory_disposition_counts": {"discovery_incomplete": 1},
            },
            {
                "stage": "opening_match",
                "provider": "generic",
                "reason_code": "OPENING_NOT_FOUND",
                "company_count": 1,
                "retryable_count": 0,
                "company_names": ["Zulu Co"],
                "inventory_disposition_counts": {"verified_inventory_no_match": 1},
            },
        ]

        self.assertEqual(summarize_results(results)["failure_clusters"], expected)
        self.assertEqual(
            summarize_results(list(reversed(results)))["failure_clusters"],
            expected,
        )

    def test_failure_cluster_company_names_are_unique_sorted_and_bounded(self):
        results = []
        for index in reversed(range(25)):
            results.append(
                {
                    "company_name": f"Company {index:02d}",
                    "stages": [
                        {
                            "stage": "job_board_discovery",
                            "status": "failed",
                            "reason_code": "PROVIDER_FETCH_FAILED",
                            "provider": "workday",
                            "retryable": index % 2 == 0,
                        }
                    ],
                }
            )
        results.append(results[0])

        cluster = summarize_results(results)["failure_clusters"][0]

        self.assertEqual(cluster["company_count"], 25)
        self.assertEqual(cluster["retryable_count"], 13)
        self.assertEqual(len(cluster["company_names"]), 20)
        self.assertEqual(
            cluster["company_names"],
            [f"Company {index:02d}" for index in range(20)],
        )


if __name__ == "__main__":
    unittest.main()
