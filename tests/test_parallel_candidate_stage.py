import unittest

from job_source_agent.contracts import PipelineContext, StageExecution
from job_source_agent.candidate_portfolio import CompositeCandidateDiscovery
from job_source_agent.direct_candidate_discovery import (
    ExternalApplyDiscovery,
    WebsiteCareerDiscovery,
)
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
    _deduplicate_public_board_identities,
    _merge_legacy_website_route,
    _opening_identity,
)
from job_source_agent.reasons import make_stage_result
from job_source_agent.stages.validation import ResultValidationStage


class _NoNetworkService:
    def find_job_board(self, *args, **kwargs):
        raise AssertionError("S5 must not require career-page discovery for external apply")


class _LegacyBoardService:
    def find_job_board_portfolio(
        self,
        career_page_url,
        company_name=None,
        target_title=None,
        target_location=None,
    ):
        board = DiscoveredJobBoard(
            board=JobBoard(
                "https://jobs.lever.co/acme",
                "lever",
                "acme",
            ),
            detection_method="linked_url_evidence",
            evidence_url="https://jobs.lever.co/acme",
        )
        from job_source_agent.job_board import JobBoardPortfolio

        return board.board.url, {"provider": "lever"}, JobBoardPortfolio(
            (board,), True
        )


class _TrackedLegacyBoardService(_LegacyBoardService):
    def __init__(self, events):
        self.events = events

    def find_job_board_portfolio(self, *args, **kwargs):
        self.events.append("website_direct")
        return super().find_job_board_portfolio(*args, **kwargs)


class _FirstPartyEmbeddedInventoryService:
    def find_job_board_portfolio(
        self,
        career_page_url,
        company_name=None,
        target_title=None,
        target_location=None,
    ):
        return career_page_url, {
            "first_party_listing_inventory": {
                "status": "verified",
                "source": "semantic_title_url_binding",
                "candidates": [
                    {
                        "title": "Staff Engineer",
                        "url": "https://job-boards.greenhouse.io/acme/jobs/123",
                        "source_url": career_page_url,
                    }
                ],
            }
        }, None


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


class PortfolioMergeTests(unittest.TestCase):
    def test_equivalent_route_boards_collapse_without_changing_primary_rank(self):
        primary = DiscoveredJobBoard(
            board=JobBoard("https://jobs.lever.co/acme", "lever", "acme"),
            detection_method="linked_url_evidence",
            evidence_url="https://jobs.lever.co/acme",
        )
        duplicate = DiscoveredJobBoard(
            board=JobBoard("https://JOBS.LEVER.CO/acme/", "LEVER", "acme"),
            detection_method="page_evidence",
            evidence_url="https://JOBS.LEVER.CO/acme/",
        )
        distinct = DiscoveredJobBoard(
            board=JobBoard("https://jobs.ashbyhq.com/acme", "ashby", "acme"),
            detection_method="linked_url_evidence",
            evidence_url="https://jobs.ashbyhq.com/acme",
        )

        merged = _deduplicate_public_board_identities(
            [primary, duplicate, distinct]
        )

        self.assertEqual(merged, [primary, distinct])


class _TrackedWaveDiscovery(_StaticCandidateDiscovery):
    def __init__(self, candidate_wave, *candidates):
        super().__init__(*candidates)
        self.candidate_wave = candidate_wave
        self.calls = 0

    def discover(self, request):
        self.calls += 1
        return super().discover(request)


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


def _guessed_candidate(tenant):
    return ProviderCandidate(
        url=f"https://jobs.ashbyhq.com/{tenant}",
        source_kind="guessed_path",
        source_url="https://careers.acme.example/jobs",
        company_name="Acme",
        target_title="Engineer",
        provider_hint="ashby",
    )


def _verified_tenant_probe_candidate(tenant):
    return ProviderCandidate(
        url=f"https://jobs.ashbyhq.com/{tenant}",
        source_kind="verified_tenant_probe",
        source_url="https://www.linkedin.com/company/acme",
        company_name="Acme",
        target_title="Engineer",
        provider_hint="ashby",
    )


def _unrelated_direct_candidate():
    return ProviderCandidate(
        url="https://jobs.ashbyhq.com/notion",
        source_kind="first_party_ats_link",
        source_url="https://jobs.ashbyhq.com/notion",
        company_name="Acme",
        target_title="Engineer",
        provider_hint="ashby",
    )


class ParallelCandidateStageCharacterizationTests(unittest.TestCase):
    def test_verified_tenant_probe_binds_exact_official_website_domain(self):
        website = "https://www.mrbeastyoutube.com/"
        candidate = ProviderCandidate(
            url="https://jobs.ashbyhq.com/mrbeastyoutube",
            source_kind="verified_tenant_probe",
            source_url=website,
            company_name="MrBeast",
            target_title="Account Executive",
            provider_hint="ashby",
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="MrBeast",
                company_website_url=website,
                job_title="Account Executive",
            )
        )
        context.hiring_identity_evidence = _verified_hiring("MrBeast")

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_StaticCandidateDiscovery(candidate),),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        identity = execution.updates["provider_identity"]
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(
            identity.verification_method,
            "provider_tenant_match",
        )

    def test_verified_website_tenant_probe_rejects_substring_tenant_collision(self):
        website = "https://www.mrbeastyoutube.com/"
        candidate = ProviderCandidate(
            url="https://jobs.ashbyhq.com/mrbeastyoutubejobs",
            source_kind="verified_tenant_probe",
            source_url=website,
            company_name="MrBeast",
            target_title="Account Executive",
            provider_hint="ashby",
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="MrBeast",
                company_website_url=website,
                job_title="Account Executive",
            )
        )
        context.hiring_identity_evidence = _verified_hiring("MrBeast")

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_StaticCandidateDiscovery(candidate),),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertNotEqual(execution.result.status, "success")

    def test_inventory_revalidated_tenant_probe_can_restore_relationship_without_s2(self):
        discovery = CompositeCandidateDiscovery(
            (_StaticCandidateDiscovery(_verified_tenant_probe_candidate("acme")),),
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

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["hiring_identity_evidence"].verification_method,
            "provider_tenant_match",
        )

    def test_official_career_direct_handoff_runs_before_search_wave(self):
        events = []

        class _SearchDiscovery(_StaticCandidateDiscovery):
            candidate_wave = "search"

            def discover(self, request):
                events.append("search")
                return super().discover(request)

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = "https://www.acme.example/careers"
        execution = JobBoardDiscoveryStage(
            _TrackedLegacyBoardService(events),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_SearchDiscovery(),), limit=12
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(events, ["website_direct"])
        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.trace["candidate_scheduler"],
            {
                "strategy": "direct_then_website_then_search",
                "website_direct_status": "success",
                "search_wave": "not_run",
            },
        )

    def test_exhaustive_route_evaluation_runs_search_after_verified_direct(self):
        direct = _TrackedWaveDiscovery(
            "direct",
            ProviderCandidate(
                url="https://jobs.lever.co/acme",
                source_kind="external_apply",
                source_url="https://jobs.lever.co/acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="lever",
            ),
        )
        search = _TrackedWaveDiscovery(
            "search",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/acme",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=1,
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="Engineer",
                external_apply_url="https://jobs.lever.co/acme",
            )
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (direct, search),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
            evaluate_all_candidate_routes=True,
        ).run(context)

        self.assertEqual((direct.calls, search.calls), (1, 1))
        self.assertEqual(
            execution.trace["candidate_discovery"]["strategy"],
            "exhaustive_route_evaluation",
        )
        routes = execution.trace["route_evaluation"]["routes"]
        self.assertEqual(routes["external_apply"]["relationship_verified_count"], 1)
        self.assertEqual(routes["provider_search"]["relationship_verified_count"], 1)
        self.assertFalse(routes["website_career"]["input_available"])

    def test_exhaustive_route_evaluation_records_legacy_website_board(self):
        search = _TrackedWaveDiscovery(
            "search",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/acme",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=1,
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.company_website_url = "https://acme.example"
        context.career_page_url = "https://careers.acme.example/jobs"
        context.hiring_identity_evidence = _verified_hiring()

        execution = JobBoardDiscoveryStage(
            _LegacyBoardService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery((search,), limit=12),
            enable_parallel_candidate_discovery=True,
            evaluate_all_candidate_routes=True,
        ).run(context)

        website = execution.trace["route_evaluation"]["routes"]["website_career"]
        self.assertEqual(website["legacy_status"], "success")
        self.assertEqual(website["relationship_verified_count"], 1)
        self.assertEqual(
            website["verified_relationship_boards"][0]["provider"],
            "lever",
        )
        self.assertEqual(len(execution.updates["job_board_portfolio"].boards), 2)

    def test_first_party_inventory_merge_keeps_provider_identity_with_selected_ats_board(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Staff Engineer")
        )
        context.company_website_url = "https://www.acme.com/"
        context.career_page_url = "https://www.acme.com/careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://www.acme.com",
        )
        candidate_board = DiscoveredJobBoard(
            board=JobBoard(
                "https://job-boards.greenhouse.io/acme",
                "greenhouse",
                "acme",
            ),
            detection_method="targeted_search",
            evidence_url="https://job-boards.greenhouse.io/acme",
        )
        candidate_execution = StageExecution(
            result=make_stage_result("job_board_discovery", "success"),
            updates={
                "job_list_page_url": candidate_board.board.url,
                "provider": "greenhouse",
                "discovered_job_board": candidate_board,
                "provider_identity": ProviderIdentity(
                    hiring_entity_name="Acme",
                    provider="greenhouse",
                    tenant="acme",
                    canonical_board_url=candidate_board.board.url,
                    evidence_url=candidate_board.board.url,
                    verification_method="linked_url_only",
                    relationship_verified=False,
                ),
            },
            trace={"route_evaluation": {"schema_version": "1.0", "routes": {}}},
        )
        legacy_execution = StageExecution(
            result=make_stage_result("job_board_discovery", "success"),
            updates={
                "job_list_page_url": context.career_page_url,
                "provider_identity": ProviderIdentity(
                    hiring_entity_name="Acme",
                    provider="generic",
                    tenant="url:https://www.acme.com/careers",
                    canonical_board_url="https://www.acme.com/careers",
                    evidence_url="https://www.acme.com/careers",
                    verification_method="first_party_same_site",
                    relationship_verified=True,
                ),
            },
            trace={
                "first_party_listing_inventory": {
                    "status": "verified",
                    "source": "semantic_title_url_binding",
                    "candidates": [
                        {
                            "title": "Staff Engineer",
                            "url": "https://job-boards.greenhouse.io/acme/jobs/123",
                            "source_url": context.career_page_url,
                        }
                    ],
                }
            },
        )

        execution = _merge_legacy_website_route(
            context,
            candidate_execution,
            legacy_execution,
            DEFAULT_PROVIDER_REGISTRY,
        )

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://job-boards.greenhouse.io/acme",
        )
        identity = execution.updates["provider_identity"]
        self.assertEqual(identity.provider, "greenhouse")
        self.assertEqual(identity.tenant, "acme")
        self.assertEqual(
            identity.canonical_board_url,
            "https://job-boards.greenhouse.io/acme",
        )
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "tenant_name_match")

    def test_verified_first_party_inventory_promotes_native_board_without_search_probe(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Staff Engineer")
        )
        context.company_website_url = "https://www.acme.com/"
        context.career_page_url = "https://www.acme.com/careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://www.acme.com",
        )

        execution = JobBoardDiscoveryStage(
            _FirstPartyEmbeddedInventoryService(),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://job-boards.greenhouse.io/acme",
        )
        self.assertEqual(execution.updates["provider"], "greenhouse")
        identity = execution.updates["provider_identity"]
        self.assertEqual(identity.provider, "greenhouse")
        self.assertEqual(identity.tenant, "acme")
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(
            execution.trace["provider_board_promotion"]["source"],
            "verified_first_party_listing_inventory",
        )

    def test_gary_isolved_direct_career_candidate_skips_targeted_search(self):
        search = _TrackedWaveDiscovery("search")
        discovery = CompositeCandidateDiscovery(
            (WebsiteCareerDiscovery(DEFAULT_PROVIDER_REGISTRY), search),
            limit=12,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Gary and Mary West PACE")
        )
        context.career_page_url = "https://westpace.isolvedhire.com/jobs/"
        context.hiring_identity_evidence = _verified_hiring(
            "Gary and Mary West PACE"
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["provider"], "isolved")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://westpace.isolvedhire.com/jobs/",
        )
        self.assertEqual(search.calls, 0)
        self.assertEqual(execution.trace["candidate_wave"], "direct")
        search_wave = execution.trace["candidate_discovery"]["waves"]["search"]
        self.assertEqual(search_wave["status"], "skipped")
        self.assertEqual(search_wave["reason"], "verified_direct_candidate")
        self.assertEqual(search_wave["sources"][0]["status"], "skipped")
        direct_sources = execution.trace["candidate_discovery"]["waves"][
            "direct"
        ]["sources"]
        self.assertEqual(direct_sources[1]["status"], "deferred")

    def test_rejected_direct_relationship_runs_search_wave(self):
        direct = _TrackedWaveDiscovery(
            "direct",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/notion",
                source_kind="first_party_ats_link",
                source_url="https://jobs.ashbyhq.com/notion",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
            ),
        )
        search = _TrackedWaveDiscovery(
            "search",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/acme",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                target_title="Engineer",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=1,
            ),
        )
        discovery = CompositeCandidateDiscovery((direct, search), limit=12)
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = "https://careers.acme.example/jobs"

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=discovery,
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual((direct.calls, search.calls), (1, 1))
        self.assertEqual(execution.trace["candidate_wave"], "search")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertEqual(
            execution.trace["candidate_discovery"]["waves"]["search"]["wave"],
            "search",
        )
        self.assertEqual(
            execution.trace["candidate_discovery"]["waves"]["direct"]["wave"],
            "direct",
        )
        self.assertEqual(
            execution.trace["relationship_verification"]["direct"]["status"],
            "rejected",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)

    def test_cross_tenant_fallback_never_becomes_verified_from_search_rank(self):
        direct = _TrackedWaveDiscovery(
            "direct",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/notion",
                source_kind="first_party_ats_link",
                source_url="https://jobs.ashbyhq.com/notion",
                company_name="Acme",
                provider_hint="ashby",
            ),
        )
        search = _TrackedWaveDiscovery(
            "search",
            ProviderCandidate(
                url="https://jobs.ashbyhq.com/linear",
                source_kind="targeted_board_search",
                source_url="https://www.bing.com/search?q=acme",
                company_name="Acme",
                provider_hint="ashby",
                query='site:jobs.ashbyhq.com "Acme"',
                result_rank=1,
            ),
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://careers.acme.example/jobs"

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (direct, search),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual((direct.calls, search.calls), (1, 1))
        self.assertEqual(execution.trace["candidate_wave"], "search")
        self.assertFalse(execution.trace["relationship_verified"])
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.trace["relationship_evidence"]["evidence_type"],
            "unverified_candidate",
        )
        context.apply(execution)
        validation = ResultValidationStage().run(context)
        self.assertEqual(validation.result.status, "failed")
        self.assertEqual(validation.result.reason_code, "RESULT_IDENTITY_MISMATCH")

    def test_guessed_same_name_tenant_stays_untrusted_with_verified_hiring_identity(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = "https://careers.acme.example/jobs"
        context.hiring_identity_evidence = _verified_hiring()

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (
                    _TrackedWaveDiscovery("direct", _unrelated_direct_candidate()),
                    _TrackedWaveDiscovery("search", _guessed_candidate("acme")),
                ),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertFalse(execution.trace["relationship_evidence"]["verified"])
        self.assertEqual(
            execution.trace["relationship_evidence"]["evidence_type"],
            "unverified_candidate",
        )
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "linked_url_only",
        )

    def test_guessed_cross_tenant_candidate_stays_untrusted(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (
                    _TrackedWaveDiscovery("direct", _unrelated_direct_candidate()),
                    _TrackedWaveDiscovery(
                        "search", _guessed_candidate("linkedin")
                    ),
                ),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertFalse(execution.trace["relationship_evidence"]["verified"])
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)

    def test_verified_first_party_handoff_still_establishes_relationship(self):
        candidate = ProviderCandidate(
            url="https://jobs.ashbyhq.com/acme-platform",
            source_kind="first_party_ats_link",
            source_url="https://careers.acme.example/jobs",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="ashby",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = "https://careers.acme.example/jobs"
        context.hiring_identity_evidence = _verified_hiring()

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_discovery=CompositeCandidateDiscovery(
                (_StaticCandidateDiscovery(candidate),),
                limit=12,
            ),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.trace["relationship_evidence"]["evidence_type"],
            "first_party_handoff",
        )
        self.assertTrue(execution.trace["relationship_evidence"]["verified"])
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)

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
