import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import HiringIdentityEvidence, ProviderIdentity
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import CompanyInput
from job_source_agent.providers import ProviderRegistry
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages.discovery import (
    CareerDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    _tenant_matches_hiring_entity,
)


class _Adapter:
    name = "example"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://jobs.example/")

    def identify_board(self, url):
        tenant = url.split("/")[3]
        return JobBoard(f"https://jobs.example/{tenant}", self.name, tenant)


class _SameTenantDifferentBoardAdapter(_Adapter):
    def identify_board(self, url):
        board = super().identify_board(url)
        if "/alternate/" in url:
            return JobBoard("https://jobs.example/alternate", self.name, "acme")
        return board


class _Service:
    def __init__(self, board, opening=None, trace=None):
        self.board = board
        self.opening = opening
        self.trace = trace or {}
        self.company_name = None

    def find_career_page(self, website, company_name=None, **kwargs):
        self.company_name = company_name
        return "https://careers.example/jobs", {}

    def find_job_board_with_evidence(self, career_url, company_name=None, **kwargs):
        self.company_name = company_name
        return self.board.board.url, {}, self.board

    def match_discovered_board(self, board, *args):
        return self.opening, board.board.url, self.trace


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

    def test_s5_accepts_provider_board_that_is_the_verified_career_page(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://jobs.example/acme",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Source Brand"))
        context.career_page_url = "https://jobs.example/acme/jobs"
        context.hiring_identity_evidence = _hiring("Source Brand")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(
            identity.verification_method,
            "verified_provider_career_page",
        )

    def test_s5_does_not_authorize_provider_board_from_a_different_career_url(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://jobs.example/acme",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Source Brand"))
        context.career_page_url = "https://acme.example/careers"
        context.hiring_identity_evidence = _hiring("Source Brand")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_s5_accepts_generic_board_from_explicit_first_party_handoff(self):
        career = "https://acme.example/careers"
        board_url = "https://opaque-hiring.example/jobs"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "generic"),
            "verified_first_party_action",
            board_url,
            relationship_evidence_url=career,
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = career
        context.hiring_identity_evidence = _hiring("Acme")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(
            identity.verification_method,
            "verified_first_party_handoff",
        )

    def test_s5_rejects_generic_handoff_from_a_different_career_page(self):
        board_url = "https://opaque-hiring.example/jobs"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "generic"),
            "verified_first_party_action",
            board_url,
            relationship_evidence_url="https://other.example/careers",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/careers"
        context.hiring_identity_evidence = _hiring("Acme")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_s5_accepts_provider_handoff_through_same_site_career_page(self):
        career = "https://www.acme.example/careers"
        board_url = "https://jobs.example/opaque-tenant"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "example", "opaque-tenant"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url=(
                "https://www.acme.example/careers/acme-jobs"
            ),
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = career
        context.hiring_identity_evidence = _hiring("Acme")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "verified_first_party_handoff")

    def test_s5_accepts_provider_handoff_from_officially_linked_career_microsite(self):
        website = "https://www.acme.example"
        career = "https://careers.acme-jobs.example/"
        board_url = "https://jobs.example/opaque-tenant"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "example", "opaque-tenant"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url="https://jobs.example/opaque-tenant/root",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url=website)
        )
        context.career_page_url = career
        context.hiring_identity_evidence = _hiring("Acme")
        context.trace["stages"] = {
            "career_discovery": {
                "selected": {
                    "url": career,
                    "source_url": website,
                    "origin": "page_link",
                }
            }
        }

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertTrue(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "verified_first_party_handoff")

    def test_s5_rejects_provider_handoff_from_guessed_career_microsite(self):
        website = "https://www.acme.example"
        career = "https://careers.acme-jobs.example/"
        board_url = "https://jobs.example/opaque-tenant"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "example", "opaque-tenant"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url="https://jobs.example/opaque-tenant/root",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url=website)
        )
        context.career_page_url = career
        context.hiring_identity_evidence = _hiring("Acme")
        context.trace["stages"] = {
            "career_discovery": {
                "selected": {
                    "url": career,
                    "source_url": website,
                    "origin": "path_probe",
                }
            }
        }

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_s5_does_not_authorize_generic_link_evidence_as_action_attestation(self):
        career = "https://acme.example/careers"
        board_url = "https://opaque-hiring.example/jobs"
        board = DiscoveredJobBoard(
            JobBoard(board_url, "generic"),
            "linked_url_evidence",
            board_url,
            relationship_evidence_url=career,
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = career
        context.hiring_identity_evidence = _hiring("Acme")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_s5_does_not_authorize_provider_career_page_without_hiring_identity(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://jobs.example/acme",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://jobs.example/acme/jobs"

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        identity = execution.updates["provider_identity"]
        self.assertFalse(identity.relationship_verified)
        self.assertEqual(identity.verification_method, "linked_url_only")

    def test_s5_does_not_authorize_a_tenant_by_substring(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/notacmeportfolio", "example", "notacmeportfolio"),
            "linked_url_evidence",
            "https://jobs.example/notacmeportfolio",
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/careers"
        context.hiring_identity_evidence = _hiring("Acme")

        execution = JobBoardDiscoveryStage(_Service(board), self.registry).run(context)

        self.assertFalse(execution.updates["provider_identity"].relationship_verified)

    def test_s5_accepts_repeated_workday_tenant_and_site_identity(self):
        self.assertTrue(_tenant_matches_hiring_entity("Acme", "acme/acme"))

    def test_s5_rejects_mixed_tenant_segments(self):
        self.assertFalse(_tenant_matches_hiring_entity("Acme", "other/acme"))

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
        self.assertTrue(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "tenant_name_match",
        )

        wrong = OpeningMatchStage(
            _Service(board, "https://jobs.example/notion/jobs/123"), self.registry
        ).run(context)
        self.assertNotIn("opening_identity", wrong.updates)

    def test_s6_does_not_inherit_verified_relationship_for_a_different_board(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://careers.example/jobs",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.discovered_job_board = board
        context.job_list_page_url = board.board.url
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="example",
            tenant="acme",
            canonical_board_url="https://jobs.example/alternate",
            evidence_url="https://careers.example/jobs",
            verification_method="first_party_handoff",
            relationship_verified=True,
        )

        execution = OpeningMatchStage(
            _Service(board, "https://jobs.example/acme/jobs/123"), self.registry
        ).run(context)

        self.assertFalse(execution.updates["provider_identity"].relationship_verified)
        self.assertEqual(
            execution.updates["provider_identity"].verification_method,
            "linked_url_only",
        )

    def test_s6_rejects_same_provider_and_tenant_on_different_canonical_board(self):
        board = DiscoveredJobBoard(
            JobBoard("https://jobs.example/acme", "example", "acme"),
            "url_evidence",
            "https://careers.example/jobs",
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.discovered_job_board = board
        context.job_list_page_url = board.board.url
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="example",
            tenant="acme",
            canonical_board_url="https://jobs.example/acme",
            evidence_url="https://careers.example/jobs",
            verification_method="tenant_name_match",
            relationship_verified=True,
        )
        registry = ProviderRegistry((_SameTenantDifferentBoardAdapter(),))

        execution = OpeningMatchStage(
            _Service(board, "https://jobs.example/alternate/jobs/123"), registry
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["open_position_url"],
            "https://jobs.example/alternate/jobs/123",
        )
        self.assertNotIn("opening_identity", execution.updates)

    def test_s6_accepts_native_detail_urls_below_canonical_board(self):
        cases = (
            (
                "greenhouse",
                "taxbit",
                "https://job-boards.greenhouse.io/taxbit",
                "https://job-boards.greenhouse.io/taxbit/jobs/6111141004",
            ),
            (
                "lever",
                "kobie",
                "https://jobs.lever.co/kobie",
                "https://jobs.lever.co/kobie/d14582bd-64a2-439e-a7e3-a50ce7270a3d",
            ),
        )
        for provider, tenant, board_url, opening_url in cases:
            with self.subTest(provider=provider):
                board = DiscoveredJobBoard(
                    JobBoard(board_url, provider, tenant),
                    "url_evidence",
                    board_url,
                )
                context = PipelineContext.from_company(
                    CompanyInput(company_name=tenant, job_title="Engineer")
                )
                context.discovered_job_board = board
                context.job_list_page_url = board_url
                context.provider_identity = ProviderIdentity(
                    hiring_entity_name=tenant,
                    provider=provider,
                    tenant=tenant,
                    canonical_board_url=board_url,
                    evidence_url=board_url,
                    verification_method="tenant_name_match",
                    relationship_verified=True,
                )

                execution = OpeningMatchStage(
                    _Service(
                        board,
                        opening_url,
                        {
                            "selected": {"url": opening_url},
                            "provider_api": {
                                "provider": provider,
                                "provider_detection": {"url": board_url},
                            }
                        },
                    ),
                    DEFAULT_PROVIDER_REGISTRY,
                ).run(context)

                self.assertIn("opening_identity", execution.updates)

    def test_s6_accepts_adapter_verified_cross_host_tenant_alias(self):
        board_url = "https://app.whitecarrot.io/careers/smart-bricks"
        opening_url = (
            "https://smart-bricks.whitecarrot.ai/jobs/"
            "a34cdab0-b26f-4337-a7ed-8788c64760b0"
        )
        board = DiscoveredJobBoard(
            JobBoard(board_url, "whitecarrot", "smart-bricks"),
            "url_evidence",
            board_url,
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Smart Bricks", job_title="AI Engineer")
        )
        context.discovered_job_board = board
        context.job_list_page_url = board_url
        context.provider_identity = ProviderIdentity(
            hiring_entity_name="Smart Bricks",
            provider="whitecarrot",
            tenant="smart-bricks",
            canonical_board_url=board_url,
            evidence_url=board_url,
            verification_method="tenant_name_match",
            relationship_verified=True,
        )

        execution = OpeningMatchStage(
            _Service(
                board,
                opening_url,
                {
                    "selected": {"url": opening_url},
                    "provider_api": {
                        "provider": "whitecarrot",
                        "provider_detection": {"url": board_url},
                    }
                },
            ),
            DEFAULT_PROVIDER_REGISTRY,
        ).run(context)

        self.assertIn("opening_identity", execution.updates)


if __name__ == "__main__":
    unittest.main()
