import unittest

from job_source_agent.candidate_portfolio import CompositeCandidateDiscovery
from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import HiringIdentityEvidence
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.models import CompanyInput
from job_source_agent.provider_candidates import CandidateDiscoveryResult, ProviderCandidate
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages.discovery import JobBoardDiscoveryStage


CAREER_PAGE = "https://careers.playstation.com"


class _StaticDiscovery:
    candidate_wave = "search"

    def __init__(self, *candidates):
        self.candidates = candidates

    def discover(self, request):
        return CandidateDiscoveryResult(tuple(self.candidates), {"source": "test"})


def _candidate(tenant, rank=1):
    return ProviderCandidate(
        url=f"https://job-boards.greenhouse.io/{tenant}",
        source_kind="targeted_board_search",
        source_url="https://www.bing.com/search?q=sony+jobs",
        company_name="Sony Interactive Entertainment",
        target_title="Software Engineer I",
        provider_hint="greenhouse",
        query='site:job-boards.greenhouse.io "Sony Interactive Entertainment"',
        result_rank=rank,
    )


def _first_party_board(tenant):
    return DiscoveredJobBoard(
        board=JobBoard(
            f"https://job-boards.greenhouse.io/{tenant}",
            "greenhouse",
            tenant,
        ),
        detection_method="linked_url_evidence",
        evidence_url=f"https://job-boards.greenhouse.io/{tenant}",
        relationship_evidence_url=CAREER_PAGE,
    )


def _context():
    context = PipelineContext.from_company(
        CompanyInput(
            company_name="Sony Interactive Entertainment",
            job_title="Software Engineer I",
        )
    )
    context.career_page_url = CAREER_PAGE
    context.hiring_identity_evidence = HiringIdentityEvidence(
        source_company_name="Sony Interactive Entertainment",
        hiring_entity_name="Sony Interactive Entertainment",
        relationship_type="same_entity",
        verification_method="same_entity",
        verified=True,
        evidence_url=CAREER_PAGE,
    )
    return context


class _SonyPortfolioService:
    def find_job_board_portfolio(self, *args, **kwargs):
        boards = tuple(
            _first_party_board(tenant)
            for tenant in (
                "haven",
                "sonyinteractiveentertainmentglobal",
                "siei",
                "teamlfg",
            )
        )
        portfolio = JobBoardPortfolio(boards, True)
        return boards[0].board.url, {"provider": "greenhouse"}, portfolio


class MultiTenantDiscoveryTests(unittest.TestCase):
    def test_sony_prefers_entity_alias_and_retains_first_party_brand_boards(self):
        stage = JobBoardDiscoveryStage(
            _SonyPortfolioService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_StaticDiscovery(_candidate("siei")),),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
            evaluate_all_candidate_routes=True,
        )

        execution = stage.run(_context())

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "tenant_name_match",
        )
        self.assertEqual(
            {board.board.identifier for board in execution.updates["job_board_portfolio"].boards},
            {"sonyinteractiveentertainmentglobal", "siei", "teamlfg", "haven"},
        )

    def test_acronym_and_legal_suffix_are_positive_entity_aliases(self):
        for tenant in ("sie", "siei"):
            with self.subTest(tenant=tenant):
                stage = JobBoardDiscoveryStage(
                    _SonyPortfolioService(),
                    DEFAULT_PROVIDER_REGISTRY,
                    candidate_discovery=CompositeCandidateDiscovery(
                        (_StaticDiscovery(_candidate(tenant)),),
                        limit=12,
                    ),
                    enable_parallel_candidate_discovery=True,
                )

                execution = stage.run(PipelineContext.from_company(
                    CompanyInput(company_name="Sony Interactive Entertainment")
                ))

                self.assertTrue(
                    execution.updates["provider_identity"].relationship_verified
                )
                self.assertEqual(
                    execution.updates["provider_identity"].verification_method,
                    "provider_tenant_match",
                )

    def test_shared_parent_token_does_not_authorize_cross_tenant_search(self):
        stage = JobBoardDiscoveryStage(
            _SonyPortfolioService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_StaticDiscovery(_candidate("sony-pictures")),),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        )

        execution = stage.run(PipelineContext.from_company(
            CompanyInput(company_name="Sony Interactive Entertainment")
        ))

        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "linked_url_only",
        )


if __name__ == "__main__":
    unittest.main()
