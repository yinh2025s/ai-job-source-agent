import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    ProviderIdentity,
)
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import CompanyInput
from job_source_agent.providers import ProviderRegistry
from job_source_agent.stages import (
    CareerDiscoveryStage,
    HiringIdentityResolutionStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    PipelineStageRunner,
    ResultValidationStage,
)


class _Adapter:
    name = "example"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://jobs.example/")

    def identify_board(self, url):
        tenant = url.split("/")[3]
        return JobBoard(f"https://jobs.example/{tenant}", self.name, tenant)


class _DiscoveryService:
    def __init__(self, board, opening_url):
        self.board = board
        self.opening_url = opening_url

    def find_career_page(self, website, company_name=None, **kwargs):
        return self.board.evidence_url, {}

    def find_job_board_with_evidence(self, career_url, company_name=None, **kwargs):
        return self.board.board.url, {}, self.board

    def match_discovered_board(self, board, *args):
        return self.opening_url, board.board.url, {}


class _ResolvedIdentity:
    hiring_entity_name = "Parent Holdings"
    career_root_url = "https://jobs.example/parent"
    official_website_url = "https://parent.example"
    relationship_type = "acquired_brand"
    relationship_verified = True
    verification_method = "verified_acquisition_evidence"
    evidence_url = "https://parent.example/brands/fresh-legacy"


class _IdentityResolver:
    def resolve(self, *args):
        return _ResolvedIdentity(), {
            "selected": {"career_root_url": _ResolvedIdentity.career_root_url}
        }


class IdentityPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry((_Adapter(),))

    def test_fresh_ventures_notion_candidate_is_suppressed_by_s7(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/notion", "example", "notion"),
            "linked_url_evidence",
            "https://fresh.example/careers",
        )
        service = _DiscoveryService(
            board,
            "https://jobs.example/notion/jobs/software-engineer",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Fresh Ventures", job_title="Software Engineer")
        )
        context.career_page_url = "https://fresh.example/careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Fresh Ventures",
            hiring_entity_name="Fresh Ventures",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://fresh.example/careers",
        )

        PipelineStageRunner(
            [
                JobBoardDiscoveryStage(service, self.registry),
                OpeningMatchStage(service, self.registry),
                ResultValidationStage(),
            ]
        ).run(context)

        self.assertFalse(context.provider_identity.relationship_verified)
        self.assertIsInstance(context.opening_identity, OpeningIdentity)
        self.assertEqual(context.stage_results[-1].status, "failed")
        self.assertEqual(
            context.stage_results[-1].reason_code,
            "RESULT_IDENTITY_MISMATCH",
        )
        self.assertEqual(
            context.trace["stages"]["result_validation"]["issues"],
            ["PROVIDER_RELATIONSHIP_UNVERIFIED"],
        )

    def test_verified_acquired_brand_parent_chain_passes_s3_through_s7(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/parent", "example", "parent"),
            "acquired_brand_handoff",
            "https://parent.example/brands/fresh-legacy",
        )
        service = _DiscoveryService(
            board,
            "https://jobs.example/parent/jobs/software-engineer",
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Fresh Legacy",
                company_website_url="https://fresh-legacy.example",
                job_title="Software Engineer",
            )
        )

        PipelineStageRunner(
            [
                HiringIdentityResolutionStage(_IdentityResolver()),
                CareerDiscoveryStage(service),
                JobBoardDiscoveryStage(service, self.registry),
                OpeningMatchStage(service, self.registry),
                ResultValidationStage(),
            ]
        ).run(context)

        self.assertIsInstance(context.hiring_identity_evidence, HiringIdentityEvidence)
        self.assertEqual(
            context.hiring_identity_evidence.relationship_type,
            "acquired_brand",
        )
        self.assertTrue(context.hiring_identity_evidence.verified)
        self.assertEqual(
            context.hiring_identity_evidence.hiring_entity_name,
            "Parent Holdings",
        )
        self.assertIsInstance(context.provider_identity, ProviderIdentity)
        self.assertTrue(context.provider_identity.relationship_verified)
        self.assertEqual(context.provider_identity.tenant, "parent")
        self.assertIsInstance(context.opening_identity, OpeningIdentity)
        self.assertEqual(context.opening_identity.tenant, "parent")
        self.assertEqual(context.stage_results[-1].status, "success")
        self.assertEqual(
            context.trace["stages"]["result_validation"]["issues"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
