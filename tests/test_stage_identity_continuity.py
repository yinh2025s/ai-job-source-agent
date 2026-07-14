import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import HiringIdentityEvidence, ProviderIdentity
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import CompanyInput
from job_source_agent.providers import ProviderRegistry
from job_source_agent.stages.discovery import (
    CareerDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
)


class _Adapter:
    name = "example"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://jobs.example/")

    def identify_board(self, url):
        tenant = url.split("/")[3]
        return JobBoard(f"https://jobs.example/{tenant}", self.name, tenant)


class _Service:
    def __init__(self, board, opening=None):
        self.board = board
        self.opening = opening
        self.company_name = None

    def find_career_page(self, website, company_name=None, **kwargs):
        self.company_name = company_name
        return "https://careers.example/jobs", {}

    def find_job_board_with_evidence(self, career_url, company_name=None, **kwargs):
        self.company_name = company_name
        return self.board.board.url, {}, self.board

    def match_discovered_board(self, board, *args):
        return self.opening, board.board.url, {}


def _hiring(source, entity=None, verified=True):
    return HiringIdentityEvidence(
        source_company_name=source,
        hiring_entity_name=entity or source,
        relationship_type="same_entity" if source == (entity or source) else "brand_parent",
        verification_method="same_entity" if source == (entity or source) else "identity_rule",
        verified=verified,
        evidence_url="https://careers.example/jobs",
    )


class StageIdentityContinuityTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry((_Adapter(),))

    def test_s4_uses_resolved_hiring_entity(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Child", company_website_url="https://child.example")
        )
        context.hiring_entity_name = "Parent"
        service = _Service(DiscoveredJobBoard(JobBoard("https://jobs.example/parent", "example", "parent"), "url_evidence", "https://careers.example/jobs"))

        CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.company_name, "Parent")

    def test_s5_marks_unrelated_tenant_unverified_without_company_exception(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/notion", "example", "notion"),
            "linked_url_evidence",
            "https://fresh.example/careers",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Fresh Ventures"))
        context.career_page_url = "https://fresh.example/careers"
        context.hiring_identity_evidence = _hiring("Fresh Ventures")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(execution.updates["provider_identity"].verification_method, "linked_url_only")

    def test_s5_accepts_explicit_verified_parent_relationship(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/parentcorp", "example", "parentcorp"),
            "url_evidence",
            "https://careers.example/jobs",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Child Brand"))
        context.career_page_url = "https://careers.example/jobs"
        context.hiring_identity_evidence = _hiring("Child Brand", "Parent Corp")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        self.assertTrue(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(execution.updates["provider_identity"].verification_method, "tenant_name_match")

    def test_external_apply_preserves_typed_discovered_board(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                external_apply_url="https://jobs.example/acme/jobs/123",
            )
        )

        execution = JobBoardDiscoveryStage(_Service(None), self.registry).run(context)

        self.assertIsInstance(execution.updates["discovered_job_board"], DiscoveredJobBoard)
        self.assertEqual(execution.updates["discovered_job_board"].board.identifier, "acme")

    def test_s6_emits_identity_only_for_same_provider_tenant_and_board(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://careers.example/jobs",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme", job_title="Engineer"))
        context.discovered_job_board = board
        context.job_list_page_url = board.board.url
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme", provider="example", tenant="acme",
            canonical_board_url="https://jobs.example/acme", evidence_url="https://careers.example/jobs",
            verification_method="tenant_name_match", relationship_verified=True,
        )
        execution = OpeningMatchStage(
            _Service(board, "https://jobs.example/acme/jobs/123"), self.registry
        ).run(context)
        self.assertEqual(execution.updates["opening_identity"].tenant, "acme")

        wrong = OpeningMatchStage(
            _Service(board, "https://jobs.example/notion/jobs/123"), self.registry
        ).run(context)
        self.assertNotIn("opening_identity", wrong.updates)


if __name__ == "__main__":
    unittest.main()
