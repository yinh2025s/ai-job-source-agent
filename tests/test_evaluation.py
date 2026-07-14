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
        self.assertEqual(
            summary["terminal_outcome_counts"],
            {"exact_opening": 1, "other_non_success": 1},
        )
        self.assertEqual(summary["stage_funnel"]["career_discovery"]["not_recorded"], 2)
        self.assertEqual(summary["stage_duration_ms"]["career_discovery"]["count"], 0)

    def test_fixed_benchmark_reaches_openings_offline(self):
        companies = load_company_inputs(ROOT / "samples" / "benchmark_companies.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company).result_record() for company in companies]
        summary = summarize_results(results)

        self.assertEqual(summary["total"], 25)
        self.assertEqual(summary["with_opening"], 25)
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
        self.assertEqual(summary["provider_counts"]["ceipal"], 1)
        self.assertEqual(summary["provider_counts"]["whitecarrot"], 1)
        self.assertEqual(summary["stage_funnel"]["opening_match"]["success"], 25)
        self.assertEqual(summary["pipeline_status_counts"]["success"], 25)
        self.assertEqual(len(summary["company_stage_matrix"]), 25)
        self.assertEqual(len(summary["company_identity_matrix"]), 25)

        expectations = json.loads((ROOT / "samples" / "benchmark_expectations.json").read_text(encoding="utf-8"))
        checks = evaluate_expectations(results, expectations)

        self.assertEqual(checks["passed"], 25)
        self.assertEqual(checks["failed"], 0)

    def test_identity_contract_rejects_url_swaps_and_accepts_explicit_aliases(self):
        result = self._identity_result()
        expected = self._identity_expectation()

        accepted = dict(result)
        accepted["company_website_url"] = "https://www.example.com/"
        accepted["career_page_url"] = "https://example.com/jobs/"
        expected["expected_identity"]["website_url_aliases"] = [
            "https://example.com"
        ]
        expected["expected_identity"]["career_page_url_aliases"] = [
            "https://example.com/jobs"
        ]
        self.assertEqual(
            evaluate_expectations([accepted], {"Example": expected})["failed"],
            0,
        )

        swaps = {
            "website": ("company_website_url", "https://other.example"),
            "tenant": ("job_list_page_url", "https://jobs.ashbyhq.com/other"),
            "opening": ("open_position_url", "https://jobs.ashbyhq.com/acme/opening-2"),
        }
        expected_failures = {
            "website": "identity:website_url_mismatch",
            "tenant": "identity:job_board_tenant_mismatch",
            "opening": "identity:opening_url_mismatch",
        }
        for name, (field, value) in swaps.items():
            with self.subTest(name=name):
                swapped = dict(result)
                swapped[field] = value
                check = evaluate_expectations([swapped], {"Example": expected})["checks"][0]
                self.assertIn(expected_failures[name], check["failures"])

    def test_identity_contract_preserves_requisition_query(self):
        result = self._identity_result()
        result["open_position_url"] = "https://jobs.ashbyhq.com/acme/opening?req=R-2"
        expected = self._identity_expectation()
        expected["expected_identity"]["opening"]["canonical_url"] = (
            "https://jobs.ashbyhq.com/acme/opening?req=R-1"
        )

        check = evaluate_expectations([result], {"Example": expected})["checks"][0]

        self.assertIn("identity:opening_url_mismatch", check["failures"])

    def test_identity_contract_fails_closed_for_invalid_expected_and_actual_urls(self):
        expected = self._identity_expectation()
        expected["expected_identity"]["website_url"] = "https://user:pass@example.com"
        result = self._identity_result()
        result["open_position_url"] = "https://jobs.ashbyhq.com:bad/opening"

        check = evaluate_expectations([result], {"Example": expected})["checks"][0]

        self.assertIn("identity:expected_website_url_url_invalid", check["failures"])
        self.assertIn("identity:actual_opening_url_invalid", check["failures"])
        self.assertIsNone(check["actual_identity"]["opening"]["canonical_url"])

    def test_duplicate_company_results_fail_instead_of_overwriting(self):
        result = self._identity_result()

        evaluation = evaluate_expectations(
            [result, {**result, "open_position_url": "https://jobs.ashbyhq.com/acme/other"}],
            {"Example": self._identity_expectation()},
        )

        self.assertEqual(evaluation["failed"], 1)
        self.assertEqual(evaluation["duplicate_company_names"], ["Example"])
        self.assertIn("duplicate_company_result", evaluation["checks"][0]["failures"])

        unexpected = evaluate_expectations(
            [
                {**result, "company_name": "Unexpected"},
                {**result, "company_name": "Unexpected"},
            ],
            {},
        )
        self.assertEqual(unexpected["failed"], 1)
        self.assertIn("duplicate_company_result", unexpected["checks"][0]["failures"])

    def test_expectations_without_identity_remain_compatible(self):
        result = self._identity_result()
        expectation = {
            "expected_provider": "ashby",
            "expected_minimum_stage": "opening_match",
            "require_exact_opening": True,
            "allow_job_board_fallback": False,
        }

        check = evaluate_expectations([result], {"Example": expectation})["checks"][0]

        self.assertTrue(check["passed"])
        self.assertIsNone(check["expected_identity"])
        self.assertIsNone(check["actual_identity"])

    @staticmethod
    def _identity_result():
        return {
            "company_name": "Example",
            "company_website_url": "https://example.com",
            "career_page_url": "https://example.com/careers",
            "job_list_page_url": "https://jobs.ashbyhq.com/acme",
            "open_position_url": "https://jobs.ashbyhq.com/acme/opening",
            "stages": [{"stage": "opening_match", "status": "success", "provider": "ashby"}],
        }

    @staticmethod
    def _identity_expectation():
        return {
            "expected_provider": "ashby",
            "expected_minimum_stage": "opening_match",
            "require_exact_opening": True,
            "allow_job_board_fallback": False,
            "expected_identity": {
                "website_url": "https://example.com",
                "career_page_url": "https://example.com/careers",
                "job_board": {
                    "provider": "ashby",
                    "tenant": "url:https://jobs.ashbyhq.com/acme",
                    "canonical_url": "https://jobs.ashbyhq.com/acme",
                },
                "opening": {
                    "canonical_url": "https://jobs.ashbyhq.com/acme/opening"
                },
            },
        }

    @staticmethod
    def _identity_matrix_row(
        company_name,
        *,
        board="https://jobs.example/acme",
        opening="https://jobs.example/acme/1",
    ):
        return {
            "company_name": company_name,
            "website_url": "https://example.com",
            "career_page_url": "https://example.com/careers",
            "job_board": {
                "provider": "example_ats",
                "tenant": f"url:{board}",
                "canonical_url": board,
            },
            "opening": {"canonical_url": opening},
        }

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
        self.assertEqual(
            comparison["company_identity_drift"]["comparison_status"],
            "not_available",
        )

    def test_summary_comparison_reports_company_and_field_identity_drift(self):
        baseline = {
            "company_identity_matrix": [
                self._identity_matrix_row("Zulu", opening="https://jobs.example/zulu/1"),
                self._identity_matrix_row("Alpha", board="https://jobs.example/alpha"),
                self._identity_matrix_row("Removed"),
            ]
        }
        current = {
            "company_identity_matrix": [
                self._identity_matrix_row("Alpha", board="https://jobs.example/other"),
                self._identity_matrix_row("Zulu", opening="https://jobs.example/zulu/2"),
                self._identity_matrix_row("Added"),
            ]
        }

        drift = compare_summaries(current, baseline)["company_identity_drift"]

        self.assertEqual(drift["comparison_status"], "available")
        self.assertEqual(drift["added_companies"], ["Added"])
        self.assertEqual(drift["removed_companies"], ["Removed"])
        self.assertEqual(drift["changed_companies"], ["Alpha", "Zulu"])
        self.assertEqual(
            drift["changed_fields"],
            {
                "job_board.canonical_url": ["Alpha"],
                "job_board.tenant": ["Alpha"],
                "opening.canonical_url": ["Zulu"],
            },
        )

    def test_summary_comparison_does_not_treat_old_summary_as_identity_removal(self):
        current = {"company_identity_matrix": [self._identity_matrix_row("Example")]}

        drift = compare_summaries(current, {})["company_identity_drift"]

        self.assertEqual(
            drift,
            {
                "comparison_status": "not_available",
                "added_companies": [],
                "removed_companies": [],
                "changed_companies": [],
                "changed_fields": {},
            },
        )

    def test_terminal_outcomes_use_durable_final_stage_semantics(self):
        def stage(name, status, reason_code=None, retryable=False, evidence=None):
            return {
                "stage": name,
                "status": status,
                "reason_code": reason_code,
                "retryable": retryable,
                "evidence": evidence or [],
            }

        verified = [{
            "type": "availability_diagnostic",
            "disposition": "verified_inventory_no_match",
        }]
        results = [
            {"open_position_url": "https://jobs.example/role"},
            {"stages": [stage("opening_match", "partial", "OPENING_NOT_FOUND", evidence=verified)]},
            {"stages": [stage("opening_match", "partial", "NO_PUBLIC_OPENINGS")]},
            {"stages": [stage("hiring_identity_resolution", "failed", "COMPANY_IDENTITY_AMBIGUOUS")]},
            {"stages": [stage("career_discovery", "failed", "NETWORK_TIMEOUT", retryable=True)]},
            {"stages": [stage("job_board_discovery", "partial", "LINKEDIN_NATIVE_ONLY")]},
            {"stages": [stage("career_discovery", "failed", "BOT_PROTECTION")]},
            {"stages": [stage("career_discovery", "failed", "OFFLINE_FIXTURE_MISSING")]},
            {"stages": [stage("job_board_discovery", "unsupported", "PROVIDER_VARIANT_UNSUPPORTED")]},
            {"stages": [stage("website_resolution", "failed", "WEBSITE_NOT_RESOLVED")]},
            {"stages": [stage(
                "opening_match",
                "partial",
                "OPENING_NOT_FOUND",
                evidence=[{
                    "type": "availability_diagnostic",
                    "disposition": "source_posting_closed",
                }],
            )]},
            {},
        ]

        expected = {
            "exact_opening": 1,
            "verified_no_match": 1,
            "no_public_openings": 1,
            "identity_ambiguous": 1,
            "retryable_failure": 1,
            "linkedin_native_only": 1,
            "external_blocked": 1,
            "replay_infrastructure_failure": 1,
            "unsupported_capability": 1,
            "discovery_unresolved": 1,
            "source_closed": 1,
            "other_non_success": 1,
        }
        self.assertEqual(summarize_results(results)["terminal_outcome_counts"], expected)
        self.assertEqual(
            summarize_results(list(reversed(results)))["terminal_outcome_counts"],
            expected,
        )

    def test_verified_no_match_beats_intermediate_trace_server_error(self):
        result = {
            "trace": {"provider_api": {"errors": [{"reason_code": "SERVER_ERROR"}]}},
            "stages": [{
                "stage": "opening_match",
                "status": "partial",
                "reason_code": "OPENING_NOT_FOUND",
                "retryable": False,
                "evidence": [{
                    "type": "availability_diagnostic",
                    "disposition": "verified_inventory_empty",
                }],
            }],
        }

        self.assertEqual(
            summarize_results([result])["terminal_outcome_counts"],
            {"verified_no_match": 1},
        )

    def test_summary_comparison_reports_terminal_outcome_deltas_with_old_inputs(self):
        comparison = compare_summaries(
            {"terminal_outcome_counts": {"exact_opening": 3, "retryable_failure": 1}},
            {"terminal_outcome_counts": {"exact_opening": 1, "verified_no_match": 2}},
        )

        self.assertEqual(
            comparison["terminal_outcome_delta"],
            {"exact_opening": 2, "retryable_failure": 1, "verified_no_match": -2},
        )
        self.assertEqual(compare_summaries({}, {})["terminal_outcome_delta"], {})

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
        self.assertEqual(
            summary["terminal_outcome_counts"],
            {"linkedin_native_only": 1},
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
                "terminal_outcome_counts": {"unsupported_capability": 1},
            },
            {
                "stage": "opening_match",
                "provider": "ashby",
                "reason_code": "OPENING_NOT_FOUND",
                "company_count": 1,
                "retryable_count": 1,
                "company_names": ["alpha Co"],
                "inventory_disposition_counts": {},
                "terminal_outcome_counts": {"retryable_failure": 1},
            },
            {
                "stage": "opening_match",
                "provider": "generic",
                "reason_code": "OPENING_NOT_FOUND",
                "company_count": 1,
                "retryable_count": 0,
                "company_names": ["Zulu Co"],
                "inventory_disposition_counts": {"verified_inventory_no_match": 1},
                "terminal_outcome_counts": {"verified_no_match": 1},
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
        self.assertEqual(cluster["terminal_outcome_counts"], {"retryable_failure": 13, "other_non_success": 12})
        self.assertEqual(len(cluster["company_names"]), 20)
        self.assertEqual(
            cluster["company_names"],
            [f"Company {index:02d}" for index in range(20)],
        )

    def test_failure_clusters_rank_largest_actionable_group_first(self):
        results = [
            {
                "company_name": "Early Stage One",
                "stages": [{
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                }],
            },
            *[
                {
                    "company_name": f"Opening {index}",
                    "job_list_page_url": "https://jobs.ashbyhq.com/example",
                    "stages": [{
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    }],
                }
                for index in range(3)
            ],
        ]

        clusters = summarize_results(results)["failure_clusters"]

        self.assertEqual(clusters[0]["stage"], "opening_match")
        self.assertEqual(clusters[0]["company_count"], 3)


if __name__ == "__main__":
    unittest.main()
