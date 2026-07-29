import unittest

from job_source_agent.contracts import OpeningMatchOutcome, PipelineContext
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningSelectionEvidence,
    ProviderIdentity,
    ProviderOpeningRouteEvidence,
)
from job_source_agent.models import CompanyInput
from job_source_agent.job_board import (
    DiscoveredJobBoard,
    JobBoard,
    JobBoardPortfolio,
)
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages import ResultValidationStage
from job_source_agent.stages.discovery import OpeningMatchStage, _opening_identity


SOURCE_BOARD = "https://aggregate.icims.com/jobs/search"
TARGET_BOARD = "https://child.icims.com/jobs/search"
OPENING = "https://child.icims.com/jobs/123/engineer/job"


def _route(**changes):
    values = {
        "provider": "icims",
        "source_tenant": "aggregate.icims.com",
        "source_canonical_board_url": SOURCE_BOARD,
        "target_tenant": "child.icims.com",
        "target_canonical_board_url": TARGET_BOARD,
        "canonical_opening_url": OPENING,
        "opening_id": "123",
        "source_response_url": (
            "https://aggregate.icims.com/jobs/search?hub=15&ss=1"
        ),
        "source_customer_identity": "acme.icims.com",
        "target_customer_identity": "acme.icims.com",
        "route_identity": "hub:15",
        "detail_evidence_url": OPENING,
        "extraction_method": "icims_aggregate_job_card",
        "detail_verified": True,
    }
    values.update(changes)
    return ProviderOpeningRouteEvidence(**values)


class ProviderOpeningRoutePipelineTests(unittest.TestCase):
    def setUp(self):
        self.context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                job_title="Engineer",
                job_location="New York, NY",
            )
        )
        self.hiring = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://acme.example/careers",
        )
        self.provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="icims",
            tenant="aggregate.icims.com",
            canonical_board_url=SOURCE_BOARD,
            evidence_url="https://acme.example/careers",
            verification_method="verified_first_party_handoff",
            relationship_verified=True,
        )

    def test_typed_verified_route_preserves_child_identity_through_s7(self):
        opening = _opening_identity(
            self.context,
            OPENING,
            DEFAULT_PROVIDER_REGISTRY,
            provider_identity=self.provider,
            route_evidence=_route(),
        )

        self.assertIsNotNone(opening)
        self.assertEqual(opening.tenant, "child.icims.com")
        self.assertEqual(opening.canonical_board_url, TARGET_BOARD)
        self.assertEqual(opening.route_evidence, _route())

        self.context.hiring_identity_evidence = self.hiring
        self.context.provider_identity = self.provider
        self.context.opening_identity = opening
        self.context.open_position_url = OPENING
        self.context.opening_selection_evidence = OpeningSelectionEvidence(
            provider="icims",
            tenant="child.icims.com",
            canonical_board_url=TARGET_BOARD,
            canonical_opening_url=OPENING,
            title="Engineer",
            location="New York, NY",
            inventory_scope="title_filtered",
            inventory_complete=True,
            candidate_count=1,
        )

        execution = ResultValidationStage().run(self.context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.trace["issues"], [])

    def test_trace_dictionary_cannot_authorize_child_route(self):
        trace = {
            "selected": {
                "url": OPENING,
                "route_evidence": _route().to_trace_payload(),
            }
        }

        opening = _opening_identity(
            self.context,
            OPENING,
            DEFAULT_PROVIDER_REGISTRY,
            trace,
            provider_identity=self.provider,
        )

        self.assertIsNone(opening)

    def test_typed_route_rejects_wrong_child_board(self):
        route = _route(
            target_tenant="other-child.icims.com",
            target_canonical_board_url=(
                "https://other-child.icims.com/jobs/search"
            ),
        )

        opening = _opening_identity(
            self.context,
            OPENING,
            DEFAULT_PROVIDER_REGISTRY,
            provider_identity=self.provider,
            route_evidence=route,
        )

        self.assertIsNone(opening)

    def test_typed_route_supersedes_same_opening_from_generic_shell(self):
        generic = DiscoveredJobBoard(
            board=JobBoard(
                "https://aggregate.icims.com",
                "generic",
                "url:https://aggregate.icims.com",
                replay_safe=False,
            ),
            detection_method="verified_first_party_action",
            evidence_url="https://aggregate.icims.com",
            relationship_evidence_url="https://acme.example/careers",
        )
        native = DiscoveredJobBoard(
            board=JobBoard(
                SOURCE_BOARD,
                "icims",
                "aggregate.icims.com",
            ),
            detection_method="linked_url_evidence",
            evidence_url=SOURCE_BOARD,
            relationship_evidence_url="https://acme.example/careers",
        )
        self.context.company_website_url = "https://acme.example"
        self.context.career_page_url = "https://acme.example/careers"
        self.context.hiring_identity_evidence = self.hiring
        self.context.hiring_entity_name = "Acme"
        self.context.provider_identity = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="generic",
            tenant="url:https://aggregate.icims.com",
            canonical_board_url="https://aggregate.icims.com",
            evidence_url="https://acme.example/careers",
            verification_method="verified_first_party_handoff",
            relationship_verified=True,
        )
        self.context.job_board_portfolio = JobBoardPortfolio(
            boards=(generic, native),
            eligible_set_complete=True,
        )
        self.context.discovered_job_board = generic
        self.context.job_list_page_url = generic.board.url

        class Service:
            def match_discovered_board_with_evidence(
                self,
                discovered,
                target_title=None,
                target_location=None,
            ):
                return OpeningMatchOutcome(
                    opening_url=OPENING,
                    job_list_url=discovered.board.url,
                    trace={
                        "selected": {
                            "url": OPENING,
                            "title": "Engineer",
                            "location": "New York, NY",
                        },
                        "provider_api": {
                            "inventory": {
                                "source": "native_adapter",
                                "status": "verified",
                                "scope": "title_filtered",
                                "complete": True,
                                "candidate_count": 1,
                            }
                        },
                    },
                    route_evidence=_route(),
                )

        execution = OpeningMatchStage(
            Service(),
            DEFAULT_PROVIDER_REGISTRY,
            max_job_board_attempts=2,
        ).run(self.context)

        self.assertEqual(execution.result.status, "success", execution.trace)
        self.assertEqual(execution.updates["open_position_url"], OPENING)
        self.assertEqual(execution.updates["provider"], "icims")
        attempts = execution.trace["board_portfolio"]["attempts"]
        self.assertEqual(attempts[0]["status"], "verified_exact")
        self.assertEqual(attempts[1]["status"], "verified_exact")


if __name__ == "__main__":
    unittest.main()
