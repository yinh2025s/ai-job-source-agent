import unittest

from job_source_agent.candidate_discovery_coordinator import (
    CandidateDiscoveryCoordinator,
    CandidateDiscoveryRouteStatus,
    RouteFailure,
    RouteProducerOutput,
    RouteProvenance,
)
from job_source_agent.contracts import PipelineContext, StageExecution
from job_source_agent.candidate_portfolio import CompositeCandidateDiscovery
from job_source_agent.direct_candidate_discovery import (
    ExternalApplyDiscovery,
    WebsiteCareerDiscovery,
)
from job_source_agent.provider_candidates import (
    CandidateDiscoveryResult,
    ProviderCandidate,
    ProviderPublishedEmployerEvidence,
)
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    HiringRelationshipEvidence,
    ProviderIdentity,
)
from job_source_agent.job_board import (
    DiscoveredJobBoard,
    JobBoard,
    JobBoardPortfolio,
    JobBoardRouteEvidence,
)
from job_source_agent.models import CompanyInput
from job_source_agent.provisional_evidence import ProvisionalWebsiteEvidence
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages.discovery import (
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    _deduplicate_public_board_identities,
    _coordinator_source_input,
    _merged_portfolio_is_complete,
    _merge_legacy_website_route,
    _opening_identity,
    _promote_verified_native_provider_identity,
    _provider_inventory_hiring_evidence,
)
from job_source_agent.reasons import make_stage_result
from job_source_agent.stages.validation import ResultValidationStage


class _NoNetworkService:
    def find_job_board(self, *args, **kwargs):
        raise AssertionError("S5 must not require career-page discovery for external apply")


class ProviderInventoryEvidenceContractTests(unittest.TestCase):
    def test_opening_evidence_url_is_canonicalized_before_identity_construction(self):
        context = PipelineContext(
            company=CompanyInput(
                company_name="Acme",
                job_title="Data Analyst",
                job_location="Austin, TX",
            )
        )
        context.hiring_entity_name = "Acme"
        opening_url = "https://careers.example.com/job/Data-Analyst/101/"
        evidence = _provider_inventory_hiring_evidence(
            context,
            {
                "selected": {
                    "url": opening_url,
                    "hiring_organization_name": "Acme",
                },
                "provider_api": {
                    "inventory": {"source": "native_adapter", "complete": True}
                },
            },
            opening_url,
        )

        self.assertIsNotNone(evidence)
        self.assertEqual(
            evidence.evidence_url,
            "https://careers.example.com/job/Data-Analyst/101",
        )

    def test_runtime_workable_widget_inventory_binds_generic_opening_to_s7_tenant(self):
        board = JobBoard(
            url="https://www.example.com/careers",
            provider="workable",
            identifier="widget:149632",
            replay_safe=False,
        )
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="page_evidence",
            evidence_url=board.url,
        )
        provider_identity = ProviderIdentity(
            hiring_entity_name="Example",
            provider="workable",
            tenant="widget:149632",
            canonical_board_url=board.url,
            evidence_url=board.url,
            verification_method="page_evidence",
            relationship_verified=True,
        )
        opening_url = "https://apply.workable.com/j/EA1650B1D6"
        trace = {
            "selected": {
                "url": opening_url,
                "title": "Product Growth Marketing Manager",
                "location": "London, England, United Kingdom",
            },
            "provider_api": {
                "provider": "workable",
                "provider_detection": {
                    "method": "page_evidence",
                    "provider": "workable",
                    "url": board.url,
                },
                "inventory": {
                    "source": "native_adapter",
                    "scope": "full",
                    "complete": True,
                    "candidate_count": 2,
                },
                "adapter": "workable",
                "candidates": [{"url": opening_url}],
                "employer_evidence": [
                    {
                        "employer_name": "Example",
                        "evidence_url": (
                            "https://apply.workable.com/api/v1/widget/accounts/149632"
                            "?origin=embed&callback=whrcallback"
                        ),
                        "opening_url": opening_url,
                        "extraction_method": "workable_widget_employer",
                    }
                ],
                "adapter_trace": {
                    "board_identity": {
                        "provider": "workable",
                        "url": board.url,
                        "identifier": "widget:149632",
                        "runtime_only": True,
                    },
                    "inventory_verified_opening_urls": [opening_url],
                },
            },
        }
        context = PipelineContext(
            company=CompanyInput(
                company_name="Example",
                job_title="Product Growth Marketing Manager",
                job_location="London",
            )
        )

        promoted = _promote_verified_native_provider_identity(
            ProviderIdentity(
                hiring_entity_name="Example",
                provider="generic",
                tenant="url:https://www.example.com/careers",
                canonical_board_url=board.url,
                evidence_url=board.url,
                verification_method="provider_inventory",
                relationship_verified=True,
            ),
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            trace,
        )
        accepted = _opening_identity(
            context,
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            trace,
            provider_identity=promoted,
            discovered_board=discovered,
        )
        rejected = _opening_identity(
            context,
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            trace,
            provider_identity=ProviderIdentity(
                hiring_entity_name="Example",
                provider="workable",
                tenant="widget:999999",
                canonical_board_url="https://careers.other.example/jobs",
                evidence_url="https://careers.other.example/jobs",
                verification_method="page_evidence",
                relationship_verified=True,
            ),
            discovered_board=discovered,
        )
        trace_without_employer_binding = {
            **trace,
            "provider_api": {
                **trace["provider_api"],
                "employer_evidence": [],
            },
        }
        unpromoted = _promote_verified_native_provider_identity(
            ProviderIdentity(
                hiring_entity_name="Example",
                provider="generic",
                tenant="url:https://www.example.com/careers",
                canonical_board_url=board.url,
                evidence_url=board.url,
                verification_method="provider_inventory",
                relationship_verified=True,
            ),
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            trace_without_employer_binding,
        )
        trace_with_foreign_employer = {
            **trace,
            "provider_api": {
                **trace["provider_api"],
                "employer_evidence": [
                    {
                        **trace["provider_api"]["employer_evidence"][0],
                        "employer_name": "Unrelated Company",
                    }
                ],
            },
        }
        foreign_unpromoted = _promote_verified_native_provider_identity(
            ProviderIdentity(
                hiring_entity_name="Example",
                provider="generic",
                tenant="url:https://www.example.com/careers",
                canonical_board_url=board.url,
                evidence_url=board.url,
                verification_method="provider_inventory",
                relationship_verified=True,
            ),
            opening_url,
            DEFAULT_PROVIDER_REGISTRY,
            trace_with_foreign_employer,
        )

        self.assertIsNotNone(accepted)
        self.assertEqual(promoted.provider, "workable")
        self.assertEqual(promoted.tenant, "widget:149632")
        self.assertEqual(promoted.canonical_board_url, board.url)
        self.assertTrue(promoted.relationship_verified)
        self.assertEqual(accepted.tenant, "widget:149632")
        self.assertEqual(accepted.canonical_opening_url, opening_url)
        self.assertIsNone(rejected)
        self.assertEqual(unpromoted.provider, "generic")
        self.assertEqual(foreign_unpromoted.provider, "generic")


class MergedPortfolioCompletenessContractTests(unittest.TestCase):
    def _board(self, provider, url, identifier):
        return DiscoveredJobBoard(
            board=JobBoard(url, provider, identifier),
            detection_method="linked_url_evidence",
            evidence_url=url,
        )

    def _route(self, board, *, authorized):
        relationship = None
        if authorized:
            relationship = HiringRelationshipEvidence(
                source_company_name="Acme",
                hiring_entity_name="Acme",
                provider=board.board.provider,
                tenant=board.board.identifier,
                evidence_type="first_party_handoff",
                evidence_url="https://www.acme.example/careers",
                strength=100,
                verified=True,
            )
        return JobBoardRouteEvidence(
            provider=board.board.provider,
            canonical_board_url=board.board.url,
            route_kind="website_career" if authorized else "provider_search",
            source_kind=(
                "legacy_website_career" if authorized else "targeted_board_search"
            ),
            hiring_relationship=relationship,
        )

    def test_complete_source_covers_authorized_board_while_diagnostic_board_is_ignored(self):
        official = self._board(
            "ultipro",
            "https://recruiting2.ultipro.com/acm1000acme/JobBoard/board",
            "acm1000acme",
        )
        diagnostic = self._board(
            "ashby",
            "https://jobs.ashbyhq.com/unrelated",
            "unrelated",
        )
        legacy = JobBoardPortfolio((official,), eligible_set_complete=True)
        candidate = JobBoardPortfolio((diagnostic,), eligible_set_complete=False)

        self.assertTrue(
            _merged_portfolio_is_complete(
                [official, diagnostic],
                [
                    self._route(official, authorized=True),
                    self._route(diagnostic, authorized=False),
                ],
                (candidate, legacy),
                limit=8,
            )
        )

    def test_authorized_board_from_incomplete_source_remains_incomplete(self):
        official = self._board(
            "ultipro",
            "https://recruiting2.ultipro.com/acm1000acme/JobBoard/board",
            "acm1000acme",
        )
        second = self._board(
            "ashby",
            "https://jobs.ashbyhq.com/acme",
            "acme",
        )
        legacy = JobBoardPortfolio((official,), eligible_set_complete=True)
        candidate = JobBoardPortfolio((second,), eligible_set_complete=False)

        self.assertFalse(
            _merged_portfolio_is_complete(
                [official, second],
                [
                    self._route(official, authorized=True),
                    self._route(second, authorized=True),
                ],
                (candidate, legacy),
                limit=8,
            )
        )

    def test_incomplete_source_cannot_establish_complete_merge(self):
        official = self._board(
            "jazzhr",
            "https://acme.applytojob.com/apply",
            "acme",
        )
        legacy = JobBoardPortfolio((official,), eligible_set_complete=False)

        self.assertFalse(
            _merged_portfolio_is_complete(
                [official],
                [self._route(official, authorized=True)],
                (legacy,),
                limit=8,
            )
        )

    def test_board_cap_keeps_merge_incomplete(self):
        boards = [
            self._board(
                "generic",
                f"https://careers{i}.acme.example/jobs",
                f"board-{i}",
            )
            for i in range(9)
        ]
        primary = JobBoardPortfolio(tuple(boards[:8]), eligible_set_complete=True)
        secondary = JobBoardPortfolio((boards[8],), eligible_set_complete=True)

        self.assertFalse(
            _merged_portfolio_is_complete(
                boards,
                [],
                (primary, secondary),
                limit=8,
            )
        )


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
            relationship_evidence_url=career_page_url,
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


class _ProviderInventoryNoMatchService:
    def __init__(self, employer_name):
        self.employer_name = employer_name

    def match_discovered_board(self, board, target_title=None, target_location=None):
        openings = [
            f"{board.board.url}/jobs/101",
            f"{board.board.url}/jobs/102",
        ]
        return None, board.board.url, {
            "provider_api": {
                "inventory": {
                    "source": "native_adapter",
                    "status": "verified",
                    "scope": "full",
                    "complete": True,
                    "candidate_count": 2,
                },
                "employer_evidence": [
                    {
                        "employer_name": self.employer_name,
                        "descriptor_terms": [],
                        "evidence_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                        "opening_url": opening,
                        "extraction_method": "greenhouse_company_name",
                    }
                    for opening in openings
                ],
            }
        }


class _BoardEmployerNoMatchService:
    def __init__(self, employer_name):
        self.employer_name = employer_name

    def match_discovered_board(self, board, target_title=None, target_location=None):
        return None, board.board.url, {
            "provider_api": {
                "provider": board.board.provider,
                "adapter": board.board.provider,
                "inventory": {
                    "source": "native_adapter",
                    "status": "verified_filtered_empty",
                    "scope": "title_filtered",
                    "complete": True,
                    "candidate_count": 0,
                },
                "board_employer_evidence": {
                    "employer_name": self.employer_name,
                    "display_name": self.employer_name,
                    "evidence_url": board.board.url,
                    "extraction_method": "governmentjobs_agency_heading",
                },
                "adapter_trace": {
                    "inventory_scope": "title_filtered",
                    "inventory_complete": True,
                },
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


class _SuccessFactorsContinuityOpeningService:
    def __init__(self, tenant, requisition, *, mutate_trace=None):
        self.tenant = tenant
        self.requisition = requisition
        self.mutate_trace = mutate_trace

    def match_opening(self, job_list_url, target_title=None, target_location=None):
        board_url = (
            "https://career5.successfactors.eu/career"
            f"?company={self.tenant}"
        )
        opening_url = f"{board_url}&career_job_req_id={self.requisition}"
        trace = {
            "selected": {
                "url": opening_url,
                "title": target_title,
                "location": target_location,
            },
            "provider_api": {
                "provider": "successfactors",
                "adapter": "successfactors",
                "candidates": [
                    {
                        "url": opening_url,
                        "title": target_title,
                        "location": target_location,
                    }
                ],
                "inventory": {
                    "source": "native_adapter",
                    "scope": "title_filtered",
                    "complete": True,
                    "candidate_count": 1,
                },
                "adapter_trace": {
                    "board_identity": {
                        "provider": "successfactors",
                        "url": board_url,
                        "identifier": self.tenant,
                    },
                    "detail_verified_opening_urls": [opening_url],
                },
            },
        }
        if self.mutate_trace is not None:
            self.mutate_trace(trace, board_url, opening_url)
        return opening_url, job_list_url, trace


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


def _successfactors_continuity_context(company, title, location):
    generic_board = f"https://careers.{company.casefold()}.example/jobs"
    context = PipelineContext.from_company(
        CompanyInput(
            company_name=company,
            job_title=title,
            job_location=location,
        )
    )
    context.job_list_page_url = generic_board
    context.hiring_identity_evidence = HiringIdentityEvidence(
        source_company_name=company,
        hiring_entity_name=company,
        relationship_type="same_entity",
        verification_method="first_party_handoff",
        verified=True,
        evidence_url=generic_board,
    )
    context.hiring_entity_name = company
    context.provider_identity = ProviderIdentity(
        hiring_entity_name=company,
        provider="generic",
        tenant=f"url:{generic_board}",
        canonical_board_url=generic_board,
        evidence_url=generic_board,
        verification_method="verified_first_party_handoff",
        relationship_verified=True,
    )
    return context


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


def _coordinator_with_routes(*, external=(), provider=(), website=(), website_calls=None):
    def website_route(source, route):
        if website_calls is not None:
            website_calls.append(route)
        return RouteProducerOutput(tuple(website), RouteProvenance("website_career"))

    return CandidateDiscoveryCoordinator(
        external_apply=lambda source: RouteProducerOutput(
            tuple(external), RouteProvenance("external_apply")
        ),
        provider_search=lambda source: RouteProducerOutput(
            tuple(provider), RouteProvenance("provider_search")
        ),
        website_career=website_route,
    )


class ParallelCandidateStageCharacterizationTests(unittest.TestCase):
    def test_coordinator_source_canonicalizes_optional_linkedin_evidence(self):
        cases = (
            (
                "https://www.linkedin.com/jobs/view/engineer%0Afull-time-4440240968",
                "",
                "4440240968",
                None,
            ),
            (
                "https://www.linkedin.com/jobs/view/engineer-at-example-4430146950",
                "https://www.linkedin.com/company/example/",
                "4430146950",
                "https://www.linkedin.com/company/example",
            ),
            (
                "https://jobs.example.com/jobs/view/4430146950",
                "https://www.linkedin.com/%0Acompany/example",
                None,
                None,
            ),
            (
                "https://user@www.linkedin.com/jobs/view/4430146950",
                "https://example.com/company/example",
                None,
                None,
            ),
        )
        for job_url, company_url, expected_id, expected_company_url in cases:
            with self.subTest(job_url=job_url, company_url=company_url):
                context = PipelineContext.from_company(
                    CompanyInput(
                        company_name="Example",
                        linkedin_job_url=job_url,
                        linkedin_company_url=company_url,
                        job_title="Engineer",
                    )
                )

                source = _coordinator_source_input(context)

                self.assertEqual(source.linkedin_job_id, expected_id)
                self.assertEqual(
                    source.linkedin_job_url,
                    (
                        f"https://www.linkedin.com/jobs/view/{expected_id}"
                        if expected_id is not None
                        else None
                    ),
                )
                self.assertEqual(
                    source.linkedin_company_url,
                    expected_company_url,
                )

    def test_coordinator_source_drops_linkedin_url_without_job_id(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example",
                linkedin_job_url="https://www.linkedin.com/jobs/search?keywords=Engineer",
                job_title="Engineer",
            )
        )

        source = _coordinator_source_input(context)

        self.assertIsNone(source.linkedin_job_id)
        self.assertIsNone(source.linkedin_job_url)

    def test_coordinator_protects_verified_career_route_before_provider_search(self):
        events = []

        def provider_route(source):
            events.append("provider_search")
            return RouteProducerOutput((), RouteProvenance("provider_search"))

        coordinator = CandidateDiscoveryCoordinator(
            external_apply=lambda source: RouteProducerOutput(
                (), RouteProvenance("external_apply")
            ),
            provider_search=provider_route,
            website_career=lambda source, route: RouteProducerOutput(
                (), RouteProvenance("website_career")
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = "https://www.acme.example/careers"

        execution = JobBoardDiscoveryStage(
            _TrackedLegacyBoardService(events),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=coordinator,
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(events, ["website_direct", "provider_search"])
        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["job_list_page_url"], "https://jobs.lever.co/acme")

    def test_coordinator_empty_routes_return_typed_not_found_without_crashing(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _LegacyBoardService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=_coordinator_with_routes(),
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "JOB_BOARD_NOT_FOUND")
        self.assertFalse(execution.result.retryable)

    def test_coordinator_explores_provisional_site_without_authorizing_it(self):
        observed = []
        coordinator = CandidateDiscoveryCoordinator(
            external_apply=lambda source: RouteProducerOutput(
                (), RouteProvenance("external_apply")
            ),
            provider_search=lambda source: RouteProducerOutput(
                (), RouteProvenance("provider_search")
            ),
            website_career=lambda source, route: observed.append(route)
            or RouteProducerOutput((), RouteProvenance("website_career")),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.provisional_website_evidence = ProvisionalWebsiteEvidence(
            source_company_name="Acme",
            url="https://group.example",
            evidence_source="linkedin_official_website",
            reason_code="downstream_hiring_relationship_required",
            homepage_verified=True,
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=coordinator,
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.reason_code, "JOB_BOARD_NOT_FOUND")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].company_website_url, "https://group.example")
        self.assertEqual(observed[0].evidence_scope, "provisional_official_website")
        self.assertNotIn("company_website_url", execution.updates)

    def test_provisional_site_alone_cannot_authorize_provider_board(self):
        provider_candidate = ProviderCandidate(
            url="https://jobs.lever.co/acme",
            source_kind="first_party_ats_link",
            source_url="https://group.example",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="lever",
        )
        coordinator = CandidateDiscoveryCoordinator(
            external_apply=lambda source: RouteProducerOutput(
                (), RouteProvenance("external_apply")
            ),
            provider_search=lambda source: RouteProducerOutput(
                (), RouteProvenance("provider_search")
            ),
            website_career=lambda source, route: RouteProducerOutput(
                (provider_candidate,), RouteProvenance("website_career")
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.provisional_website_evidence = ProvisionalWebsiteEvidence(
            source_company_name="Acme",
            url="https://group.example",
            evidence_source="linkedin_official_website",
            reason_code="downstream_hiring_relationship_required",
            homepage_verified=True,
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=coordinator,
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "COMPANY_IDENTITY_AMBIGUOUS")
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)

    def test_verified_provisional_career_chain_can_authorize_provider_board(self):
        provider_candidate = ProviderCandidate(
            url="https://jobs.lever.co/acme",
            source_kind="first_party_ats_link",
            source_url="https://group.example/acme-careers",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="lever",
        )
        coordinator = CandidateDiscoveryCoordinator(
            external_apply=lambda source: RouteProducerOutput(
                (), RouteProvenance("external_apply")
            ),
            provider_search=lambda source: RouteProducerOutput(
                (), RouteProvenance("provider_search")
            ),
            website_career=lambda source, route: RouteProducerOutput(
                (provider_candidate,), RouteProvenance("website_career")
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.provisional_website_evidence = ProvisionalWebsiteEvidence(
            source_company_name="Acme",
            url="https://group.example",
            evidence_source="linkedin_official_website",
            reason_code="downstream_hiring_relationship_required",
            homepage_verified=True,
        )
        context.career_page_url = "https://group.example/acme-careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="provisional_same_host_career",
            verified=True,
            evidence_url=context.career_page_url,
        )
        context.hiring_entity_name = "Acme"

        execution = JobBoardDiscoveryStage(
            _LegacyBoardService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=coordinator,
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.trace["relationship_evidence"]["evidence_type"],
            "first_party_handoff",
        )

    def test_coordinator_budget_exhaustion_remains_retryable(self):
        coordinator = CandidateDiscoveryCoordinator(
            external_apply=lambda source: RouteProducerOutput(
                (), RouteProvenance("external_apply")
            ),
            provider_search=lambda source: RouteProducerOutput(
                (),
                RouteProvenance("provider_search"),
                status=CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
                failure=RouteFailure("fetch_budget_exhausted", retryable=True),
            ),
            website_career=lambda source, route: RouteProducerOutput(
                (), RouteProvenance("website_career")
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=coordinator,
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(execution.result.retryable)

    def test_coordinator_s3_failure_suppresses_only_matching_website_route(self):
        external = ProviderCandidate(
            url="https://jobs.lever.co/acme",
            source_kind="external_apply",
            source_url="https://jobs.lever.co/acme",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="lever",
        )
        provider = ProviderCandidate(
            url="https://jobs.ashbyhq.com/acme",
            source_kind="targeted_board_search",
            source_url="https://www.bing.com/search?q=acme",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="ashby",
            query='site:jobs.ashbyhq.com "Acme"',
            result_rank=1,
        )
        website_calls = []
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                external_apply_url=external.url,
                job_title="Engineer",
            )
        )
        context.career_page_url = "https://acme.example/careers"
        context.stage_results.append(
            make_stage_result(
                "hiring_identity_resolution",
                "failed",
                reason_code="COMPANY_IDENTITY_AMBIGUOUS",
            )
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=_coordinator_with_routes(
                external=(external,),
                provider=(provider,),
                website_calls=website_calls,
            ),
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(website_calls, [])
        routes = execution.trace["routes"]
        self.assertEqual(routes["website_career"]["status"], "suppressed")
        self.assertEqual(routes["provider_search"]["status"], "completed")
        self.assertEqual(len(execution.updates["job_board_portfolio"].boards), 2)

    def test_coordinator_shared_url_does_not_borrow_external_authority(self):
        url = "https://jobs.lever.co/acme"
        external = ProviderCandidate(
            url=url,
            source_kind="external_apply",
            source_url=url,
            company_name="Acme",
            target_title="Engineer",
            provider_hint="lever",
        )
        searched = ProviderCandidate(
            url=url,
            source_kind="targeted_board_search",
            source_url="https://www.bing.com/search?q=acme",
            company_name="Acme",
            target_title="Engineer",
            provider_hint="lever",
            query='site:jobs.lever.co "Acme"',
            result_rank=1,
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                external_apply_url=url,
                job_title="Engineer",
            )
        )

        execution = JobBoardDiscoveryStage(
            _NoNetworkService(),
            DEFAULT_PROVIDER_REGISTRY,
            candidate_coordinator=_coordinator_with_routes(
                external=(external,), provider=(searched,)
            ),
            enable_parallel_candidate_discovery=True,
            candidate_discovery_engine="coordinator_v2",
        ).run(context)

        evidence = execution.updates["job_board_portfolio"].route_evidence
        by_route = {item.route_kind: item for item in evidence}
        self.assertTrue(by_route["external_apply"].authorized)
        self.assertFalse(by_route["provider_search"].authorized)
        attributions = execution.trace["candidate_attributions"]
        self.assertEqual(
            attributions,
            [{"url": url, "routes": ["external_apply", "provider_search"]}],
        )

    def test_provider_published_employer_can_bind_descriptor_name_without_s2(self):
        evidence = ProviderPublishedEmployerEvidence(
            employer_name="Slant",
            descriptor_terms=("crm",),
            evidence_url="https://api.ashbyhq.com/posting-api/job-board/slant",
            opening_url="https://jobs.ashbyhq.com/slant/role-123",
            extraction_method="about_heading_self_description",
        )
        candidate = ProviderCandidate(
            url="https://jobs.ashbyhq.com/slant",
            source_kind="verified_tenant_probe",
            source_url="https://www.linkedin.com/company/slantcrmforadvisors",
            company_name="Slant CRM",
            target_title="Product Designer",
            target_location="Lehi, UT",
            provider_hint="ashby",
            provider_employer_evidence=evidence,
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Slant CRM",
                linkedin_company_url=(
                    "https://www.linkedin.com/company/slantcrmforadvisors"
                ),
                job_title="Product Designer",
                job_location="Lehi, UT",
            )
        )

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
            execution.updates["provider_identity"].verification_method,
            "provider_published_employer",
        )
        self.assertEqual(
            execution.updates["provider_identity"].evidence_url,
            "https://api.ashbyhq.com/posting-api/job-board/slant",
        )
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)

    def test_provider_published_employer_rejects_descriptor_or_tenant_collision(self):
        for employer, descriptors, opening_url in (
            ("Slant", ("ai",), "https://jobs.ashbyhq.com/slant/role-123"),
            ("Other", ("crm",), "https://jobs.ashbyhq.com/slant/role-123"),
            ("Slant", ("crm",), "https://jobs.ashbyhq.com/other/role-123"),
        ):
            with self.subTest(
                employer=employer,
                descriptors=descriptors,
                opening_url=opening_url,
            ):
                evidence = ProviderPublishedEmployerEvidence(
                    employer_name=employer,
                    descriptor_terms=descriptors,
                    evidence_url=(
                        "https://api.ashbyhq.com/posting-api/job-board/slant"
                    ),
                    opening_url=opening_url,
                    extraction_method="about_heading_self_description",
                )
                candidate = ProviderCandidate(
                    url="https://jobs.ashbyhq.com/slant",
                    source_kind="verified_tenant_probe",
                    source_url=(
                        "https://www.linkedin.com/company/slantcrmforadvisors"
                    ),
                    company_name="Slant CRM",
                    target_title="Product Designer",
                    target_location="Lehi, UT",
                    provider_hint="ashby",
                    provider_employer_evidence=evidence,
                )
                context = PipelineContext.from_company(
                    CompanyInput(
                        company_name="Slant CRM",
                        job_title="Product Designer",
                        job_location="Lehi, UT",
                    )
                )

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

    def test_verified_tenant_probe_does_not_bind_exact_official_website_domain(self):
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

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")
        self.assertFalse(
            execution.updates["job_board_portfolio"].route_evidence[0].authorized
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

    def test_tenant_probe_without_provider_employer_stays_as_untrusted_candidate(self):
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

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://jobs.ashbyhq.com/acme",
        )
        self.assertFalse(
            execution.updates["provider_identity"].relationship_verified
        )
        self.assertEqual(
            execution.result.evidence[0]["field"],
            "candidate_job_board_url",
        )
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(len(portfolio.boards), 1)
        self.assertEqual(len(portfolio.route_evidence), 1)
        self.assertFalse(portfolio.route_evidence[0].authorized)

    def test_exhaustive_mode_runs_search_after_official_career_route(self):
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
            evaluate_all_candidate_routes=True,
        ).run(context)

        self.assertEqual(events, ["website_direct", "search"])
        self.assertEqual(execution.result.status, "success")
        fallback = execution.trace["candidate_route_probe"]
        self.assertEqual(
            fallback["candidate_discovery"]["strategy"],
            "exhaustive_route_evaluation",
        )
        self.assertEqual(
            fallback["candidate_discovery"]["waves"]["search"]["sources"][0][
                "status"
            ],
            "success",
        )

    def test_non_exhaustive_mode_skips_search_after_official_provider_board(self):
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
        fallback = execution.trace["parallel_candidate_fallback"]
        self.assertEqual(
            fallback["candidate_discovery"]["waves"]["search"]["reason"],
            "website_provider_board",
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
        self.assertEqual(routes["provider_search"]["relationship_verified_count"], 0)
        self.assertEqual(routes["provider_search"]["provider_verified_count"], 1)
        self.assertFalse(routes["website_career"]["input_available"])
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(len(portfolio.boards), 2)
        search_routes = [
            route
            for route in portfolio.route_evidence
            if route.route_kind == "provider_search"
        ]
        self.assertEqual(len(search_routes), 1)
        self.assertFalse(search_routes[0].authorized)

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
            result=make_stage_result(
                "job_board_discovery",
                "partial",
                reason_code="COMPANY_IDENTITY_AMBIGUOUS",
            ),
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

        self.assertEqual(execution.result.status, "success")
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
        self.assertEqual(
            identity.verification_method,
            "verified_first_party_handoff",
        )

    def test_legacy_merge_does_not_republish_existing_provisional_identity(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="City Example", job_title="Analyst")
        )
        context.career_page_url = "https://jobs.example.net/careers/city"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="City Example",
            hiring_entity_name="City Example",
            relationship_type="same_entity",
            verification_method="provisional_navigation_handoff",
            verified=True,
            evidence_url=context.career_page_url,
        )
        board = DiscoveredJobBoard(
            board=JobBoard(
                context.career_page_url,
                "generic",
                "url:https://jobs.example.net/careers/city",
            ),
            detection_method="verified_first_party_action",
            evidence_url=context.career_page_url,
        )
        relationship = HiringRelationshipEvidence(
            source_company_name="City Example",
            hiring_entity_name="City Example",
            provider="generic",
            tenant=board.board.identifier,
            evidence_type="first_party_handoff",
            evidence_url=context.career_page_url,
            strength=100,
            verified=True,
        )
        portfolio = JobBoardPortfolio(
            boards=(board,),
            eligible_set_complete=True,
            route_evidence=(
                JobBoardRouteEvidence(
                    provider="generic",
                    canonical_board_url=board.board.url,
                    route_kind="website_career",
                    source_kind="legacy_website_career",
                    hiring_relationship=relationship,
                ),
            ),
        )
        candidate_execution = StageExecution(
            result=make_stage_result("job_board_discovery", "success"),
            updates={
                "job_list_page_url": board.board.url,
                "provider": "generic",
                "discovered_job_board": board,
                "job_board_portfolio": portfolio,
                "provider_identity": ProviderIdentity(
                    hiring_entity_name="City Example",
                    provider="generic",
                    tenant=board.board.identifier,
                    canonical_board_url=board.board.url,
                    evidence_url=context.career_page_url,
                    verification_method="verified_first_party_handoff",
                    relationship_verified=True,
                ),
            },
            trace={"route_evaluation": {"schema_version": "1.0", "routes": {}}},
        )
        legacy_execution = StageExecution(
            result=make_stage_result("job_board_discovery", "success"),
            updates=dict(candidate_execution.updates),
            trace={},
        )

        execution = _merge_legacy_website_route(
            context,
            candidate_execution,
            legacy_execution,
            DEFAULT_PROVIDER_REGISTRY,
        )

        self.assertNotIn("hiring_identity_evidence", execution.updates)
        self.assertNotIn("hiring_entity_name", execution.updates)

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
        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertFalse(
            execution.updates["provider_identity"].relationship_verified
        )
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(
            {board.board.identifier for board in portfolio.boards},
            {"acme", "notion"},
        )
        self.assertFalse(any(route.authorized for route in portfolio.route_evidence))

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

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
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
        portfolio = execution.updates["job_board_portfolio"]
        self.assertIn(
            "acme",
            {board.board.identifier for board in portfolio.boards},
        )
        self.assertFalse(any(route.authorized for route in portfolio.route_evidence))

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

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertFalse(execution.trace["relationship_evidence"]["verified"])
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertIn("job_board_portfolio", execution.updates)
        self.assertFalse(
            any(
                route.authorized
                for route in execution.updates["job_board_portfolio"].route_evidence
            )
        )

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

    def _assert_successfactors_identity_promotion(
        self,
        *,
        company,
        tenant,
        requisition,
        title,
        location,
    ):
        context = _successfactors_continuity_context(company, title, location)
        prior = context.provider_identity

        execution = OpeningMatchStage(
            _SuccessFactorsContinuityOpeningService(tenant, requisition),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        provider = execution.updates["provider_identity"]
        opening = execution.updates["opening_identity"]
        selection = execution.updates["opening_selection_evidence"]
        self.assertEqual(provider.provider, "successfactors")
        self.assertEqual(provider.tenant, tenant)
        self.assertEqual(
            provider.canonical_board_url,
            f"https://career5.successfactors.eu/career?company={tenant}",
        )
        self.assertEqual(provider.hiring_entity_name, prior.hiring_entity_name)
        self.assertEqual(provider.evidence_url, prior.evidence_url)
        self.assertEqual(provider.verification_method, prior.verification_method)
        self.assertTrue(provider.relationship_verified)
        self.assertEqual(opening.provider, "successfactors")
        self.assertEqual(opening.tenant, tenant)
        self.assertEqual(selection.provider, "successfactors")
        self.assertEqual(selection.tenant, tenant)
        self.assertEqual(selection.location, location)

    def test_arkema_generic_relationship_promotes_verified_successfactors_opening(self):
        self._assert_successfactors_identity_promotion(
            company="Arkema",
            tenant="arkema",
            requisition="1401455133",
            title="Human Resources Manager Job",
            location="Beaumont, TX",
        )

    def test_aramark_generic_relationship_promotes_verified_successfactors_opening(self):
        self._assert_successfactors_identity_promotion(
            company="Aramark",
            tenant="aramark",
            requisition="1404601400",
            title="HR Manager",
            location="Indianapolis, IN",
        )

    def test_cintas_generic_relationship_promotes_verified_successfactors_opening(self):
        self._assert_successfactors_identity_promotion(
            company="Cintas",
            tenant="cintas",
            requisition="1373711200",
            title="Human Resources Manager II",
            location="Fort Myers, FL",
        )

    def test_custom_domain_successfactors_identity_promotes_from_page_evidence(self):
        context = _successfactors_continuity_context(
            "Cintas",
            "Human Resources Manager II",
            "Fort Myers, FL",
        )
        board_url = "https://careers.cintas.example/search/"
        opening_url = "https://careers.cintas.example/job/HR-Manager-II/1373711200/"

        class _CustomDomainOpeningService:
            def match_opening(self, job_list_url, target_title=None, target_location=None):
                return opening_url, job_list_url, {
                    "selected": {
                        "url": opening_url,
                        "title": target_title,
                        "location": target_location,
                    },
                    "provider_api": {
                        "provider": "successfactors",
                        "adapter": "successfactors",
                        "provider_detection": {
                            "method": "page_evidence",
                            "provider": "successfactors",
                            "url": board_url,
                        },
                        "candidates": [{"url": opening_url}],
                        "inventory": {
                            "source": "native_adapter",
                            "complete": True,
                        },
                        "adapter_trace": {
                            "board_identity": {
                                "provider": "successfactors",
                                "url": board_url,
                                "identifier": "custom:CINTAS",
                            },
                            "detail_verified_opening_urls": [opening_url],
                        },
                    },
                }

        execution = OpeningMatchStage(
            _CustomDomainOpeningService(),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertEqual(execution.updates["provider_identity"].provider, "successfactors")
        self.assertEqual(execution.updates["provider_identity"].tenant, "custom:CINTAS")
        self.assertEqual(execution.updates["opening_identity"].tenant, "custom:CINTAS")

    def _assert_successfactors_identity_promotion_rejected(
        self,
        mutate_trace,
        *,
        relationship_verified=True,
    ):
        context = _successfactors_continuity_context(
            "Acme",
            "HR Manager",
            "Indianapolis, IN",
        )
        if not relationship_verified:
            context.provider_identity = ProviderIdentity(
                hiring_entity_name="Acme",
                provider="generic",
                tenant="url:https://careers.acme.example/jobs",
                canonical_board_url="https://careers.acme.example/jobs",
                evidence_url="https://careers.acme.example/jobs",
                verification_method="linked_url_only",
                relationship_verified=False,
            )

        execution = OpeningMatchStage(
            _SuccessFactorsContinuityOpeningService(
                "acme",
                "12345",
                mutate_trace=mutate_trace,
            ),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertEqual(execution.updates["provider_identity"].provider, "generic")
        self.assertNotIn("opening_identity", execution.updates)
        self.assertNotIn("opening_selection_evidence", execution.updates)

    def test_native_identity_promotion_rejects_missing_tenant(self):
        self._assert_successfactors_identity_promotion_rejected(
            lambda trace, board, opening: trace["provider_api"]["adapter_trace"][
                "board_identity"
            ].update(identifier="")
        )

    def test_native_identity_promotion_rejects_selected_url_outside_inventory(self):
        def replace_candidate(trace, board, opening):
            trace["provider_api"]["candidates"] = [
                {"url": f"{board}&career_job_req_id=99999"}
            ]

        self._assert_successfactors_identity_promotion_rejected(replace_candidate)

    def test_native_identity_promotion_rejects_selected_url_mismatch(self):
        def replace_selected(trace, board, opening):
            trace["selected"]["url"] = f"{board}&career_job_req_id=99999"

        self._assert_successfactors_identity_promotion_rejected(replace_selected)

    def test_native_identity_promotion_rejects_unverified_generic_relationship(self):
        self._assert_successfactors_identity_promotion_rejected(
            lambda trace, board, opening: None,
            relationship_verified=False,
        )

    def test_native_identity_promotion_rejects_cross_provider_board_trace(self):
        def replace_provider(trace, board, opening):
            trace["provider_api"]["adapter_trace"]["board_identity"][
                "provider"
            ] = "greenhouse"

        self._assert_successfactors_identity_promotion_rejected(replace_provider)

    def test_native_identity_promotion_rejects_cross_tenant_board_trace(self):
        def replace_tenant(trace, board, opening):
            trace["provider_api"]["adapter_trace"]["board_identity"][
                "identifier"
            ] = "other"

        self._assert_successfactors_identity_promotion_rejected(replace_tenant)

    def test_native_identity_promotion_rejects_trace_only_search_candidate(self):
        def move_candidate_outside_provider_inventory(trace, board, opening):
            trace["candidates"] = trace["provider_api"].pop("candidates")

        self._assert_successfactors_identity_promotion_rejected(
            move_candidate_outside_provider_inventory
        )

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

    def test_complete_provider_inventory_can_authorize_unverified_board_no_match(self):
        board = DiscoveredJobBoard(
            board=JobBoard(
                "https://job-boards.greenhouse.io/acme",
                "greenhouse",
                "acme",
                replay_safe=True,
            ),
            detection_method="targeted_search",
            evidence_url="https://job-boards.greenhouse.io/acme",
        )
        relationship = HiringRelationshipEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            provider="greenhouse",
            tenant="acme",
            evidence_type="unverified_candidate",
            evidence_url=board.board.url,
            strength=0,
            verified=False,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Staff Engineer")
        )
        context.hiring_entity_name = "Acme"
        context.job_list_page_url = board.board.url
        context.discovered_job_board = board
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="greenhouse",
            tenant="acme",
            canonical_board_url=board.board.url,
            evidence_url=board.evidence_url,
            verification_method="linked_url_only",
            relationship_verified=False,
        )
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(board,),
            eligible_set_complete=False,
            route_evidence=(
                JobBoardRouteEvidence(
                    provider="greenhouse",
                    canonical_board_url=board.board.url,
                    route_kind="provider_search",
                    source_kind="targeted_board_search",
                    hiring_relationship=relationship,
                ),
            ),
        )

        execution = OpeningMatchStage(
            _ProviderInventoryNoMatchService("Acme"),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")
        self.assertTrue(
            execution.updates["provider_identity"].relationship_verified
        )
        self.assertEqual(
            execution.updates[
                "hiring_identity_evidence"
            ].verification_method,
            "provider_inventory",
        )

        rejected = OpeningMatchStage(
            _ProviderInventoryNoMatchService("Other Company"),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)
        self.assertEqual(
            rejected.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertNotIn("provider_identity", rejected.updates)

    def test_provider_board_employer_conflict_overrides_first_party_route(self):
        board = DiscoveredJobBoard(
            board=JobBoard(
                "https://www.governmentjobs.com/careers/example",
                "governmentjobs",
                "example",
                replay_safe=True,
            ),
            detection_method="linked_url_evidence",
            evidence_url="https://www.governmentjobs.com/careers/example",
        )
        relationship = HiringRelationshipEvidence(
            source_company_name="Example Limited",
            hiring_entity_name="Example Limited",
            provider="governmentjobs",
            tenant="example",
            evidence_type="first_party_handoff",
            evidence_url=board.board.url,
            strength=85,
            verified=True,
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example Limited",
                job_title="Staff Engineer",
            )
        )
        context.hiring_entity_name = "Example Limited"
        context.job_list_page_url = board.board.url
        context.discovered_job_board = board
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(board,),
            eligible_set_complete=True,
            route_evidence=(
                JobBoardRouteEvidence(
                    provider="governmentjobs",
                    canonical_board_url=board.board.url,
                    route_kind="website_career",
                    source_kind="first_party_ats_link",
                    hiring_relationship=relationship,
                ),
            ),
        )

        execution = OpeningMatchStage(
            _BoardEmployerNoMatchService("City of Example"),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertTrue(execution.trace["provider_employer_identity_conflict"])
        self.assertNotIn("open_position_url", execution.updates)

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

    def test_same_name_search_tenant_is_ranked_but_remains_untrusted(self):
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
        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "linked_url_only",
        )
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(
            {board.board.identifier for board in portfolio.boards},
            {"acme", "notion"},
        )

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
        self.assertFalse(relationship["verified"])
        self.assertEqual(relationship["evidence_type"], "unverified_candidate")
        self.assertEqual(execution.result.status, "partial")
        self.assertIn("job_board_portfolio", execution.updates)

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
            "unverified_candidate",
        )
        self.assertFalse(execution.trace["relationship_evidence"]["verified"])
        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(
            {board.board.identifier for board in portfolio.boards},
            {"acme", "notion"},
        )
        self.assertFalse(any(route.authorized for route in portfolio.route_evidence))

    def test_targeted_opening_priority_does_not_authorize_search_tenant(self):
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
        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertFalse(execution.trace["relationship_evidence"]["verified"])
        portfolio = execution.updates["job_board_portfolio"]
        self.assertEqual(
            {board.board.identifier for board in portfolio.boards},
            {"acme", "notion"},
        )
        self.assertFalse(any(route.authorized for route in portfolio.route_evidence))


if __name__ == "__main__":
    unittest.main()
