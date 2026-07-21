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
        url=f"https://{tenant}.pinpointhq.com/",
        source_kind="targeted_board_search",
        source_url="https://www.bing.com/search?q=sony+jobs",
        company_name="Sony Interactive Entertainment",
        target_title="Software Engineer I",
        provider_hint="pinpoint",
        query='site:pinpointhq.com "Sony Interactive Entertainment"',
        result_rank=rank,
    )


def _first_party_board(tenant):
    return DiscoveredJobBoard(
        board=JobBoard(
            f"https://{tenant}.pinpointhq.com/",
            "pinpoint",
            tenant,
            replay_safe=True,
        ),
        detection_method="linked_url_evidence",
        evidence_url=f"https://{tenant}.pinpointhq.com/",
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
        entity_url = (
            "https://sonyinteractiveentertainmentglobal.hcshiring.com/jobs"
        )
        boards = (
            _first_party_board("haven"),
            DiscoveredJobBoard(
                board=JobBoard(
                    entity_url,
                    "healthcaresource",
                    "sonyinteractiveentertainmentglobal",
                    replay_safe=True,
                ),
                detection_method="linked_url_evidence",
                evidence_url=entity_url,
                relationship_evidence_url=CAREER_PAGE,
            ),
            _first_party_board("siei"),
            _first_party_board("teamlfg"),
        )
        portfolio = JobBoardPortfolio(boards, True)
        original = boards[0]
        return original.board.url, {
            "provider": original.board.provider,
            "job_list_page_url": original.board.url,
            "provider_detection": {
                "method": original.detection_method,
                "provider": original.board.provider,
                "url": original.board.url,
            },
            "job_board_portfolio": {
                "eligible_count": len(boards),
                "eligible_set_complete": True,
                "primary_provider": original.board.provider,
                "primary_url": original.board.url,
            },
        }, portfolio


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
            "https://sonyinteractiveentertainmentglobal.hcshiring.com/jobs",
        )
        expected_url = (
            "https://sonyinteractiveentertainmentglobal.hcshiring.com/jobs"
        )
        self.assertEqual(execution.result.provider, "healthcaresource")
        self.assertEqual(
            execution.result.evidence[0],
            {"field": "job_list_page_url", "url": expected_url},
        )
        self.assertEqual(execution.updates["provider"], "healthcaresource")
        self.assertEqual(
            execution.updates["discovered_job_board"],
            execution.updates["job_board_portfolio"].primary,
        )
        self.assertEqual(
            execution.updates["discovered_job_board"].board.url,
            expected_url,
        )
        self.assertEqual(execution.trace["job_list_page_url"], expected_url)
        self.assertEqual(execution.trace["provider"], "healthcaresource")
        self.assertEqual(
            execution.trace["provider_detection"],
            {
                "method": "linked_url_evidence",
                "provider": "healthcaresource",
                "url": expected_url,
                "evidence_url": expected_url,
            },
        )
        summary = execution.trace["job_board_portfolio"]
        self.assertEqual(summary["primary_url"], expected_url)
        self.assertEqual(summary["primary_provider"], "healthcaresource")
        self.assertEqual(summary["eligible_count"], 4)
        self.assertFalse(summary["eligible_set_complete"])
        payload = summary["checkpoint_payload"]
        self.assertNotIn("relationship_evidence_url", repr(payload))
        self.assertNotIn(CAREER_PAGE, repr(payload))
        restored = JobBoardPortfolio.from_checkpoint_payload(payload)
        self.assertEqual(restored.primary.board.url, expected_url)
        self.assertEqual(restored.to_checkpoint_payload(), payload)
        self.assertEqual(
            execution.trace["job_board_portfolio_projection"],
            {
                "status": "superseded_conflict",
                "fields": [
                    "job_list_page_url",
                    "provider",
                    "provider_detection.url",
                    "provider_detection.provider",
                    "job_board_portfolio.primary_url",
                    "job_board_portfolio.primary_provider",
                ],
                "resolution": "final_typed_portfolio",
            },
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
