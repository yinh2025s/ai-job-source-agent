import unittest

from job_source_agent.opening_availability import diagnose_opening_availability


class OpeningAvailabilityTests(unittest.TestCase):
    def test_verified_inventory_without_title_match_is_not_called_closed(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "source": "native_adapter",
                        "status": "verified",
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
                        "candidate_count": 0,
                        "strongest_title_score": 0,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_no_match")
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(diagnostic.evidence["inventory_scope"], "title_filtered")

    def test_provider_failure_remains_inconclusive(self):
        diagnostic = diagnose_opening_availability(
            {"provider_api": {"errors": [{"error": "timeout"}]}}
        )

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(diagnostic.evidence["provider_error_count"], 1)
        self.assertEqual(
            diagnostic.evidence["provider_errors"],
            [{"error": "timeout", "provenance": ["provider_api"]}],
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
