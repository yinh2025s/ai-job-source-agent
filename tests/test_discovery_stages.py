import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.company_discovery_evidence import (
    VerifiedCareerEvidence,
    VerifiedCompanyDiscoveryEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.candidate_portfolio import CompositeCandidateDiscovery
from job_source_agent.errors import DiscoveryError
from job_source_agent.homepage_navigation import HomepageNavigationEvidence
from job_source_agent.identity_continuity import HiringIdentityEvidence, ProviderIdentity
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.models import (
    STAGE_HIRING_IDENTITY_RESOLUTION,
    CompanyInput,
    StageResult,
)
from job_source_agent.stages import (
    CareerDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    PipelineStageRunner,
)
from job_source_agent.stages.discovery import (
    _is_transient_generic_board_query_variant,
    _stored_provider_relationship,
    _stored_tenant_matches_hiring_entity,
)


class FakeDiscoveryService:
    def find_career_page(
        self,
        company_website_url,
        company_name=None,
        preferred_url=None,
        target_title=None,
        target_location=None,
    ):
        return f"{company_website_url}/careers", {"method": "fake-career"}

    def find_job_board(self, career_page_url, company_name=None, target_location=None):
        return "https://boards.greenhouse.io/acme", {"method": "fake-board"}

    def match_opening(self, job_list_url, target_title=None, target_location=None):
        return f"{job_list_url}/jobs/123", job_list_url, {"method": "fake-match"}


class DiscoveryStageTests(unittest.TestCase):
    @staticmethod
    def _no_public_context() -> PipelineContext:
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.company_website_url = "https://acme.example"
        context.stage_results.extend(
            [
                StageResult(stage="website_resolution", status="success"),
                StageResult(
                    stage="career_discovery",
                    status="failed",
                    reason_code="CAREER_PAGE_NOT_FOUND",
                ),
            ]
        )
        context.trace.setdefault("stages", {})["career_discovery"] = {
            "homepage_url": "https://acme.example",
            "homepage_fetch_error": None,
            "transport_budget": {
                "dispatched": 12,
                "rejected": 0,
                "exhausted": False,
            },
            "bundle_navigation_discovery": {"candidate_urls": []},
            "sitemap_discovery": {
                "candidate_count": 0,
                "fanout_limit_reached": False,
                "sitemaps_checked": [
                    {"url": "https://acme.example/sitemap.xml", "url_count": 3}
                ],
            },
            "search_discovery": {
                "candidates": [],
                "stopped_reason": "no_valid_candidates",
                "queries": [
                    {"error": None, "candidates": [], "result_count": 10}
                ],
            },
            "candidate_fetch_errors": [
                {
                    "url": "https://careers.acme.example",
                    "origin": "subdomain_probe",
                    "retryable": True,
                }
            ],
        }
        return context

    def test_complete_bounded_official_surface_miss_is_no_public_openings(self):
        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
        ).run(self._no_public_context())

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "NO_PUBLIC_OPENINGS")
        self.assertEqual(
            execution.result.evidence[0]["disposition"],
            "verified_no_public_recruiting_surface",
        )

    def test_no_public_terminal_replays_without_runtime_transport_capability(self):
        context = self._no_public_context()
        context.trace["stages"]["career_discovery"].pop("transport_budget")

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.reason_code, "NO_PUBLIC_OPENINGS")

    def test_no_public_terminal_fails_closed_on_incomplete_official_probe(self):
        context = self._no_public_context()
        context.trace["stages"]["career_discovery"]["candidate_fetch_errors"].append(
            {
                "url": "https://acme.example/careers",
                "origin": "page_link",
                "retryable": True,
            }
        )

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertIsNone(execution.result.reason_code)

    def test_no_public_terminal_rejects_homepage_company_identity_mismatch(self):
        context = self._no_public_context()
        context.trace["stages"]["career_discovery"][
            "homepage_career_surface_verification"
        ] = {
            "verified": False,
            "reason": "homepage company identity mismatch",
        }

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
        ).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertIsNone(execution.result.reason_code)

    def test_unlinked_third_party_handoff_is_external_terminal(self):
        class UnlinkedHandoffService(FakeDiscoveryService):
            def find_job_board(self, *args, **kwargs):
                raise DiscoveryError(
                    "job_board_not_found",
                    "No linked board",
                    trace={
                        "unlinked_third_party_handoffs": [
                            {
                                "platform": "indeed",
                                "disposition": "unlinked_third_party_recruiting_handoff",
                            }
                        ]
                    },
                )

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/employment"

        execution = JobBoardDiscoveryStage(UnlinkedHandoffService()).run(context)

        self.assertEqual(
            execution.result.reason_code,
            "UNVERIFIABLE_THIRD_PARTY_HANDOFF",
        )
        self.assertEqual(execution.result.owner, "external")

    def test_first_party_generic_search_filter_preserves_stable_board_identity(self):
        self.assertTrue(
            _is_transient_generic_board_query_variant(
                "https://jobs.acme.example/search-jobs",
                "https://jobs.acme.example/search-jobs?orgIds=1127&k=Registered+Nurse",
                verified_first_party_url="https://jobs.acme.example/careers",
            )
        )

    def test_cross_site_organization_filter_is_not_treated_as_transient(self):
        self.assertFalse(
            _is_transient_generic_board_query_variant(
                "https://jobs.vendor.example/search-jobs",
                "https://jobs.vendor.example/search-jobs?orgIds=1127&k=Engineer",
                verified_first_party_url="https://acme.example/careers",
            )
        )

    class _EvidenceStore:
        def __init__(self, record=None):
            self.record = record
            self.saved = []
            self.invalidated = []

        def load(self, company_name, linkedin_company_url):
            return self.record

        def save(self, company_name, linkedin_company_url, **layers):
            self.saved.append((company_name, linkedin_company_url, layers))

        def invalidate(self, company_name, linkedin_company_url, **kwargs):
            self.invalidated.append((company_name, linkedin_company_url, kwargs))

    @staticmethod
    def _stored_career_record():
        return VerifiedCompanyDiscoveryEvidence(
            company_name="acme",
            linkedin_company_url="https://www.linkedin.com/company/acme",
            website=VerifiedWebsiteEvidence(
                url="https://acme.example",
                source="verified_resolver",
                evidence_url="https://www.linkedin.com/company/acme",
                observed_at=1.0,
            ),
            career=VerifiedCareerEvidence(
                url="https://acme.example/careers",
                website_url="https://acme.example",
                source="first_party_navigation",
                evidence_url="https://acme.example",
                observed_at=1.0,
            ),
        )

    def test_stored_career_is_revalidated_and_refreshed(self):
        class CapturingCareer(FakeDiscoveryService):
            preferred_url = None

            def find_career_page(self, *args, preferred_url=None, **kwargs):
                self.preferred_url = preferred_url
                return preferred_url, {
                    "selected": {"url": preferred_url, "reason": "same-site career link"}
                }

        store = self._EvidenceStore(self._stored_career_record())
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(service, store).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.preferred_url, "https://acme.example/careers")
        self.assertEqual(store.saved[0][2]["career"].url, service.preferred_url)

    def test_stored_website_and_career_are_revalidated_when_s2_has_no_output(self):
        class CapturingCareer(FakeDiscoveryService):
            website_url = None
            preferred_url = None

            def find_career_page(self, website_url, *args, preferred_url=None, **kwargs):
                self.website_url = website_url
                self.preferred_url = preferred_url
                return preferred_url, {
                    "selected": {"url": preferred_url, "reason": "same-site career link"}
                }

        store = self._EvidenceStore(self._stored_career_record())
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(service, store).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.website_url, "https://acme.example")
        self.assertEqual(service.preferred_url, "https://acme.example/careers")
        self.assertTrue(
            execution.trace["stored_company_discovery_candidate"]["revalidated"]
        )
        self.assertTrue(execution.updates["hiring_identity_evidence"].verified)
        self.assertEqual(
            execution.updates["hiring_identity_evidence"].verification_method,
            "revalidated_stored_career",
        )
        self.assertEqual(execution.updates["hiring_entity_name"], "Acme")
        self.assertEqual(store.saved, [])

    def test_stored_website_is_only_a_lead_until_same_site_career_is_verified(self):
        class CapturingCareer(FakeDiscoveryService):
            website_url = None
            preferred_url = "unset"

            def find_career_page(self, website_url, *args, preferred_url=None, **kwargs):
                self.website_url = website_url
                self.preferred_url = preferred_url
                return f"{website_url}/careers", {
                    "selected": {
                        "url": f"{website_url}/careers",
                        "reason": "same-site career link",
                    }
                }

        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
        )
        store = self._EvidenceStore(record)
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(service, store).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.website_url, "https://acme.example")
        self.assertIsNone(service.preferred_url)
        self.assertEqual(
            execution.updates["hiring_identity_evidence"].verification_method,
            "revalidated_stored_website_career",
        )
        self.assertEqual(store.saved[0][2]["career"].url, "https://acme.example/careers")

    def test_stored_website_cross_site_career_does_not_restore_identity(self):
        class CrossSiteCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                return "https://jobs.other.example/acme", {
                    "selected": {
                        "url": "https://jobs.other.example/acme",
                        "reason": "provider career page",
                    }
                }

        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
        )
        store = self._EvidenceStore(record)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(CrossSiteCareer(), store).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertNotIn("hiring_identity_evidence", execution.updates)
        self.assertEqual(store.saved, [])

    def test_stored_career_redirect_does_not_recover_hiring_identity(self):
        class RedirectedCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                return "https://jobs.example.net/other", {
                    "selected": {
                        "url": "https://jobs.example.net/other",
                        "reason": "provider career page",
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(
            RedirectedCareer(),
            self._EvidenceStore(self._stored_career_record()),
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertNotIn("hiring_identity_evidence", execution.updates)
        self.assertNotIn("revalidated_stored_career_identity", execution.trace)

    def test_stored_provider_candidate_defers_s4_when_current_website_is_unavailable(self):
        class UnexpectedCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("S4 must not consume the provider revalidation budget")

        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=base.career,
            provider_boards=(
                VerifiedProviderBoardEvidence(
                    provider="greenhouse",
                    tenant="acme",
                    canonical_board_url="https://job-boards.greenhouse.io/acme",
                    relationship_evidence_url="https://acme.example/careers",
                    verification_method="first_party_handoff",
                    source="first_party_handoff",
                    observed_at=1.0,
                ),
            ),
        )
        store = self._EvidenceStore(record)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(UnexpectedCareer(), store).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(
            execution.trace["scheduler"]["reason"],
            "stored_provider_candidate_deferred_to_s5",
        )

    def test_stored_provider_career_defers_s4_for_current_s5_revalidation(self):
        class UnexpectedCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("S4 must not refetch a recognized ATS career lead")

        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=VerifiedCareerEvidence(
                url=(
                    "https://recruiting.paylocity.com/recruiting/jobs/All/"
                    "18300151-3b4d-4044-912d-501cebb26321/Panacea-Healthcare-Solutions"
                ),
                website_url=base.website.url,
                source="verified_career_search",
                evidence_url=base.website.url,
                observed_at=1.0,
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )
        context.company_website_url = "https://acme.example"

        execution = CareerDiscoveryStage(
            UnexpectedCareer(),
            self._EvidenceStore(record),
        ).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(
            execution.trace["scheduler"]["reason"],
            "stored_provider_candidate_deferred_to_s5",
        )

    def test_first_party_stored_provider_career_still_revalidates_in_s4(self):
        class CapturingCareer(FakeDiscoveryService):
            preferred_url = None

            def find_career_page(self, *args, preferred_url=None, **kwargs):
                self.preferred_url = preferred_url
                return preferred_url, {
                    "selected": {"url": preferred_url, "reason": "provider career page"}
                }

        base = self._stored_career_record()
        career_url = "https://jobs.lever.co/acme"
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=VerifiedCareerEvidence(
                url=career_url,
                website_url=base.website.url,
                source="first_party_navigation",
                evidence_url=base.website.url,
                observed_at=1.0,
            ),
        )
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )
        context.company_website_url = "https://acme.example"

        execution = CareerDiscoveryStage(
            service,
            self._EvidenceStore(record),
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.preferred_url, career_url)

    def test_legacy_s5_mode_does_not_defer_stored_provider_candidate(self):
        class CapturingCareer(FakeDiscoveryService):
            called = False

            def find_career_page(self, *args, preferred_url=None, **kwargs):
                self.called = True
                return preferred_url, {
                    "selected": {"url": preferred_url, "reason": "provider career page"}
                }

        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=VerifiedCareerEvidence(
                url="https://jobs.lever.co/acme",
                website_url=base.website.url,
                source="verified_career_search",
                evidence_url=base.website.url,
                observed_at=1.0,
            ),
        )
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(
            service,
            self._EvidenceStore(record),
            enable_parallel_candidate_discovery=False,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertTrue(service.called)

    def test_generic_nonretryable_stored_career_rejection_retains_career_layer(self):
        class MissingCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "career_page_not_found",
                    "gone",
                    trace={"candidate": {"url": "https://acme.example/careers"}},
                )

        store = self._EvidenceStore(self._stored_career_record())
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(MissingCareer(), store).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(store.invalidated, [])

    def test_explicit_stored_career_identity_rejection_invalidates_layer(self):
        class WrongCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "career_page_not_found",
                    "identity conflict",
                    trace={"stored_candidate_identity_rejected": True},
                )

        store = self._EvidenceStore(self._stored_career_record())
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = CareerDiscoveryStage(WrongCareer(), store).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(store.invalidated[0][2]["layer"], "career")

    def test_retryable_stored_career_rejection_retains_evidence(self):
        class TimedOutCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "career_page_not_found",
                    "temporarily unavailable",
                    trace={
                        "fetch_failure": {
                            "retryable": True,
                            "url": "https://acme.example/careers",
                        }
                    },
                )

        store = self._EvidenceStore(self._stored_career_record())
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        CareerDiscoveryStage(TimedOutCareer(), store).run(context)

        self.assertEqual(store.invalidated, [])

    def test_verified_first_party_provider_handoff_is_persisted(self):
        class BoardService:
            def find_job_board_with_evidence(
                self, career_page_url, company_name=None, target_location=None
            ):
                board = JobBoard(
                    provider="greenhouse",
                    identifier="acme",
                    url="https://boards.greenhouse.io/acme",
                )
                return board.url, {}, DiscoveredJobBoard(
                    board=board,
                    detection_method="linked_url_evidence",
                    evidence_url=board.url,
                    relationship_evidence_url=career_page_url,
                )

        store = self._EvidenceStore()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )
        context.company_website_url = "https://acme.example"
        context.career_page_url = "https://acme.example/careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="official_website",
            verified=True,
            evidence_url="https://acme.example",
        )
        context.hiring_entity_name = "Acme"

        execution = JobBoardDiscoveryStage(
            BoardService(),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        saved = store.saved[0][2]["provider_board"]
        self.assertEqual(saved.provider, "greenhouse")
        self.assertEqual(saved.tenant, "acme")
        self.assertEqual(saved.source, "first_party_handoff")

    def test_tenant_name_match_alone_is_not_persisted(self):
        class BoardService:
            def find_job_board_with_evidence(
                self, career_page_url, company_name=None, target_location=None
            ):
                board = JobBoard(
                    provider="greenhouse",
                    identifier="acme",
                    url="https://boards.greenhouse.io/acme",
                )
                return board.url, {}, DiscoveredJobBoard(
                    board=board,
                    detection_method="search_result",
                    evidence_url=board.url,
                )

        store = self._EvidenceStore()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )
        context.company_website_url = "https://acme.example"
        context.career_page_url = "https://acme.example/careers"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="official_website",
            verified=True,
            evidence_url="https://acme.example",
        )
        context.hiring_entity_name = "Acme"

        execution = JobBoardDiscoveryStage(
            BoardService(),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "tenant_name_match",
        )
        self.assertEqual(store.saved, [])

    def test_stored_provider_board_is_only_an_unverified_s5_candidate(self):
        record = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=record.company_name,
            linkedin_company_url=record.linkedin_company_url,
            website=record.website,
            career=record.career,
            provider_boards=(
                VerifiedProviderBoardEvidence(
                    provider="greenhouse",
                    tenant="acme",
                    canonical_board_url="https://job-boards.greenhouse.io/acme",
                    relationship_evidence_url="https://acme.example/careers",
                    verification_method="first_party_handoff",
                    source="first_party_handoff",
                    observed_at=1.0,
                ),
            ),
        )
        store = self._EvidenceStore(record)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_title="Engineer",
            )
        )

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://job-boards.greenhouse.io/acme",
        )
        self.assertFalse(
            execution.updates["provider_identity"].relationship_verified
        )
        self.assertEqual(
            execution.trace["selected"]["source_kind"],
            "stored_verified_provider_board",
        )
        self.assertFalse(
            execution.updates["job_board_portfolio"].eligible_set_complete
        )
        self.assertEqual(
            execution.trace["candidate_discovery"]["waves"]["search"]["reason"],
            "stored_candidate_requires_inventory_revalidation",
        )

    def test_stored_provider_career_is_only_an_unverified_s5_candidate(self):
        base = self._stored_career_record()
        career_url = (
            "https://recruiting.paylocity.com/recruiting/jobs/All/"
            "18300151-3b4d-4044-912d-501cebb26321/Panacea-Healthcare-Solutions"
        )
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=VerifiedCareerEvidence(
                url=career_url,
                website_url=base.website.url,
                source="verified_career_search",
                evidence_url=base.website.url,
                observed_at=1.0,
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_title="Engineer",
            )
        )

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
            company_discovery_evidence_store=self._EvidenceStore(record),
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["provider"], "paylocity")
        self.assertEqual(execution.updates["job_list_page_url"], career_url)
        self.assertEqual(
            execution.trace["selected"]["source_kind"],
            "stored_verified_career_provider",
        )
        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertFalse(execution.updates["job_board_portfolio"].eligible_set_complete)

    def test_generic_stored_career_is_not_promoted_to_s5(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_title="Engineer",
            )
        )

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
            company_discovery_evidence_store=self._EvidenceStore(
                self._stored_career_record()
            ),
        ).run(context)

        self.assertNotEqual(
            execution.trace.get("selected", {}).get("source_kind"),
            "stored_verified_career_provider",
        )

    def test_complete_stored_career_inventory_with_wrong_tenant_is_identity_ambiguous(self):
        class EmptyInventoryService:
            def match_discovered_board(self, discovered, *args):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "source": "native_adapter",
                            "status": "verified_filtered_empty",
                            "scope": "title_filtered",
                            "complete": True,
                            "candidate_count": 0,
                        },
                        "adapter_trace": {
                            "inventory_complete": True,
                            "inventory_scope": "title_filtered",
                        },
                    }
                }

        base = self._stored_career_record()
        career_url = (
            "https://recruiting.paylocity.com/recruiting/jobs/All/"
            "18300151-3b4d-4044-912d-501cebb26321/Panacea-Healthcare-Solutions"
        )
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=base.company_name,
            linkedin_company_url=base.linkedin_company_url,
            website=base.website,
            career=VerifiedCareerEvidence(
                url=career_url,
                website_url=base.website.url,
                source="verified_career_search",
                evidence_url=base.website.url,
                observed_at=1.0,
            ),
        )
        board = JobBoard(
            provider="paylocity",
            identifier=(
                "18300151-3b4d-4044-912d-501cebb26321|"
                "panacea-healthcare-solutions"
            ),
            url=career_url,
        )
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="linked_url_evidence",
            evidence_url=career_url,
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_title="Engineer",
            )
        )
        context.job_list_page_url = career_url
        context.discovered_job_board = discovered
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(discovered,),
            eligible_set_complete=False,
        )
        context.trace["stages"] = {
            "job_board_discovery": {
                "selected": {
                    "url": career_url,
                    "source_kind": "stored_verified_career_provider",
                }
            }
        }

        execution = OpeningMatchStage(
            EmptyInventoryService(),
            max_job_board_attempts=1,
            company_discovery_evidence_store=self._EvidenceStore(record),
        ).run(context)

        self.assertEqual(execution.result.reason_code, "COMPANY_IDENTITY_AMBIGUOUS")

    def test_current_career_route_precedes_unverified_stored_provider_candidate(self):
        record = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name=record.company_name,
            linkedin_company_url=record.linkedin_company_url,
            website=record.website,
            career=record.career,
            provider_boards=(
                VerifiedProviderBoardEvidence(
                    provider="workday",
                    tenant="parent/Brand",
                    canonical_board_url="https://parent.wd1.myworkdayjobs.com/Brand",
                    relationship_evidence_url="https://old.example/careers",
                    verification_method="first_party_handoff",
                    source="first_party_handoff",
                    observed_at=1.0,
                ),
            ),
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_title="Engineer",
            )
        )
        context.career_page_url = "https://acme.example/current-careers"

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
            company_discovery_evidence_store=self._EvidenceStore(record),
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://boards.greenhouse.io/acme",
        )
        self.assertEqual(
            execution.trace["candidate_discovery"]["selected_wave"],
            "website_direct",
        )

    def test_deterministic_hiring_identity_failure_blocks_stored_provider(self):
        store = self._EvidenceStore(self._stored_career_record())
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="failed",
                reason_code="COMPANY_IDENTITY_AMBIGUOUS",
            )
        )

        execution = JobBoardDiscoveryStage(
            FakeDiscoveryService(),
            candidate_discovery=CompositeCandidateDiscovery((), limit=12),
            enable_parallel_candidate_discovery=True,
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(
            execution.trace["scheduler"]["reason"],
            "hiring_identity_unresolved",
        )

    def test_complete_native_inventory_revalidates_stored_same_entity_board(self):
        class EmptyInventoryService:
            def match_discovered_board(self, discovered, *args):
                return None, discovered.board.url, {
                    "provider_api": {
                        "provider": "smartrecruiters",
                        "inventory": {
                            "source": "native_adapter",
                            "status": "verified_filtered_empty",
                            "complete": True,
                        },
                        "adapter_trace": {
                            "tenant_identity_conflict": False,
                            "errors": [],
                        },
                    }
                }

        board = JobBoard(
            provider="smartrecruiters",
            identifier="LinkedIn3",
            url="https://jobs.smartrecruiters.com/LinkedIn3",
        )
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="linked_url_evidence",
            evidence_url=board.url,
        )
        base = self._stored_career_record()
        record = VerifiedCompanyDiscoveryEvidence(
            company_name="linkedin",
            linkedin_company_url="https://www.linkedin.com/company/linkedin",
            website=base.website,
            career=base.career,
            provider_boards=(
                VerifiedProviderBoardEvidence(
                    provider="smartrecruiters",
                    tenant="LinkedIn3",
                    canonical_board_url=board.url,
                    relationship_evidence_url="https://careers.linkedin.com",
                    verification_method="first_party_handoff",
                    source="first_party_handoff",
                    observed_at=1.0,
                ),
            ),
        )
        store = self._EvidenceStore(record)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="LinkedIn",
                linkedin_company_url="https://www.linkedin.com/company/linkedin",
                job_title="Missing Role",
            )
        )
        context.job_list_page_url = board.url
        context.discovered_job_board = discovered
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(discovered,),
            eligible_set_complete=False,
        )
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="LinkedIn",
            provider="smartrecruiters",
            tenant="LinkedIn3",
            canonical_board_url=board.url,
            evidence_url=board.url,
            verification_method="linked_url_only",
            relationship_verified=False,
        )
        context.trace["stages"] = {
            "job_board_discovery": {
                "selected": {"source_kind": "stored_verified_provider_board"}
            }
        }

        execution = OpeningMatchStage(
            EmptyInventoryService(),
            max_job_board_attempts=1,
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")
        self.assertTrue(
            execution.updates["provider_identity"].relationship_verified
        )
        self.assertTrue(
            execution.updates["hiring_identity_evidence"].verified
        )

    def test_stored_inventory_allows_verified_parent_brand_tenant_segment(self):
        self.assertTrue(
            _stored_tenant_matches_hiring_entity("Gucci", "kering/Gucci")
        )
        self.assertFalse(
            _stored_tenant_matches_hiring_entity("Gucci", "kering/SaintLaurent")
        )
        self.assertFalse(
            _stored_tenant_matches_hiring_entity("Gucci", "kering")
        )

    def test_generic_search_query_does_not_change_verified_board_identity(self):
        root = "https://careers.example.com/jobs"
        searched = f"{root}?search=Financial+Analyst"
        opening = f"{root}/northern-america/financial-analyst"

        class SearchResultService(FakeDiscoveryService):
            def match_opening(self, job_list_url, target_title=None, target_location=None):
                return opening, searched, {
                    "selected": {
                        "url": opening,
                        "title": "Financial Analyst",
                        "location": "Wayne, NJ",
                        "provider": "generic",
                        "score": 200,
                        "reasons": ["exact title match"],
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example",
                job_title="Financial Analyst",
                job_location="Wayne, NJ",
            )
        )
        context.hiring_entity_name = "Example"
        context.hiring_identity_evidence = HiringIdentityEvidence(
            source_company_name="Example",
            hiring_entity_name="Example",
            relationship_type="same_entity",
            verification_method="official_company_website",
            verified=True,
            evidence_url="https://www.example.com",
        )
        context.job_list_page_url = root
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Example",
            provider="generic",
            tenant=f"url:{root}",
            canonical_board_url=root,
            evidence_url=root,
            verification_method="identity_career_root",
            relationship_verified=True,
        )

        execution = OpeningMatchStage(SearchResultService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["job_list_page_url"], searched)
        self.assertEqual(
            execution.updates["provider_identity"].canonical_board_url,
            root,
        )
        self.assertEqual(execution.updates["provider_identity"].tenant, f"url:{root}")
        self.assertEqual(execution.updates["opening_identity"].canonical_board_url, root)

    def test_stored_first_party_chain_binds_brand_to_different_provider_tenant(self):
        record = self._stored_career_record()
        stored = VerifiedProviderBoardEvidence(
            provider="ashby",
            tenant="parentco",
            canonical_board_url="https://jobs.ashbyhq.com/parentco",
            relationship_evidence_url=record.career.url,
            verification_method="verified_first_party_handoff",
            source="first_party_handoff",
            observed_at=1.0,
        )

        self.assertEqual(
            _stored_provider_relationship(record, stored, "Acme", "parentco"),
            ("brand_parent", "parentco"),
        )
        unbound = VerifiedProviderBoardEvidence(
            provider=stored.provider,
            tenant=stored.tenant,
            canonical_board_url=stored.canonical_board_url,
            relationship_evidence_url="https://unrelated.example/careers",
            verification_method=stored.verification_method,
            source=stored.source,
            observed_at=stored.observed_at,
        )
        self.assertIsNone(
            _stored_provider_relationship(record, unbound, "Acme", "parentco")
        )

    def test_career_stage_passes_saved_homepage_navigation_evidence(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.evidence = None

            def find_career_page(self, *args, homepage_navigation_evidence=None, **kwargs):
                self.evidence = homepage_navigation_evidence
                return super().find_career_page(*args, **kwargs)

        evidence = HomepageNavigationEvidence(
            homepage_url="https://acme.example",
            candidate_urls=("https://acme.example/careers",),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url="https://acme.example")
        )
        context.homepage_navigation_evidence = evidence
        service = CapturingCareer()

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertIs(service.evidence, evidence)

    def test_career_stage_does_not_search_publisher_after_identity_failure(self):
        class MustNotSearch(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("publisher career site must not be searched")

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Recruiting Publisher",
                company_website_url="https://publisher.example",
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="failed",
                reason_code="COMPANY_IDENTITY_AMBIGUOUS",
            )
        )

        execution = CareerDiscoveryStage(MustNotSearch()).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(
            execution.trace["scheduler"]["reason"],
            "hiring_identity_unresolved",
        )

    def test_s4_s5_s6_can_run_through_versioned_context(self):
        service = FakeDiscoveryService()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                job_title="Data Analyst",
            )
        )
        runner = PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        )

        runner.run(context)

        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.job_list_page_url, "https://boards.greenhouse.io/acme")
        self.assertEqual(context.open_position_url, "https://boards.greenhouse.io/acme/jobs/123")
        self.assertEqual([result.status for result in context.stage_results], ["success", "success", "success"])

    def test_job_board_stage_can_run_independently_from_saved_career_context(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["job_list_page_url"], "https://boards.greenhouse.io/acme")

    def test_job_board_stage_passes_target_location(self):
        class LocationAwareService(FakeDiscoveryService):
            def __init__(self):
                self.target_location = None

            def find_job_board(
                self,
                career_page_url,
                company_name=None,
                target_location=None,
            ):
                self.target_location = target_location
                return super().find_job_board(
                    career_page_url,
                    company_name=company_name,
                    target_location=target_location,
                )

        service = LocationAwareService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_location="Brussels, Belgium")
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(service).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.target_location, "Brussels, Belgium")

    def test_job_board_stage_uses_native_external_apply_without_career_page(self):
        external = (
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/"
            "Data-Analyst_R123"
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", external_apply_url=external)
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.result.provider, "workday")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://company.wd5.myworkdayjobs.com/en-US/acme",
        )
        self.assertEqual(execution.trace["method"], "external_apply_url")

    def test_job_board_stage_rejects_unknown_external_apply_provider(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                external_apply_url="https://apply.untrusted.example/jobs/123",
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "unsupported")
        self.assertEqual(execution.result.reason_code, "PROVIDER_UNSUPPORTED")
        self.assertEqual(execution.updates, {})

    def test_direct_input_career_root_is_trusted_without_network_revalidation(self):
        class MustNotFetchCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("trusted direct root should not be re-fetched")

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://jobs.lever.co/acme",
                source="input",
            )
        )

        execution = CareerDiscoveryStage(MustNotFetchCareer()).run(context)

        self.assertEqual(execution.updates["career_page_url"], "https://jobs.lever.co/acme")
        self.assertEqual(execution.trace["preferred_root_validation"], "trusted_provenance")

    def test_replay_career_root_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://job-boards.greenhouse.io/acme", {"validated": True}

        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://wrong.example/careers",
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, "https://wrong.example/careers")
        self.assertEqual(execution.updates["career_page_url"], "https://job-boards.greenhouse.io/acme")

    def test_replay_career_root_from_fresh_matching_identity_evidence_is_trusted(self):
        class MustNotFetchCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("freshly resolved identity root should not be re-fetched")

        career_root = "https://careers.example-health.test/jobs"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example Health",
                company_website_url="https://example-health.test",
                career_root_url="https://stale.example/careers",
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.career_root_url = career_root
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": career_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "selected": {"career_root_url": career_root}
        }

        execution = CareerDiscoveryStage(MustNotFetchCareer()).run(context)

        self.assertEqual(execution.updates["career_page_url"], career_root)
        self.assertEqual(execution.trace["preferred_root_validation"], "trusted_provenance")

    def test_replay_career_root_passed_through_s3_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://careers.example.test/jobs", {"validated": True}

        stale_root = "https://stale.example/careers"
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example",
                company_website_url="https://example.test",
                career_root_url=stale_root,
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": stale_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "matched_rule": None
        }

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, stale_root)
        self.assertEqual(execution.updates["career_page_url"], "https://careers.example.test/jobs")

    def test_replay_career_root_mismatching_identity_evidence_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://careers.example-health.test/jobs", {"validated": True}

        stale_root = "https://stale.example/careers"
        resolved_root = "https://careers.example-health.test/jobs"
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example Health",
                company_website_url="https://example-health.test",
                career_root_url=stale_root,
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": resolved_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "selected": {"career_root_url": resolved_root}
        }

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, stale_root)
        self.assertEqual(execution.updates["career_page_url"], resolved_root)

    def test_job_board_stage_accepts_provider_from_verified_page_evidence(self):
        class PageAwareService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                return career_page_url, {
                    "provider": "icims",
                    "provider_detection": {"method": "page_evidence"},
                }

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://jobs.acme.example/region/jobs"

        execution = JobBoardDiscoveryStage(PageAwareService()).run(context)

        self.assertEqual(execution.result.provider, "icims")
        self.assertEqual(execution.updates["provider"], "icims")

    def test_page_aware_board_handoff_flows_from_s5_to_s6(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.acme.example/careers",
                provider="phenom",
                identifier="ACME",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.acme.example/careers",
        )

        class PageAwareService(FakeDiscoveryService):
            def __init__(self):
                self.received = None

            def find_job_board_with_evidence(
                self, career_page_url, company_name=None, target_location=None
            ):
                return discovered.board.url, {"provider": "phenom"}, discovered

            def match_discovered_board(
                self, board_evidence, target_title=None, target_location=None
            ):
                self.received = board_evidence
                return (
                    board_evidence.board.url + "/job/123",
                    board_evidence.board.url,
                    {"provider_detection": {"method": "typed_stage_handoff"}},
                )

        service = PageAwareService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = discovered.evidence_url

        PipelineStageRunner(
            [JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        ).run(context)

        self.assertEqual(context.discovered_job_board, discovered)
        self.assertIs(service.received, discovered)
        self.assertEqual(context.open_position_url, discovered.board.url + "/job/123")

    def test_opening_portfolio_continues_after_board_local_empty_to_exact(self):
        early = DiscoveredJobBoard(
            board=JobBoard(
                url="https://early.example.test/search-results",
                provider="phenom",
            ),
            detection_method="url_evidence",
            evidence_url="https://early.example.test/search-results",
        )
        general = DiscoveredJobBoard(
            board=JobBoard(
                url="https://general.example.test/search-results",
                provider="phenom",
            ),
            detection_method="url_evidence",
            evidence_url="https://general.example.test/search-results",
        )

        class PortfolioService(FakeDiscoveryService):
            def __init__(self):
                self.attempted = []

            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                self.attempted.append(discovered.board.url)
                if discovered is early:
                    return None, discovered.board.url, {
                        "provider_api": {
                            "inventory": {
                                "status": "verified_filtered_empty",
                                "scope": "title_filtered",
                                "candidate_count": 0,
                            }
                        }
                    }
                return (
                    discovered.board.url + "/job/123",
                    discovered.board.url,
                    {"provider_api": {"inventory": {"status": "verified"}}},
                )

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Data Scientist")
        )
        context.job_list_page_url = early.board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(early, general),
            eligible_set_complete=True,
        )
        service = PortfolioService()

        execution = OpeningMatchStage(
            service,
            max_job_board_attempts=2,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.result.provider, "phenom")
        self.assertEqual(service.attempted, [early.board.url, general.board.url])
        self.assertEqual(
            execution.updates["open_position_url"],
            general.board.url + "/job/123",
        )
        self.assertEqual(execution.trace["board_portfolio"]["attempted_count"], 2)

    def test_opening_portfolio_does_not_claim_no_match_with_unattempted_board(self):
        boards = tuple(
            DiscoveredJobBoard(
                board=JobBoard(
                    url=f"https://jobs{index}.example.test/search-results",
                    provider="phenom",
                ),
                detection_method="url_evidence",
                evidence_url=f"https://jobs{index}.example.test/search-results",
            )
            for index in range(2)
        )

        class EmptyPortfolioService(FakeDiscoveryService):
            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified_filtered_empty",
                            "scope": "title_filtered",
                            "complete": True,
                            "candidate_count": 0,
                        }
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.job_list_page_url = boards[0].board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=boards,
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(
            EmptyPortfolioService(),
            max_job_board_attempts=1,
        ).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "JOB_BOARD_PORTFOLIO_INCOMPLETE",
        )
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 1)

    def test_opening_portfolio_claims_no_match_only_after_complete_attempt_set(self):
        boards = tuple(
            DiscoveredJobBoard(
                board=JobBoard(
                    url=f"https://jobs{index}.example.test/search-results",
                    provider="phenom",
                ),
                detection_method="url_evidence",
                evidence_url=f"https://jobs{index}.example.test/search-results",
            )
            for index in range(2)
        )

        class EmptyPortfolioService(FakeDiscoveryService):
            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified_filtered_empty",
                            "scope": "title_filtered",
                            "complete": True,
                            "candidate_count": 0,
                        }
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.job_list_page_url = boards[0].board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=boards,
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(
            EmptyPortfolioService(),
            max_job_board_attempts=2,
        ).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 0)

    def test_opening_no_match_detail_reports_verified_location_conflict(self):
        board = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.example.test/search-results",
                provider="generic",
            ),
            detection_method="verified_first_party_action",
            evidence_url="https://jobs.example.test/search-results",
        )

        class LocationConflictService(FakeDiscoveryService):
            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified",
                            "scope": "filtered",
                            "complete": True,
                            "candidate_count": 64,
                            "strongest_title_score": 150,
                        }
                    },
                    "location_unverified_candidate_rejected": {
                        "url": "https://jobs.example.test/jobs/42",
                        "candidate_location": "United States",
                        "target_location": "Greater Tampa Bay Area",
                    },
                }

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="Junior Data Analyst",
                job_location="Greater Tampa Bay Area",
            )
        )
        context.job_list_page_url = board.board.url
        context.discovered_job_board = board
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(board,),
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(LocationConflictService()).run(context)

        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")
        self.assertIn("none matched the target location", execution.result.detail)

    def test_opening_no_match_is_partial_not_failed(self):
        class NoMatchService(FakeDiscoveryService):
            def match_opening(self, job_list_url, target_title=None, target_location=None):
                return None, job_list_url, {"opening_error": "specific_opening_not_found"}

        context = PipelineContext.from_company(CompanyInput(company_name="Acme", job_title="Engineer"))
        context.job_list_page_url = "https://boards.greenhouse.io/acme"
        context.provider = "greenhouse"

        execution = OpeningMatchStage(NoMatchService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "OPENING_DISCOVERY_INCOMPLETE",
        )

    def test_career_failure_makes_downstream_stages_not_run(self):
        class MissingCareerService(FakeDiscoveryService):
            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                raise DiscoveryError("career_page_not_found", "missing", trace={"searched": True})

        service = MissingCareerService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Missing", company_website_url="https://missing.example", job_title="Engineer")
        )

        PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        ).run(context)

        self.assertEqual([result.status for result in context.stage_results], ["failed", "not_run", "not_run"])
        self.assertEqual(context.stage_results[0].reason_code, "CAREER_PAGE_NOT_FOUND")

    def test_deterministic_career_miss_reports_linkedin_native_only(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.stage_results.append(
            StageResult(
                stage="career_discovery",
                status="failed",
                reason_code="CAREER_PAGE_NOT_FOUND",
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(execution.updates, {})
        self.assertEqual(execution.result.evidence[0]["source_posting_url"], job_url)

    def test_retryable_career_failure_is_not_hidden_by_native_source(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.stage_results.append(
            StageResult(
                stage="career_discovery",
                status="failed",
                reason_code="NETWORK_TIMEOUT",
                retryable=True,
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "not_run")

    def test_deterministic_job_board_miss_reports_linkedin_native_only(self):
        class MissingBoardService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError("job_board_not_found", "missing", trace={"searched": True})

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(MissingBoardService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "LINKEDIN_NATIVE_ONLY")

    def test_verified_board_wins_over_native_source_evidence(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertIn("job_list_page_url", execution.updates)

    def test_incomplete_job_board_trace_is_not_hidden_by_native_source(self):
        class IncompleteBoardService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError(
                    "job_board_not_found",
                    "incomplete",
                    trace={"candidate_fetch_errors": [{"error": "request timed out"}]},
                )

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(IncompleteBoardService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "JOB_BOARD_NOT_FOUND")

    def test_career_budget_exhaustion_remains_retryable(self):
        class BudgetService(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "FETCH_BUDGET_EXHAUSTED",
                    "candidate budget exhausted",
                    trace={"candidate_fetch_budget_exhausted": {"limit": 5}},
                )

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
            )
        )

        execution = CareerDiscoveryStage(BudgetService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(execution.result.retryable)

    def test_explicit_empty_official_career_page_is_not_rewritten_as_native_only(self):
        class EmptyService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError(
                    "NO_PUBLIC_OPENINGS",
                    "official empty state",
                    trace={"explicit_empty_inventory": {"phrase": "no open positions"}},
                )

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(EmptyService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "NO_PUBLIC_OPENINGS")


if __name__ == "__main__":
    unittest.main()
