import unittest

from job_source_agent.identity_continuity import IDENTITY_CONTRACT_VERSION
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
                "relationship_type": "same_entity",
                "verification_method": "same_entity",
                "verified": True,
                "evidence_url": "https://www.example.test/careers",
                "schema_version": IDENTITY_CONTRACT_VERSION,
            },
            "provider": {
                "hiring_entity_name": "Example Company",
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "evidence_url": board,
                "verification_method": "first_party_inventory",
                "relationship_verified": True,
                "schema_version": IDENTITY_CONTRACT_VERSION,
            },
            "opening": {
                "hiring_entity_name": "Example Company",
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "canonical_opening_url": opening,
                "route_evidence": None,
                "schema_version": IDENTITY_CONTRACT_VERSION,
            },
            "selection": {
                "provider": "example",
                "tenant": "example-tenant",
                "canonical_board_url": board,
                "canonical_opening_url": opening,
                "title": "Software Engineer",
                "location": "Seattle, WA",
                "inventory_scope": "full",
                "inventory_complete": True,
                "candidate_count": 1,
                "schema_version": IDENTITY_CONTRACT_VERSION,
            },
            "candidate_opening_url": opening,
            "location_classification": "exact",
        },
    }


def source_record() -> dict:
    return {
        "company_name": "Example Company",
        "job_title": "Software Engineer",
        "job_location": "Seattle, WA",
        "linkedin_job_url": (
            "https://www.linkedin.com/jobs/view/software-engineer-at-example-1234567890"
        ),
    }


def measurement_records(*, exact: bool = True) -> tuple[list[dict], list[dict]]:
    record = exact_record()
    record["linkedin_job_url"] = source_record()["linkedin_job_url"]
    if not exact:
        record["open_position_url"] = None
        record["pipeline_status"] = "partial"
        record["identity_assertion"] = {
            "verdict": "not_applicable",
            "failure_codes": [],
        }
    result = dict(record)
    trace = {**record, "trace": {"stages": {}}}
    return [result], [trace]


class AuditExactIdentitiesTests(unittest.TestCase):
    def test_accepts_complete_verified_chain(self):
        report = audit_exact_identities([exact_record()], require_exact_count=1)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["passed_count"], 1)
        self.assertEqual(report["failed_count"], 0)
        self.assertFalse(report["measurement_bound"])
        self.assertEqual(report["audit_mode"], "legacy_trace_only")

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

    def test_measurement_binds_cohort_results_and_full_trace(self):
        results, trace = measurement_records()
        report = audit_exact_identities(
            trace,
            cohort_records=[source_record()],
            result_records=results,
            require_exact_count=1,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["measurement_bound"])
        self.assertEqual(report["audit_mode"], "measurement_bound")
        self.assertEqual(report["measurement_binding"]["cohort_record_count"], 1)

    def test_measurement_recomputes_title_contract(self):
        results, trace = measurement_records()
        for record in (results[0], trace[0]):
            record["identity_assertion"]["selection"]["title"] = (
                "Chief Financial Officer"
            )

        report = audit_exact_identities(
            trace,
            cohort_records=[source_record()],
            result_records=results,
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn(
            "opening_title_mismatch",
            report["records"][0]["issues"],
        )

    def test_measurement_recomputes_location_contract(self):
        results, trace = measurement_records()
        for record in (results[0], trace[0]):
            record["identity_assertion"]["selection"]["location"] = "Miami, FL"

        report = audit_exact_identities(
            trace,
            cohort_records=[source_record()],
            result_records=results,
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn(
            "opening_location_mismatch",
            report["records"][0]["issues"],
        )

    def test_measurement_zero_exact_is_explicit_and_passes(self):
        results, trace = measurement_records(exact=False)
        report = audit_exact_identities(
            trace,
            cohort_records=[source_record()],
            result_records=results,
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["exact_count"], 0)
        self.assertTrue(report["measurement_bound"])

    def test_measurement_rejects_result_without_full_trace_binding(self):
        results, trace = measurement_records()
        trace[0]["linkedin_job_title"] = "Different source title"

        with self.assertRaisesRegex(
            ExactIdentityAuditError,
            "does not preserve job_title",
        ):
            audit_exact_identities(
                trace,
                cohort_records=[source_record()],
                result_records=results,
            )

    def test_measurement_requires_both_cohort_and_results(self):
        _results, trace = measurement_records()
        with self.assertRaisesRegex(
            ExactIdentityAuditError,
            "requires both cohort and results",
        ):
            audit_exact_identities(
                trace,
                cohort_records=[source_record()],
            )

    def test_measurement_rejects_lookalike_linkedin_host(self):
        source = source_record()
        source["linkedin_job_url"] = (
            "https://evil-linkedin.com/jobs/view/software-engineer-1234567890"
        )
        results, trace = measurement_records()
        with self.assertRaisesRegex(ExactIdentityAuditError, "URL is invalid"):
            audit_exact_identities(
                trace,
                cohort_records=[source],
                result_records=results,
            )


if __name__ == "__main__":
    unittest.main()
