import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.candidate_portfolio import CompositeCandidateDiscovery
from job_source_agent.direct_candidate_discovery import ExternalApplyDiscovery
from job_source_agent.provider_candidates import (
    CandidateDiscoveryResult,
    ProviderCandidate,
)
from job_source_agent.identity_continuity import HiringIdentityEvidence, ProviderIdentity
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import CompanyInput
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages.discovery import (
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    _opening_identity,
)
from job_source_agent.stages.validation import ResultValidationStage


class _NoNetworkService:
    def find_job_board(self, *args, **kwargs):
        raise AssertionError("S5 must not require career-page discovery for external apply")


class _NoOpeningService:
    def match_discovered_board(self, board, target_title=None, target_location=None):
        return None, board.board.url, {
            "provider_api": {
                "inventory": {
                    "status": "verified_filtered_empty",
                    "scope": "title_filtered",
                    "complete": True,
                    "candidate_count": 0,
                }
            }
        }


class _ExactOpeningService:
    def match_discovered_board(self, board, target_title=None, target_location=None):
        opening = "https://jobs.lever.co/acme/role-123"
        return opening, board.board.url, {
            "selected": {
                "url": opening,
                "title": "AI Engineer",
                "location": "New York, NY",
            },
            "provider_api": {
                "inventory": {
                    "scope": "full",
                    "complete": True,
                    "candidate_count": 2,
                }
            },
        }


class _ProviderInventoryOpeningService:
    def __init__(self, organization):
        self.organization = organization

    def match_discovered_board(self, board, target_title=None, target_location=None):
        opening = board.evidence_url
        return opening, board.board.url, {
            "selected": {
                "url": opening,
                "title": target_title,
                "location": target_location,
                "hiring_organization_name": self.organization,
            },
            "provider_api": {
                "inventory": {
                    "source": "native_adapter",
                    "scope": "title_filtered",
                    "complete": True,
                    "candidate_count": 1,
                }
            },
        }


class _StaticCandidateDiscovery:
    def __init__(self, *candidates):
        self.candidates = candidates

    def discover(self, request):
        return CandidateDiscoveryResult(tuple(self.candidates), {"source": "test"})


def _verified_hiring(name="Acme"):
    return HiringIdentityEvidence(
        source_company_name=name,
        hiring_entity_name=name,
        relationship_type="same_entity",
        verification_method="same_entity",
        verified=True,
        evidence_url="https://careers.acme.example/jobs",
    )


def _provider_identity(provider, tenant, board_url):
    return ProviderIdentity(
        hiring_entity_name="Acme",
        provider=provider,
        tenant=tenant,
        canonical_board_url=board_url,
        evidence_url="https://careers.acme.example/jobs",
        verification_method="tenant_name_match",
        relationship_verified=True,
    )


class ParallelCandidateStageCharacterizationTests(unittest.TestCase):
    def _oracle_context(self):
        opening = (
            "https://eohh.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX/job/425798"
        )
        adapter = DEFAULT_PROVIDER_REGISTRY.adapter_for(opening)
        self.assertIsNotNone(adapter)
        board = adapter.identify_board(opening)
        self.assertIsNotNone(board)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Texas Children's Hospital",
                job_title="Registered Nurse (RN) - LDRP",
                job_location="Austin, TX",
            )
        )
        context.job_list_page_url = board.url
        context.provider = board.provider
        context.discovered_job_board = DiscoveredJobBoard(
            board=board,
            detection_method="targeted_search",
            evidence_url=opening,
        )
        return context

    def test_native_opening_organization_can_verify_opaque_provider_tenant(self):
        context = self._oracle_context()

        opening_execution = OpeningMatchStage(
            _ProviderInventoryOpeningService("Texas Children's Hospital"),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)
        context.apply(opening_execution)
        validation = ResultValidationStage().run(context)

        self.assertEqual(opening_execution.result.status, "success")
        self.assertEqual(
            opening_execution.updates[
                "hiring_identity_evidence"
            ].verification_method,
            "provider_inventory",
        )
        self.assertTrue(
            opening_execution.updates["provider_identity"].relationship_verified
        )
        self.assertEqual(validation.result.status, "success")

    def test_native_opening_organization_mismatch_remains_identity_rejected(self):
        context = self._oracle_context()

        opening_execution = OpeningMatchStage(
            _ProviderInventoryOpeningService("Unrelated Health System"),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)
        context.apply(opening_execution)
        validation = ResultValidationStage().run(context)

        self.assertNotIn("hiring_identity_evidence", opening_execution.updates)
        self.assertFalse(
            opening_execution.updates["provider_identity"].relationship_verified
        )
        self.assertEqual(validation.result.status, "failed")
        self.assertEqual(validation.result.reason_code, "RESULT_IDENTITY_MISMATCH")

    def test_external_apply_runs_s5_without_s4_career_page(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                external_apply_url="https://jobs.lever.co/acme/role-123",
            )
        )

        execution = JobBoardDiscoveryStage(_NoNetworkService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.trace["method"], "external_apply_url")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.lever.co/acme",
        )

    def test_default_s5_keeps_blocking_without_career_or_external_candidate(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        execution = JobBoardDiscoveryStage(_NoNetworkService()).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(execution.updates, {})

    def test_enabled_empty_candidate_pool_preserves_fallback_trace(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        discovery = CompositeCandidateDiscovery(
            (_StaticCandidateDiscovery(),),
            limit=12,
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "not_run")
        fallback = execution.trace["parallel_candidate_fallback"]
        self.assertEqual(
            fallback["candidate_discovery"]["pool"]["candidate_count"],
            0,
        )
        self.assertEqual(
            fallback["candidate_verification"]["verified_candidate_count"],
            0,
        )

    def test_search_rank_and_snippet_do_not_authorize_an_unrelated_tenant(self):
        board = DiscoveredJobBoard(
            board=JobBoard(
                "https://jobs.ashbyhq.com/notion",
                "ashby",
                "notion",
            ),
            detection_method="linked_url_evidence",
            evidence_url="https://www.google.com/search?q=acme+jobs",
        )

        class _SearchCandidateService:
            def find_job_board_with_evidence(self, *args, **kwargs):
                return board.board.url, {
                    "search_candidate": {
                        "rank": 1,
                        "snippet": "Acme is hiring now",
                    }
                }, board

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://careers.acme.example/jobs"
        context.hiring_identity_evidence = _verified_hiring()

        execution = JobBoardDiscoveryStage(
            _SearchCandidateService(), DEFAULT_PROVIDER_REGISTRY
        ).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_cross_provider_opening_candidate_cannot_receive_identity(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.provider_identity = _provider_identity(
            "greenhouse",
            "acme",
            "https://boards.greenhouse.io/acme",
        )

        identity = _opening_identity(
            context,
            "https://jobs.lever.co/acme/role-123",
            DEFAULT_PROVIDER_REGISTRY,
        )

        self.assertIsNone(identity)

    def test_cross_tenant_opening_candidate_cannot_receive_identity(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.provider_identity = _provider_identity(
            "greenhouse",
            "acme",
            "https://boards.greenhouse.io/acme",
        )

        identity = _opening_identity(
            context,
            "https://boards.greenhouse.io/notion/jobs/role-123",
            DEFAULT_PROVIDER_REGISTRY,
        )

        self.assertIsNone(identity)

    def test_first_party_opening_trace_cannot_bind_to_different_provider_board(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.provider_identity = _provider_identity(
            "greenhouse",
            "acme",
            "https://boards.greenhouse.io/acme",
        )
        opening_url = "https://careers.acme.example/jobs?gh_jid=123"

        identity = _opening_identity(
            context,
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            {
                "provider_api": {
                    "provider": "greenhouse",
                    "provider_detection": {
                        "url": "https://boards.greenhouse.io/notion",
                    },
                },
                "selected": {"url": opening_url},
            },
        )

        self.assertIsNone(identity)

    def test_verified_official_board_with_no_exact_opening_is_partial(self):
        board = DiscoveredJobBoard(
            board=JobBoard(
                "https://boards.greenhouse.io/acme",
                "greenhouse",
                "acme",
            ),
            detection_method="url_evidence",
            evidence_url="https://careers.acme.example/jobs",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Staff Engineer")
        )
        context.job_list_page_url = board.board.url
        context.discovered_job_board = board
        context.provider_identity = _provider_identity(
            "greenhouse", "acme", board.board.url
        )

        execution = OpeningMatchStage(_NoOpeningService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.updates["job_list_page_url"], board.board.url)
        self.assertNotIn("open_position_url", execution.updates)
        self.assertNotIn("opening_identity", execution.updates)

    def test_enabled_parallel_candidates_allow_external_and_search_inputs_without_s4(self):
        external = "https://jobs.lever.co/acme/role-123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="Engineer",
                external_apply_url=external,
            )
        )
        discovery = CompositeCandidateDiscovery(
            (ExternalApplyDiscovery(DEFAULT_PROVIDER_REGISTRY),),
            limit=12,
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.trace["method"], "parallel_candidate_discovery")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.lever.co/acme",
        )
        self.assertTrue(execution.updates["hiring_identity_evidence"].verified)
        self.assertEqual(
            execution.updates["hiring_identity_evidence"].verification_method,
            "linkedin_external_apply",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)

    def test_enabled_external_apply_runs_through_s6_and_s7_identity_gate(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="AI Engineer",
                job_location="New York, NY",
                external_apply_url="https://jobs.lever.co/acme/role-123",
            )
        )
        discovery = CompositeCandidateDiscovery(
            (ExternalApplyDiscovery(DEFAULT_PROVIDER_REGISTRY),),
            limit=12,
        )
        stages = (
            JobBoardDiscoveryStage(
                _NoNetworkService(),
                DEFAULT_PROVIDER_REGISTRY,
                candidate_discovery=discovery,
                enable_parallel_candidate_discovery=True,
            ),
            OpeningMatchStage(_ExactOpeningService(), DEFAULT_PROVIDER_REGISTRY),
            ResultValidationStage(),
        )

        for stage in stages:
            execution = stage.run(context)
            context.apply(execution)

        self.assertEqual([item.status for item in context.stage_results], ["success"] * 3)
        self.assertEqual(
            context.opening_selection_evidence.canonical_opening_url,
            "https://jobs.lever.co/acme/role-123",
        )
        self.assertEqual(
            context.trace["stages"]["result_validation"]["location_classification"],
            "exact",
        )

    def test_verified_company_tenant_is_ranked_before_unrelated_search_result(self):
        def search_candidate(tenant, rank):
            return ProviderCandidate(
                url=f"https://jobs.ashbyhq.com/{tenant}",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=rank,
            )

        discovery = CompositeCandidateDiscovery(
            (
                _StaticCandidateDiscovery(
                    search_candidate("notion", 1),
                    search_candidate("acme", 2),
                ),
            ),
            limit=12,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "provider_tenant_match",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)

    def test_candidate_relationship_canonicalizes_evidence_url(self):
        discovery = CompositeCandidateDiscovery(
            (
                _StaticCandidateDiscovery(
                    ProviderCandidate(
                        url="https://jobs.ashbyhq.com/acme/?utm_source=search",
                        source_kind="targeted_board_search",
                        source_url="https://www.bing.com/search?q=acme",
                        company_name="Acme",
                        target_title="Engineer",
                        provider_hint="ashby",
                        query='site:jobs.ashbyhq.com "Acme"',
                        result_rank=1,
                    ),
                ),
            ),
            limit=12,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        relationship = execution.trace["relationship_evidence"]
        self.assertEqual(relationship["evidence_url"], "https://jobs.ashbyhq.com/acme")
        self.assertTrue(relationship["verified"])

    def test_candidate_contract_rejects_invalid_identity_evidence_url(self):
        with self.assertRaisesRegex(ValueError, "canonical identity evidence"):
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/acme?note=%0A",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=1,
            )

    def test_s3_identity_does_not_authorize_an_unrelated_first_search_tenant(self):
        def search_candidate(tenant, rank):
            return ProviderCandidate(
                url=f"https://jobs.ashbyhq.com/{tenant}",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=rank,
            )

        discovery = CompositeCandidateDiscovery(
            (
                _StaticCandidateDiscovery(
                    search_candidate("notion", 1),
                    search_candidate("acme", 2),
                ),
            ),
            limit=12,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.hiring_identity_evidence = _verified_hiring()

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertEqual(
            execution.trace["relationship_evidence"]["evidence_type"],
            "provider_tenant_match",
        )
        self.assertTrue(execution.trace["relationship_evidence"]["verified"])

    def test_targeted_opening_priority_does_not_override_tenant_identity(self):
        wrong_opening = ProviderCandidate(
            url="https://jobs.ashbyhq.com/notion/role-123",
            source_kind="targeted_opening_search",
            source_url="https://www.bing.com/search?q=acme+engineer",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="ashby",
            query='"acme" "Engineer" jobs',
            result_rank=1,
        )
        right_board = ProviderCandidate(
            url="https://jobs.ashbyhq.com/acme",
            source_kind="targeted_board_search",
            source_url="https://www.bing.com/search?q=acme+engineer",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="ashby",
            query='site:jobs.ashbyhq.com "acme" "Engineer"',
            result_rank=2,
        )
        discovery = CompositeCandidateDiscovery(
            (_StaticCandidateDiscovery(wrong_opening, right_board),),
            limit=12,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertTrue(execution.trace["relationship_evidence"]["verified"])


if __name__ == "__main__":
    unittest.main()
