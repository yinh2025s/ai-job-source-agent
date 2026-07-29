import unittest

from scripts.audit_exact_identities import (
    ExactIdentityAuditError,
    audit_exact_identities,
)


def exact_record() -> dict:
    board = "https://jobs.example.test/board"
    opening = "https://jobs.example.test/board/jobs/123"
    return {
        "company_name": "Example Company",
        "linkedin_job_title": "Software Engineer",
        "linkedin_job_location": "Seattle, WA",
        "pipeline_status": "success",
        "output_validation_status": "success",
        "open_position_url": opening,
        "identity_assertion": {
            "verdict": "verified",
            "failure_codes": [],
            "hiring": {
                "source_company_name": "Example Company",
                "hiring_entity_name": "Example Company",
                "verified": True,
            },
            "provider": {
                "hiring_entity_name": "Example Company",
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "relationship_verified": True,
            },
            "opening": {
                "hiring_entity_name": "Example Company",
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "canonical_opening_url": opening,
            },
            "selection": {
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "canonical_opening_url": opening,
                "title": "Software Engineer",
                "location": "Seattle, WA",
            },
            "candidate_opening_url": opening,
            "location_classification": "exact",
        },
    }


class AuditExactIdentitiesTests(unittest.TestCase):
    def test_accepts_complete_verified_chain(self):
        report = audit_exact_identities([exact_record()], require_exact_count=1)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["passed_count"], 1)
        self.assertEqual(report["failed_count"], 0)

    def test_rejects_cross_tenant_and_unsafe_url(self):
        record = exact_record()
        record["open_position_url"] = "http://127.0.0.1/jobs/123"
        record["identity_assertion"]["opening"]["tenant"] = "other-tenant"
        report = audit_exact_identities([record])
        self.assertEqual(report["status"], "failed")
        issues = set(report["records"][0]["issues"])
        self.assertIn("tenant_continuity_mismatch", issues)
        self.assertIn("opening_url_continuity_mismatch", issues)
        self.assertIn("opening_url_not_safe_public_https", issues)

    def test_rejects_unverified_location_and_company(self):
        record = exact_record()
        record["identity_assertion"]["location_classification"] = "missing"
        record["identity_assertion"]["hiring"]["source_company_name"] = "Other Company"
        report = audit_exact_identities([record])
        issues = set(report["records"][0]["issues"])
        self.assertIn("location_not_verified", issues)
        self.assertIn("source_company_continuity_mismatch", issues)

    def test_accepts_verified_first_party_generic_url_location(self):
        record = exact_record()
        assertion = record["identity_assertion"]
        for section in ("provider", "opening", "selection"):
            assertion[section]["provider"] = "generic"
        assertion["selection"]["location"] = None
        assertion["location_classification"] = "url_qualifier"
        report = audit_exact_identities([record])
        self.assertEqual(report["status"], "passed")

    def test_exact_count_guard_fails_closed(self):
        with self.assertRaisesRegex(ExactIdentityAuditError, "expected 2"):
            audit_exact_identities([exact_record()], require_exact_count=2)

    def test_non_array_trace_is_rejected(self):
        with self.assertRaisesRegex(ExactIdentityAuditError, "JSON array"):
            audit_exact_identities({})


if __name__ == "__main__":
    unittest.main()
