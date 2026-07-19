import json
import unittest

from job_source_agent.candidate_route_evaluation import (
    aggregate_candidate_route_metrics,
    evaluate_candidate_routes,
)


BOARD = "https://jobs.example.test/acme"
OPENING = f"{BOARD}/roles/ai-engineer"


def route(
    *,
    covered=True,
    candidate=True,
    board=True,
    relationship=True,
    board_url=BOARD,
):
    return {
        "input_available": covered,
        "candidate_count": int(candidate),
        "provider_verified_count": int(board),
        "relationship_verified_count": int(relationship),
        "verified_relationship_boards": (
            [
                {
                    "provider": "example",
                    "tenant": "acme",
                    "url": board_url,
                }
            ]
            if relationship
            else []
        ),
    }


def result(routes, *, exact=True):
    value = {
        "company_name": "Acme",
        "company_website_url": "https://acme.example",
        "career_page_url": "https://acme.example/careers",
        "external_apply_url": f"{OPENING}?source=linkedin",
        "job_list_page_url": BOARD,
        "open_position_url": OPENING if exact else None,
        "trace": {
            "stages": {
                "job_board_discovery": {
                    "method": "parallel_candidate_discovery",
                    "route_evaluation": {"routes": routes},
                }
            }
        },
    }
    value["identity_assertion"] = {
        "verdict": "verified" if exact else "not_applicable",
        "provider": {
            "provider": "example",
            "tenant": "acme",
            "canonical_board_url": BOARD,
            "relationship_verified": True,
        },
        "opening": {
            "provider": "example",
            "tenant": "acme",
            "canonical_board_url": BOARD,
            "canonical_opening_url": OPENING,
        },
        "selection": {
            "provider": "example",
            "tenant": "acme",
            "canonical_board_url": BOARD,
            "canonical_opening_url": OPENING,
        },
    }
    return value


class CandidateRouteRecordTests(unittest.TestCase):
    def test_exact_is_attributed_only_to_routes_with_matching_verified_board(self):
        record = evaluate_candidate_routes(
            result(
                {
                    "external_apply": route(),
                    "provider_search": route(
                        board_url="https://jobs.example.test/another-company"
                    ),
                    "website_career": route(relationship=False),
                }
            )
        )

        self.assertTrue(record["routes"]["external_apply"]["exact_attributable"])
        self.assertFalse(record["routes"]["provider_search"]["exact_attributable"])
        self.assertEqual(
            record["routes"]["provider_search"]["reason"],
            "selected_exact_not_attributable",
        )
        self.assertFalse(record["routes"]["website_career"]["exact_attributable"])
        self.assertEqual(record["exact_bitmask"], 1)
        self.assertTrue(record["union_exact"])

    def test_generic_route_board_can_refine_to_selected_search_board(self):
        route_board = "https://careers.example.test/jobs"
        selected_board = f"{route_board}/search?q=AI+Engineer"
        payload = result(
            {
                "external_apply": route(
                    covered=False, candidate=False, board=False, relationship=False
                ),
                "provider_search": route(
                    candidate=False, board=False, relationship=False
                ),
                "website_career": route(board_url=route_board),
            }
        )
        website_board = payload["trace"]["stages"]["job_board_discovery"][
            "route_evaluation"
        ]["routes"]["website_career"]["verified_relationship_boards"][0]
        website_board.update(
            {"provider": "generic", "tenant": f"url:{route_board}"}
        )
        payload["job_list_page_url"] = selected_board
        for identity_name in ("provider", "opening", "selection"):
            identity = payload["identity_assertion"][identity_name]
            identity.update(
                {
                    "provider": "generic",
                    "tenant": f"url:{selected_board}",
                    "canonical_board_url": selected_board,
                }
            )

        record = evaluate_candidate_routes(payload)

        self.assertTrue(record["routes"]["website_career"]["exact_attributable"])
        self.assertEqual(record["exact_bitmask"], 4)

    def test_generic_route_refinement_rejects_wrong_identity_boundaries(self):
        route_board = "https://careers.example.test/departments/engineering"
        selected_board = f"{route_board}/search?q=AI+Engineer"
        invalid_boards = {
            "wrong_provider": {
                "provider": "other",
                "tenant": f"url:{route_board}",
                "url": route_board,
            },
            "forged_tenant": {
                "provider": "generic",
                "tenant": "url:https://careers.example.test/departments/sales",
                "url": route_board,
            },
            "wrong_host": {
                "provider": "generic",
                "tenant": "url:https://jobs.other.test/departments/engineering",
                "url": "https://jobs.other.test/departments/engineering",
            },
            "sibling_route": {
                "provider": "generic",
                "tenant": "url:https://careers.example.test/departments/sales",
                "url": "https://careers.example.test/departments/sales",
            },
        }
        for case, invalid_board in invalid_boards.items():
            with self.subTest(case=case):
                payload = result({name: route() for name in (
                    "external_apply", "provider_search", "website_career"
                )})
                payload["job_list_page_url"] = selected_board
                for identity_name in ("provider", "opening", "selection"):
                    identity = payload["identity_assertion"][identity_name]
                    identity.update(
                        {
                            "provider": "generic",
                            "tenant": f"url:{selected_board}",
                            "canonical_board_url": selected_board,
                        }
                    )
                website_board = payload["trace"]["stages"]["job_board_discovery"][
                    "route_evaluation"
                ]["routes"]["website_career"]["verified_relationship_boards"][0]
                website_board.update(invalid_board)

                record = evaluate_candidate_routes(payload)

                website = record["routes"]["website_career"]
                self.assertFalse(website["exact_attributable"])
                self.assertEqual(website["reason"], "selected_exact_not_attributable")

    def test_final_exact_and_search_snippet_do_not_imply_route_success(self):
        search = route(candidate=False, board=False, relationship=False)
        search["search"] = {
            "queries": [
                {
                    "candidates": [
                        {"url": OPENING, "snippet": "AI Engineer at Acme"}
                    ]
                }
            ]
        }
        record = evaluate_candidate_routes(
            result(
                {
                    "external_apply": route(covered=False, candidate=False, board=False, relationship=False),
                    "provider_search": search,
                    "website_career": route(covered=False, candidate=False, board=False, relationship=False),
                }
            )
        )

        provider_search = record["routes"]["provider_search"]
        self.assertFalse(provider_search["candidate_produced"])
        self.assertFalse(provider_search["exact_attributable"])
        self.assertEqual(provider_search["reason"], "candidate_not_produced")
        self.assertFalse(record["union_exact"])

    def test_modern_exact_requires_typed_selection_continuity(self):
        payload = result({name: route() for name in (
            "external_apply", "provider_search", "website_career"
        )})
        payload["identity_assertion"]["selection"] = None

        record = evaluate_candidate_routes(payload)

        self.assertFalse(record["union_exact"])
        self.assertTrue(all(
            item["reason"] == "selected_exact_not_attributable"
            for item in record["routes"].values()
        ))

    def test_malformed_trace_fails_closed(self):
        malformed = route()
        malformed["relationship_verified_count"] = "yes"
        record = evaluate_candidate_routes(
            result(
                {
                    "external_apply": malformed,
                    "provider_search": route(),
                    # A benchmark trace must report every route.
                }
            )
        )

        self.assertTrue(record["malformed_trace"])
        self.assertTrue(record["routes"]["external_apply"]["malformed_trace"])
        self.assertFalse(record["routes"]["external_apply"]["exact_attributable"])
        self.assertTrue(record["routes"]["website_career"]["malformed_trace"])
        self.assertIn(
            "external_apply:relationship_verified_count_invalid",
            record["malformed_reasons"],
        )

    def test_missing_job_board_trace_is_malformed_not_empty_legacy(self):
        payload = result({})
        payload["trace"] = {"stages": {}}

        record = evaluate_candidate_routes(payload)

        self.assertTrue(record["malformed_trace"])
        self.assertEqual(record["malformed_reasons"], ["job_board_trace_missing"])
        self.assertFalse(record["union_exact"])
        self.assertTrue(all(
            item["reason"] == "malformed_trace"
            for item in record["routes"].values()
        ))

    def test_legacy_s5_trace_is_website_only_and_can_be_exact(self):
        payload = result({})
        payload["identity_assertion"]["selection"] = None
        payload["trace"] = {
            "steps": [
                {
                    "name": "find_job_board",
                    "method": "career_page_scan",
                    "job_list_page_url": BOARD,
                    "provider_detection": {
                        "provider": "example",
                        "url": BOARD,
                    },
                }
            ]
        }

        record = evaluate_candidate_routes(payload)

        self.assertEqual(record["trace_mode"], "legacy_website")
        self.assertTrue(record["routes"]["website_career"]["exact_attributable"])
        self.assertEqual(
            record["routes"]["external_apply"]["reason"],
            "legacy_route_not_evaluated",
        )
        self.assertEqual(record["exact_bitmask"], 4)

    def test_augmented_legacy_website_board_does_not_require_pool_candidate(self):
        routes = {
            "external_apply": route(
                covered=False, candidate=False, board=False, relationship=False
            ),
            "provider_search": route(
                covered=True, candidate=False, board=False, relationship=False
            ),
            "website_career": route(candidate=False),
        }
        routes["website_career"]["legacy_status"] = "success"

        record = evaluate_candidate_routes(result(routes))

        website = record["routes"]["website_career"]
        self.assertFalse(record["malformed_trace"])
        self.assertTrue(website["candidate_produced"])
        self.assertTrue(website["provider_tenant_board_verified"])
        self.assertTrue(website["relationship_verified"])
        self.assertTrue(website["exact_attributable"])

    def test_selected_legacy_website_route_uses_final_typed_board_identity(self):
        routes = {
            "external_apply": route(
                covered=False, candidate=False, board=False, relationship=False
            ),
            "provider_search": route(candidate=False, board=False, relationship=False),
            "website_career": route(candidate=False, board=False, relationship=False),
        }
        routes["website_career"]["legacy_status"] = "success"
        payload = result(routes)
        payload["trace"]["stages"]["job_board_discovery"][
            "candidate_route_probe"
        ] = {"candidate_verification": {"verified_candidate_count": 0}}

        record = evaluate_candidate_routes(payload)

        website = record["routes"]["website_career"]
        self.assertTrue(website["candidate_produced"])
        self.assertTrue(website["provider_tenant_board_verified"])
        self.assertTrue(website["relationship_verified"])
        self.assertTrue(website["exact_attributable"])

    def test_legacy_external_apply_method_is_not_credited_to_website(self):
        payload = result({})
        payload["identity_assertion"]["selection"] = None
        payload["trace"] = {
            "stages": {
                "job_board_discovery": {
                    "method": "external_apply_url",
                    "job_list_page_url": BOARD,
                }
            }
        }

        record = evaluate_candidate_routes(payload)

        self.assertFalse(record["routes"]["website_career"]["candidate_produced"])
        self.assertFalse(record["union_exact"])


class CandidateRouteAggregateTests(unittest.TestCase):
    def test_aggregate_reports_denominators_rates_reasons_and_venn_overlaps(self):
        all_routes = result({name: route() for name in (
            "external_apply", "provider_search", "website_career"
        )})
        external_only = result(
            {
                "external_apply": route(),
                "provider_search": route(candidate=False, board=False, relationship=False),
                "website_career": route(covered=False, candidate=False, board=False, relationship=False),
            }
        )
        no_exact = result(
            {
                "external_apply": route(),
                "provider_search": route(),
                "website_career": route(),
            },
            exact=False,
        )

        metrics = aggregate_candidate_route_metrics(
            [all_routes, (external_only, external_only["trace"]), no_exact]
        )

        external = metrics["routes"]["external_apply"]
        self.assertEqual(external["input_coverage"], {
            "count": 3, "denominator": 3, "rate": 1.0
        })
        self.assertEqual(external["exact_attributable"]["count"], 2)
        self.assertEqual(external["exact_attributable"]["denominator"], 3)
        self.assertEqual(metrics["union_exact"]["count"], 2)
        self.assertEqual(metrics["overlap_bitmask_counts"]["111"], 1)
        self.assertEqual(metrics["overlap_bitmask_counts"]["001"], 1)
        self.assertEqual(metrics["overlap_bitmask_counts"]["000"], 1)
        self.assertEqual(len(metrics["overlaps"]), 8)
        self.assertEqual(
            metrics["routes"]["provider_search"]["reason_counts"]["candidate_not_produced"],
            1,
        )
        json.dumps(metrics)

    def test_empty_aggregate_has_explicit_zero_denominators(self):
        metrics = aggregate_candidate_route_metrics([])

        self.assertEqual(metrics["record_count"], 0)
        self.assertEqual(metrics["union_exact"]["denominator"], 0)
        self.assertIsNone(metrics["union_exact"]["rate"])
        self.assertEqual(metrics["overlap_bitmask_counts"]["000"], 0)


if __name__ == "__main__":
    unittest.main()
