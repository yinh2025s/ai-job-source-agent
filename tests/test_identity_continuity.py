import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    HiringRelationshipEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
    ProviderOpeningRouteEvidence,
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
            evidence_url="https://acme.example/careers",
            verification_method="verified_first_party_handoff",
            relationship_verified=True,
        )
        self.opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            canonical_opening_url="https://jobs.ashbyhq.com/acme/role-123",
        )

    def test_tenant_name_only_chain_fails_closed(self):
        provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="ashby",
            tenant="acme",
            canonical_board_url="https://jobs.ashbyhq.com/acme",
            evidence_url="https://jobs.ashbyhq.com/acme",
            verification_method="tenant_name_match",
            relationship_verified=True,
        )
        self.assertEqual(
            validate_opening_identity_chain(
                hiring=self.hiring,
                provider=provider,
                opening=self.opening,
                open_position_url="https://jobs.ashbyhq.com/acme/role-123",
            ),
            ["PROVIDER_RELATIONSHIP_PROVENANCE_MISSING"],
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

    def test_verified_provider_route_authorizes_exact_child_identity(self):
        provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="icims",
            tenant="aggregate.icims.com",
            canonical_board_url="https://aggregate.icims.com/jobs/search",
            evidence_url="https://acme.example/careers",
            verification_method="verified_first_party_handoff",
            relationship_verified=True,
        )
        route = ProviderOpeningRouteEvidence(
            provider="icims",
            source_tenant="aggregate.icims.com",
            source_canonical_board_url="https://aggregate.icims.com/jobs/search",
            target_tenant="child.icims.com",
            target_canonical_board_url="https://child.icims.com/jobs/search",
            canonical_opening_url="https://child.icims.com/jobs/123/engineer/job",
            opening_id="123",
            source_response_url=(
                "https://aggregate.icims.com/jobs/search?hub=15&ss=1"
            ),
            source_customer_identity="acme.icims.com",
            target_customer_identity="acme.icims.com",
            route_identity="hub:15",
            detail_evidence_url="https://child.icims.com/jobs/123/engineer/job",
            extraction_method="icims_aggregate_job_card",
            detail_verified=True,
        )
        opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="icims",
            tenant="child.icims.com",
            canonical_board_url="https://child.icims.com/jobs/search",
            canonical_opening_url="https://child.icims.com/jobs/123/engineer/job",
            route_evidence=route,
        )

        self.assertEqual(
            validate_opening_identity_chain(
                hiring=self.hiring,
                provider=provider,
                opening=opening,
                open_position_url=opening.canonical_opening_url,
            ),
            [],
        )
        self.assertEqual(
            OpeningIdentity.from_checkpoint_payload(
                opening.to_checkpoint_payload()
            ),
            opening,
        )

    def test_provider_route_rejects_cross_tenant_and_tampered_evidence(self):
        provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="icims",
            tenant="aggregate.icims.com",
            canonical_board_url="https://aggregate.icims.com/jobs/search",
            evidence_url="https://acme.example/careers",
            verification_method="verified_first_party_handoff",
            relationship_verified=True,
        )
        route = ProviderOpeningRouteEvidence(
            provider="icims",
            source_tenant="other.icims.com",
            source_canonical_board_url="https://other.icims.com/jobs/search",
            target_tenant="wrong-child.icims.com",
            target_canonical_board_url="https://wrong-child.icims.com/jobs/search",
            canonical_opening_url="https://wrong-child.icims.com/jobs/999/engineer/job",
            opening_id="999",
            source_response_url="https://other.icims.com/jobs/search?hub=26",
            source_customer_identity="other.icims.com",
            target_customer_identity="other.icims.com",
            route_identity="hub:26",
            detail_evidence_url="https://wrong-child.icims.com/jobs/999/engineer/job",
            extraction_method="icims_aggregate_job_card",
            detail_verified=True,
        )
        opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="icims",
            tenant="child.icims.com",
            canonical_board_url="https://child.icims.com/jobs/search",
            canonical_opening_url="https://child.icims.com/jobs/123/engineer/job",
            route_evidence=route,
        )

        failures = validate_opening_identity_chain(
            hiring=self.hiring,
            provider=provider,
            opening=opening,
            open_position_url=opening.canonical_opening_url,
        )

        self.assertIn("OPENING_ROUTE_SOURCE_MISMATCH", failures)
        self.assertIn("OPENING_ROUTE_TARGET_MISMATCH", failures)
        self.assertIn("OPENING_ROUTE_URL_MISMATCH", failures)

    def test_provider_route_payload_rejects_unknown_or_mutated_fields(self):
        route = ProviderOpeningRouteEvidence(
            provider="icims",
            source_tenant="aggregate.icims.com",
            source_canonical_board_url="https://aggregate.icims.com/jobs/search",
            target_tenant="child.icims.com",
            target_canonical_board_url="https://child.icims.com/jobs/search",
            canonical_opening_url="https://child.icims.com/jobs/123/engineer/job",
            opening_id="123",
            source_response_url="https://aggregate.icims.com/jobs/search?hub=15",
            source_customer_identity="acme.icims.com",
            target_customer_identity="acme.icims.com",
            route_identity="hub:15",
            detail_evidence_url="https://child.icims.com/jobs/123/engineer/job",
            extraction_method="icims_aggregate_job_card",
            detail_verified=True,
        )
        payload = route.to_checkpoint_payload()
        payload["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            ProviderOpeningRouteEvidence.from_checkpoint_payload(payload)

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
