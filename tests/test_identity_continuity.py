import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    HiringRelationshipEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
    validate_opening_identity_chain,
)
from job_source_agent.models import CompanyInput
from job_source_agent.stages import ResultValidationStage


class OpeningIdentityContinuityTests(unittest.TestCase):
    def setUp(self):
        self.hiring = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://acme.example/careers",
        )
        self.provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            evidence_url="https://jobs.ashbyhq.com/acme",
            verification_method="tenant_name_match",
            relationship_verified=True,
        )
        self.opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            canonical_opening_url="https://jobs.ashbyhq.com/acme/role-123",
        )

    def test_complete_same_tenant_chain_passes(self):
        self.assertEqual(
            validate_opening_identity_chain(
                hiring=self.hiring,
                provider=self.provider,
                opening=self.opening,
                open_position_url="https://jobs.ashbyhq.com/acme/role-123",
            ),
            [],
        )

    def test_relationship_contract_rejects_strength_verification_conflict(self):
        with self.assertRaisesRegex(ValueError, "strength conflicts"):
            HiringRelationshipEvidence(
                source_company_name="Acme",
                hiring_entity_name="Acme",
                provider="ashby",
                tenant="acme",
                evidence_type="provider_tenant_match",
                evidence_url="https://jobs.ashbyhq.com/acme",
                strength=20,
                verified=True,
            )

    def test_unverified_board_relationship_fails_without_an_opening(self):
        provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="notion",
            canonical_board_url="https://jobs.ashbyhq.com/notion",
            evidence_url="https://jobs.ashbyhq.com/notion",
            verification_method="linked_url_only",
            relationship_verified=False,
        )

        failures = validate_opening_identity_chain(
            hiring=self.hiring,
            provider=provider,
            opening=None,
            open_position_url=None,
            job_list_page_url=provider.canonical_board_url,
        )

        self.assertEqual(failures, ["PROVIDER_RELATIONSHIP_UNVERIFIED"])

    def test_same_title_on_different_tenant_is_rejected(self):
        wrong_opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="notion",
            canonical_board_url="https://jobs.ashbyhq.com/notion",
            canonical_opening_url="https://jobs.ashbyhq.com/notion/role-123",
        )

        failures = validate_opening_identity_chain(
            hiring=self.hiring,
            provider=self.provider,
            opening=wrong_opening,
            open_position_url="https://jobs.ashbyhq.com/notion/role-123",
        )

        self.assertIn("OPENING_TENANT_MISMATCH", failures)
        self.assertIn("OPENING_BOARD_MISMATCH", failures)

    def test_fresh_ventures_cannot_authorize_notion_tenant(self):
        fresh_hiring = HiringIdentityEvidence(
            source_company_name="Fresh Ventures",
            hiring_entity_name="Fresh Ventures",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://fresh.vc/careers",
        )
        notion_provider = ProviderIdentity(
            hiring_entity_name="Fresh Ventures",
            provider="ashby",
            tenant="notion",
            canonical_board_url="https://jobs.ashbyhq.com/notion",
            evidence_url="https://jobs.ashbyhq.com/notion",
            verification_method="linked_url_only",
            relationship_verified=False,
        )
        notion_opening = OpeningIdentity(
            hiring_entity_name="Fresh Ventures",
            provider="ashby",
            tenant="notion",
            canonical_board_url="https://jobs.ashbyhq.com/notion",
            canonical_opening_url="https://jobs.ashbyhq.com/notion/role-123",
        )

        failures = validate_opening_identity_chain(
            hiring=fresh_hiring,
            provider=notion_provider,
            opening=notion_opening,
            open_position_url="https://jobs.ashbyhq.com/notion/role-123",
        )

        self.assertEqual(failures, ["PROVIDER_RELATIONSHIP_UNVERIFIED"])

    def test_verified_parent_relationship_can_pass(self):
        parent = HiringIdentityEvidence(
            source_company_name="Child Brand",
            hiring_entity_name="Parent Corp",
            relationship_type="brand_parent",
            verification_method="verified_brand_relationship",
            verified=True,
            evidence_url="https://parent.example/careers",
        )
        provider = ProviderIdentity(
            hiring_entity_name="Parent Corp",
            provider="workday",
            tenant="parent/parentcareers",
            canonical_board_url="https://parent.wd5.myworkdayjobs.com/ParentCareers",
            evidence_url="https://parent.wd5.myworkdayjobs.com/ParentCareers",
            verification_method="identity_career_root",
            relationship_verified=True,
        )
        opening = OpeningIdentity(
            hiring_entity_name="Parent Corp",
            provider="workday",
            tenant="parent/parentcareers",
            canonical_board_url="https://parent.wd5.myworkdayjobs.com/ParentCareers",
            canonical_opening_url=(
                "https://parent.wd5.myworkdayjobs.com/ParentCareers/job/role-123"
            ),
        )

        self.assertEqual(
            validate_opening_identity_chain(
                hiring=parent,
                provider=provider,
                opening=opening,
                open_position_url=(
                    "https://parent.wd5.myworkdayjobs.com/ParentCareers/job/role-123"
                ),
            ),
            [],
        )

    def test_s7_fails_closed_when_exact_identity_is_missing(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.open_position_url = "https://jobs.ashbyhq.com/acme/role-123"

        execution = ResultValidationStage().run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "RESULT_IDENTITY_MISMATCH")
        self.assertEqual(
            execution.trace["issues"],
            [
                "HIRING_IDENTITY_MISSING",
                "PROVIDER_IDENTITY_MISSING",
                "OPENING_IDENTITY_MISSING",
            ],
        )

    def test_s7_accepts_complete_identity_chain(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.hiring_identity_evidence = self.hiring
        context.provider_identity = self.provider
        context.opening_identity = self.opening
        context.open_position_url = self.opening.canonical_opening_url
        context.opening_selection_evidence = OpeningSelectionEvidence(
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            canonical_opening_url="https://jobs.ashbyhq.com/acme/role-123",
            title="Software Engineer",
            location=None,
            inventory_scope="full",
            inventory_complete=True,
            candidate_count=1,
        )

        execution = ResultValidationStage().run(context)

        self.assertEqual(execution.result.status, "success")

    def test_s7_rejects_ambiguous_incomplete_selection_without_location(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="Software Engineer",
                job_location="New York, NY",
            )
        )
        context.hiring_identity_evidence = self.hiring
        context.provider_identity = self.provider
        context.opening_identity = self.opening
        context.open_position_url = self.opening.canonical_opening_url
        context.opening_selection_evidence = OpeningSelectionEvidence(
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            canonical_opening_url="https://jobs.ashbyhq.com/acme/role-123",
            title="Software Engineer",
            location=None,
            inventory_scope="unknown",
            inventory_complete=False,
            candidate_count=8,
        )

        execution = ResultValidationStage().run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "RESULT_IDENTITY_MISMATCH")
        self.assertIn("OPENING_LOCATION_UNVERIFIED", execution.trace["issues"])


if __name__ == "__main__":
    unittest.main()
