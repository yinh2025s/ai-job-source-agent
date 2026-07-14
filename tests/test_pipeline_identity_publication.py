import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    ProviderIdentity,
)
from job_source_agent.models import (
    CompanyInput,
    StageResult,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
)
from job_source_agent.pipeline_application import discovery_result_from_context


class PipelineIdentityPublicationTests(unittest.TestCase):
    def test_rejected_exact_candidate_is_not_published(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Fresh Ventures"))
        context.job_list_page_url = "https://jobs.ashbyhq.com/notion"
        context.open_position_url = "https://jobs.ashbyhq.com/notion/role-123"
        context.stage_results = [
            StageResult(stage=STAGE_OPENING_MATCH, status="success"),
            StageResult(
                stage=STAGE_RESULT_VALIDATION,
                status="failed",
                reason_code="RESULT_IDENTITY_MISMATCH",
            ),
        ]
        context.trace["stages"] = {
            STAGE_RESULT_VALIDATION: {
                "pipeline_status": "success",
                "issues": ["PROVIDER_RELATIONSHIP_UNVERIFIED"],
            }
        }

        result = discovery_result_from_context(context)

        self.assertIsNone(result.open_position_url)
        self.assertEqual(result.pipeline_status, "failed")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.identity_assertion["verdict"], "rejected")
        self.assertEqual(
            result.identity_assertion["candidate_opening_url"],
            "https://jobs.ashbyhq.com/notion/role-123",
        )

    def test_verified_exact_candidate_is_published(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://acme.example/careers",
        )
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="lever",
            tenant="acme",
            canonical_board_url="https://jobs.lever.co/acme",
            evidence_url="https://jobs.lever.co/acme",
            verification_method="tenant_name_match",
            relationship_verified=True,
        )
        context.opening_identity = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="lever",
            tenant="acme",
            canonical_board_url="https://jobs.lever.co/acme",
            canonical_opening_url="https://jobs.lever.co/acme/role-123",
        )
        context.job_list_page_url = "https://jobs.lever.co/acme"
        context.open_position_url = "https://jobs.lever.co/acme/role-123"
        context.stage_results = [
            StageResult(stage=STAGE_OPENING_MATCH, status="success"),
            StageResult(stage=STAGE_RESULT_VALIDATION, status="success"),
        ]
        context.trace["stages"] = {
            STAGE_RESULT_VALIDATION: {"pipeline_status": "success", "issues": []}
        }

        result = discovery_result_from_context(context)

        self.assertEqual(result.open_position_url, "https://jobs.lever.co/acme/role-123")
        self.assertEqual(result.pipeline_status, "success")
        self.assertEqual(result.identity_assertion["verdict"], "verified")


if __name__ == "__main__":
    unittest.main()
