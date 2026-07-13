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
