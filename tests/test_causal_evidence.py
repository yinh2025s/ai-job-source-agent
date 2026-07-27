import unittest

from job_source_agent.causal_evidence import (
    build_causal_ledger,
    classify_causal_record,
    linkedin_job_id,
)


def record(job_id="123456", company="Acme", reason="JOB_BOARD_NOT_FOUND"):
    return {
        "company_name": company,
        "linkedin_job_url": f"https://www.linkedin.com/jobs/view/role-at-acme-{job_id}",
        "linkedin_job_title": "Engineer",
        "error_code": reason,
        "stages": [
            {"stage": "linkedin_discovery", "status": "success"},
            {
                "stage": "job_board_discovery",
                "status": "failed",
                "reason_code": reason,
            },
        ],
        "identity_assertion": {
            "verdict": "not_applicable",
            "failure_codes": [],
            "provider": None,
        },
        "trace": {
            "stages": {
                "job_board_discovery": {
                    "candidate_discovery": {
                        "pool": {"candidate_count": 0, "candidates": []},
                        "sources": [
                            {
                                "source": "ProviderSearchCandidateDiscovery",
                                "status": "success",
                                "candidate_count": 0,
                                "trace": {
                                    "search": {
                                        "queries": [
                                            {
                                                "source": "bing_rss",
                                                "result_count": 10,
                                                "candidates": [],
                                                "error": None,
                                            }
                                        ],
                                        "candidates": [],
                                        "stopped_reason": "no_valid_candidates",
                                    }
                                },
                            }
                        ],
                    }
                }
            },
            "steps": [
                {
                    "name": "find_job_board",
                    "candidate_coordinator": {
                        "candidate_count": 0,
                        "routes": {
                            "external_apply": {
                                "status": "not_applicable",
                                "candidate_count": 0,
                                "failure": {"reason_code": "detail_not_observed"},
                            },
                            "provider_search": {
                                "status": "completed",
                                "candidate_count": 0,
                                "diagnostics": {"query_error_count": "0"},
                            },
                        },
                    },
                }
            ]
        },
    }


class CausalEvidenceTests(unittest.TestCase):
    def test_linkedin_job_id_canonicalizes_slug(self):
        self.assertEqual(
            linkedin_job_id("https://www.linkedin.com/jobs/view/engineer-at-acme-1234567890"),
            "1234567890",
        )
        with self.assertRaises(ValueError):
            linkedin_job_id("https://example.com/jobs/view/1234567890")

    def test_unaccepted_focused_terminal_does_not_replace_baseline(self):
        old = record(reason="JOB_BOARD_NOT_FOUND")
        new = record(reason=None)
        new["open_position_url"] = "https://jobs.example/opening/1"
        ledger = build_causal_ledger([("old", [old]), ("new", [new])])

        self.assertEqual(ledger["record_count"], 1)
        self.assertEqual(ledger["records"][0]["artifact_label"], "old")
        self.assertEqual(ledger["records"][0]["durable_outcome"], "unresolved")

    def test_accepted_focused_terminal_replaces_baseline(self):
        old = record(reason="JOB_BOARD_NOT_FOUND")
        new = record(reason=None)
        new["open_position_url"] = "https://jobs.example/opening/1"
        ledger = build_causal_ledger(
            [("old", [old]), ("new", [new])],
            accepted_terminals={
                "123456": {
                    "durable_outcome": "exact",
                    "evidence_ref": "review.md",
                }
            },
        )

        self.assertEqual(ledger["records"][0]["artifact_label"], "new")
        self.assertEqual(ledger["records"][0]["durable_outcome"], "exact")

    def test_duplicate_job_id_inside_artifact_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate LinkedIn job id"):
            build_causal_ledger([("one", [record(), record()])])

    def test_selected_career_transport_failure_requires_matching_url(self):
        item = record(reason="FETCH_FAILED")
        item["stages"][1]["stage"] = "career_discovery"
        item["trace"]["stages"]["career_discovery"] = {
            "candidates": [
                {
                    "url": "https://acme.example/careers",
                    "origin": "page_link",
                }
            ],
            "candidate_fetch_errors": [
                {
                    "url": "https://acme.example/careers",
                    "origin": "page_link",
                    "evidence_tier": 1,
                    "reason_code": "FETCH_FAILED",
                    "retryable": True,
                    "error": "SSL EOF",
                }
            ],
        }
        item["trace"]["retry_events"] = [
            {
                "url": "https://acme.example/careers",
                "reason_code": "FETCH_FAILED",
                "retryable": True,
                "transport_phase": "tls",
            }
        ]

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "correct_candidate_transport_failure")
        self.assertEqual(classified["trigger"], "evidence_backed_career_tls")

    def test_budget_failure_uses_stage_specific_code_path(self):
        item = record(reason="FETCH_BUDGET_EXHAUSTED")

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "budget_starvation")
        self.assertEqual(
            classified["code_path"],
            "job_board_discovery.budget_controller",
        )

    def test_identity_rejection_precedes_other_categories(self):
        item = record(reason="RESULT_IDENTITY_MISMATCH")
        item["identity_assertion"] = {
            "verdict": "rejected",
            "failure_codes": ["tenant_mismatch"],
        }

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "candidate_identity_rejected")
        self.assertEqual(classified["trigger"], "tenant_mismatch")

    def test_provider_search_candidate_is_bypass_evidence(self):
        item = record()
        item["trace"]["stages"]["job_board_discovery"]["route_evaluation"] = {
            "routes": {
                "provider_search": {
                    "candidate_count": 1,
                    "provider_verified_count": 1,
                    "relationship_verified_count": 0,
                }
            }
        }

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "candidate_identity_rejected")
        self.assertEqual(
            classified["trigger"],
            "provider_verified_hiring_relationship_unverified",
        )
        self.assertEqual(classified["bypass_opportunities"], ["provider_search"])

    def test_query_errors_are_source_rejection_not_empty_candidate_route(self):
        item = record()
        search = item["trace"]["stages"]["job_board_discovery"]["candidate_discovery"][
            "sources"
        ][0]["trace"]["search"]
        search["queries"] = [
            {"source": "bing_rss", "result_count": 0, "error": "HTTP 403"},
            {"source": "duckduckgo", "result_count": 0, "error": "challenge"},
        ]

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "linkedin_or_search_source_rejected")
        self.assertEqual(
            classified["trigger"],
            "ProviderSearchCandidateDiscovery:all_sources_rejected",
        )

    def test_partial_query_error_with_results_is_not_source_rejection(self):
        item = record()
        search = item["trace"]["stages"]["job_board_discovery"]["candidate_discovery"][
            "sources"
        ][0]["trace"]["search"]
        search["queries"] = [
            {"source": "bing_rss", "result_count": 40, "error": None},
            {"source": "duckduckgo", "result_count": 0, "error": "challenge"},
        ]

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "correct_candidate_not_produced")

    def test_empty_routes_are_candidate_not_produced(self):
        classified = classify_causal_record(record(), artifact_label="live")

        self.assertEqual(classified["category"], "correct_candidate_not_produced")
        self.assertEqual(classified["trigger"], "search_results_filtered_to_zero")

    def test_cluster_requires_three_independent_companies(self):
        one = record("123456", "Acme")
        two = record("123457", "Acme")
        three = record("123458", "Beta")
        ledger = build_causal_ledger([("live", [one, two, three])])
        cluster = ledger["clusters"][0]

        self.assertEqual(cluster["record_count"], 3)
        self.assertEqual(cluster["company_count"], 2)
        self.assertFalse(cluster["qualified_for_implementation"])

        four = record("123459", "Gamma")
        ledger = build_causal_ledger([("live", [one, two, three, four])])
        cluster = ledger["clusters"][0]
        self.assertTrue(cluster["meets_company_count"])
        self.assertFalse(cluster["qualified_for_implementation"])
        self.assertIn(
            "current_version_reproduction_not_reviewed",
            cluster["qualification_blockers"],
        )

        ledger = build_causal_ledger(
            [("live", [one, two, three, four])],
            reviewed_cluster_signatures={cluster["cluster_signature"]},
        )
        self.assertTrue(ledger["clusters"][0]["qualified_for_implementation"])

    def test_opening_incomplete_is_split_by_inventory_mechanism(self):
        item = record(reason="OPENING_DISCOVERY_INCOMPLETE")
        item["stages"][1]["stage"] = "opening_match"
        item["job_list_page_url"] = "https://jobs.example/search"
        item["trace"]["stages"]["opening_match"] = {
            "job_search_actions": [
                {
                    "method": "get",
                    "disposition": "eligible",
                    "query_field": "search",
                }
            ],
            "generic_inventory": [
                {
                    "candidate_count": 0,
                    "complete": False,
                    "stop_reason": "unsafe_next_url",
                }
            ],
        }

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(
            classified["trigger"],
            "declared_get_inventory_unsafe_next_url",
        )
        self.assertEqual(
            classified["code_path"],
            "opening_match.execute_declared_get_search",
        )

    def test_undeclared_generic_inventory_is_scoped_to_integration_origin(self):
        items = []
        for job_id, company, host in (
            ("123456", "Alpha", "jobs.alpha.example"),
            ("123457", "Beta", "careers.beta.example"),
            ("123458", "Gamma", "apply.gamma.example"),
        ):
            item = record(job_id, company, reason="OPENING_DISCOVERY_INCOMPLETE")
            item["stages"][1]["stage"] = "opening_match"
            item["job_list_page_url"] = f"https://{host}/jobs"
            item["trace"]["stages"]["opening_match"] = {
                "job_list_url": f"https://{host}/jobs",
                "generic_inventory": [
                    {
                        "candidate_count": 0,
                        "complete": False,
                        "stop_reason": "single_page_unbounded",
                    }
                ],
                "js_declared_inventory": [
                    {"status": "transport_not_declared"}
                ],
            }
            items.append(item)

        ledger = build_causal_ledger([("live", items)])

        self.assertEqual(len(ledger["clusters"]), 3)
        self.assertEqual(ledger["candidate_cluster_count"], 0)
        self.assertTrue(
            all(
                row["confidence"] == "insufficient"
                and row["trigger"].startswith("inventory_integration_unidentified:")
                for row in ledger["records"]
            )
        )

    def test_career_deadline_before_search_is_primary_budget_trigger(self):
        item = record(reason="FETCH_BUDGET_EXHAUSTED")
        item["stages"][1]["stage"] = "career_discovery"
        item["trace"]["stages"]["career_discovery"] = {
            "search_discovery": {
                "queries": [],
                "stopped_reason": "deadline_exhausted",
                "fetch_budget_unavailable": True,
            },
            "transport_budget": {
                "dispatched": 8,
                "limit": 32,
                "exhausted": False,
            },
        }

        classified = classify_causal_record(item, artifact_label="live")

        self.assertEqual(classified["category"], "budget_starvation")
        self.assertEqual(
            classified["trigger"],
            "career_search_deadline_before_execution",
        )


if __name__ == "__main__":
    unittest.main()
