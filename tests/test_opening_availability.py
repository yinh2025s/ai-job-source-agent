import unittest

from job_source_agent.opening_availability import diagnose_opening_availability


class OpeningAvailabilityTests(unittest.TestCase):
    def test_verified_title_with_location_rejection_reports_location_no_match(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "generic_html",
                        "scope": "filtered",
                        "status": "verified",
                        "complete": True,
                        "candidate_count": 64,
                        "strongest_title_score": 150,
                    }
                },
                "location_unverified_candidate_rejected": {
                    "candidate_location": "United States",
                    "target_location": "Greater Tampa Bay Area",
                },
            }
        )

        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(diagnostic.evidence["match_basis"], "title_location_no_match")
        self.assertIn("none matched the target location", diagnostic.detail)

    def test_verified_inventory_without_title_match_is_not_called_closed(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "verified",
                        "complete": True,
                        "candidate_count": 12,
                        "strongest_title_score": 30,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_no_match")
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(diagnostic.confidence, "medium")

    def test_valid_empty_provider_inventory_has_business_reason(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "provider_api",
                        "status": "verified_empty",
                        "complete": True,
                        "candidate_count": 0,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_empty")
        self.assertEqual(diagnostic.reason_code, "NO_PUBLIC_OPENINGS")

    def test_empty_title_filtered_inventory_means_no_match_not_no_openings(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "verified_filtered_empty",
                        "scope": "title_filtered",
                        "complete": True,
                        "candidate_count": 0,
                        "strongest_title_score": 0,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_no_match")
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(diagnostic.evidence["inventory_scope"], "title_filtered")

    def test_verified_status_without_complete_inventory_remains_inconclusive(self):
        inventory_cases = (
            ("verified", 12),
            ("verified_filtered_empty", 0),
            ("verified_empty", 0),
        )

        for status, candidate_count in inventory_cases:
            for complete in (None, False, 1):
                with self.subTest(status=status, complete=complete):
                    inventory = {
                        "source": "native_adapter",
                        "status": status,
                        "candidate_count": candidate_count,
                    }
                    if complete is not None:
                        inventory["complete"] = complete

                    diagnostic = diagnose_opening_availability(
                        {"provider_api": {"inventory": inventory}}
                    )

                    self.assertEqual(diagnostic.disposition, "discovery_incomplete")
                    self.assertEqual(
                        diagnostic.reason_code,
                        "OPENING_DISCOVERY_INCOMPLETE",
                    )
                    self.assertEqual(diagnostic.confidence, "low")

    def test_incomplete_verified_inventory_preserves_typed_provider_reason(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "verified_empty",
                        "complete": False,
                        "candidate_count": 0,
                        "reason_code": "FETCH_BUDGET_EXHAUSTED",
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "FETCH_BUDGET_EXHAUSTED")

    def test_provider_failure_remains_inconclusive(self):
        diagnostic = diagnose_opening_availability(
            {"provider_api": {"errors": [{"error": "timeout"}]}}
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "NETWORK_TIMEOUT")
        self.assertEqual(diagnostic.evidence["provider_error_count"], 1)
        self.assertEqual(
            diagnostic.evidence["provider_failure_reason"],
            "NETWORK_TIMEOUT",
        )
        self.assertEqual(
            diagnostic.evidence["provider_errors"],
            [{"error": "timeout", "provenance": ["provider_api"]}],
        )

    def test_http_forbidden_is_not_reported_as_an_opening_miss(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [
                        {
                            "url": "https://jobs.example.test/",
                            "error": "HTTP Error 403: Forbidden",
                        }
                    ]
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "HTTP_FORBIDDEN")
        self.assertEqual(
            diagnostic.evidence["provider_failure_reason"],
            "HTTP_FORBIDDEN",
        )

    def test_typed_bot_protection_beats_generic_error_text(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [
                        {
                            "error": "request rejected",
                            "reason_code": "BOT_PROTECTION",
                            "status": 403,
                        }
                    ]
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "BOT_PROTECTION")

    def test_typed_forbidden_without_error_text_is_preserved(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [
                        {"reason_code": "HTTP_FORBIDDEN", "status": 403}
                    ]
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "HTTP_FORBIDDEN")

    def test_verified_official_empty_inventory_beats_blocked_generic_fallback(self):
        diagnostic = diagnose_opening_availability(
            {
                "errors": [
                    {
                        "error": "HTTP Error 403: Forbidden",
                        "reason_code": "HTTP_FORBIDDEN",
                        "status": 403,
                    }
                ],
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "verified_empty",
                        "complete": True,
                        "candidate_count": 0,
                    }
                },
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_empty")
        self.assertEqual(diagnostic.reason_code, "NO_PUBLIC_OPENINGS")

    def test_verified_closed_source_beats_forbidden_discovery(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [
                        {"reason_code": "HTTP_FORBIDDEN", "status": 403}
                    ]
                }
            },
            {"linkedin_posting": {"availability": "closed"}},
        )

        self.assertEqual(diagnostic.disposition, "source_posting_closed")
        self.assertEqual(diagnostic.reason_code, "OPENING_CLOSED")

    def test_inaccessible_source_is_not_reported_as_closed_or_no_public(self):
        diagnostic = diagnose_opening_availability(
            {},
            {"linkedin_posting": {"availability": "unavailable"}},
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "OPENING_DISCOVERY_INCOMPLETE")

    def test_missing_replay_fixture_is_not_reported_as_a_network_failure(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [
                        {
                            "url": "https://jobs.example.test/search/jobs.json",
                            "error": (
                                "No fixture found for "
                                "https://jobs.example.test/search/jobs.json"
                            ),
                        }
                    ]
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "OFFLINE_FIXTURE_MISSING")
        self.assertEqual(
            diagnostic.evidence["provider_failure_reason"],
            "OFFLINE_FIXTURE_MISSING",
        )

    def test_adapter_reason_is_preserved_without_an_error_string(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "incomplete",
                        "complete": False,
                        "reason_code": "BOT_PROTECTION",
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "BOT_PROTECTION")
        self.assertEqual(diagnostic.evidence["provider_error_count"], 0)
        self.assertEqual(
            diagnostic.evidence["provider_failure_reason"],
            "BOT_PROTECTION",
        )

    def test_specific_adapter_reason_beats_generic_fetch_error(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [{"error": "request failed"}],
                    "inventory": {
                        "status": "incomplete",
                        "reason_code": "PROVIDER_VARIANT_UNSUPPORTED",
                    },
                }
            }
        )

        self.assertEqual(
            diagnostic.reason_code,
            "PROVIDER_VARIANT_UNSUPPORTED",
        )

    def test_generic_search_errors_are_counted_with_provenance(self):
        diagnostic = diagnose_opening_availability(
            {
                "errors": [
                    {"url": "https://jobs.example.test/?q=engineer", "error": "timeout"},
                    {"url": "https://jobs.example.test/?search=engineer", "error": "timeout"},
                ],
                "provider_api": {"provider": "generic", "errors": []},
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.evidence["provider_error_count"], 2)
        self.assertEqual(
            [error["provenance"] for error in diagnostic.evidence["provider_errors"]],
            [["generic_search"], ["generic_search"]],
        )

    def test_duplicate_errors_are_merged_without_losing_provenance(self):
        repeated = {"url": "https://api.example.test/jobs", "error": "timeout"}
        diagnostic = diagnose_opening_availability(
            {
                "errors": [repeated],
                "provider_api": {
                    "errors": [repeated],
                    "adapter_trace": {
                        "errors": [repeated],
                        "error": "invalid response",
                    },
                    "provider_detection": {"error": "board lookup failed"},
                },
            }
        )

        self.assertEqual(diagnostic.evidence["provider_error_count"], 3)
        self.assertEqual(
            diagnostic.evidence["provider_errors"],
            [
                {
                    **repeated,
                    "provenance": ["generic_search", "provider_api", "provider_adapter"],
                },
                {"error": "invalid response", "provenance": ["provider_adapter"]},
                {"error": "board lookup failed", "provenance": ["provider_detection"]},
            ],
        )

    def test_provider_error_prevents_verified_no_match_classification(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "errors": [{"url": "https://api.example.test/page/2", "error": "timeout"}],
                    "inventory": {
                        "source": "provider_api",
                        "status": "verified",
                        "complete": True,
                        "candidate_count": 20,
                        "strongest_title_score": 30,
                    },
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.confidence, "low")
        self.assertEqual(diagnostic.evidence["provider_error_count"], 1)

    def test_explicit_source_status_is_required_for_closed_reason(self):
        diagnostic = diagnose_opening_availability(
            {},
            {"linkedin_posting": {"status": "expired"}},
        )

        self.assertEqual(diagnostic.disposition, "source_posting_closed")
        self.assertEqual(diagnostic.reason_code, "OPENING_CLOSED")
        self.assertEqual(diagnostic.confidence, "high")

    def test_unrelated_nested_status_is_not_treated_as_source_evidence(self):
        diagnostic = diagnose_opening_availability({}, {"resume": {"status": "expired"}})

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")


if __name__ == "__main__":
    unittest.main()
