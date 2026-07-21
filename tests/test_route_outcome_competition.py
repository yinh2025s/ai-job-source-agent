import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import HiringRelationshipEvidence
from job_source_agent.job_board import (
    DiscoveredJobBoard,
    JobBoard,
    JobBoardPortfolio,
    JobBoardRouteEvidence,
)
from job_source_agent.models import CompanyInput
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.stages.discovery import OpeningMatchStage


PINPOINT_BOARD = "https://oneapp.pinpointhq.com/"
PINPOINT_OPENING = (
    "https://oneapp.pinpointhq.com/en/postings/"
    "674c9316-435e-4642-8dca-8960ed3c2075"
)
ASHBY_BOARD = "https://jobs.ashbyhq.com/oneapp"
ASHBY_OPENING = (
    "https://jobs.ashbyhq.com/oneapp/"
    "11111111-1111-4111-8111-111111111111"
)
ADP_BOARD = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=6d761223-04f6-4d39-a498-276f6ca9389f&"
    "ccId=19000101_000001&type=MP&lang=en_US&"
    "selectedMenuKey=CurrentOpenings"
)
ADP_OPENING = ADP_BOARD.replace(
    "selectedMenuKey=CurrentOpenings",
    "jobId=9201055969029_1",
)


def _typed_board(url):
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_for(url)
    board = adapter.identify_board(url) if adapter is not None else None
    if board is None:
        raise AssertionError(f"test URL did not identify a provider board: {url}")
    canonicalize_board = getattr(adapter, "canonicalize_board", None)
    if callable(canonicalize_board):
        board = canonicalize_board(board)
    return DiscoveredJobBoard(
        board=board,
        detection_method="linked_url_evidence",
        evidence_url=board.url,
    )


def _generic_board(url):
    return DiscoveredJobBoard(
        board=JobBoard(
            url=url,
            provider="generic",
            identifier=f"url:{url}",
            replay_safe=False,
        ),
        detection_method="verified_first_party_action",
        evidence_url=url,
        relationship_evidence_url=url,
    )


def _route(discovered, *, authorized, source_kind="first_party_ats_link"):
    board = discovered.board
    relationship = None
    if authorized:
        relationship = HiringRelationshipEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            provider=board.provider,
            tenant=board.identifier,
            evidence_type=(
                "first_party_inventory"
                if board.provider == "generic"
                else "first_party_handoff"
            ),
            evidence_url="https://acme.example/careers",
            strength=95,
            verified=True,
        )
    return JobBoardRouteEvidence(
        provider=board.provider,
        canonical_board_url=board.url,
        route_kind=(
            "provider_search"
            if source_kind == "targeted_board_search"
            else "website_career"
        ),
        source_kind=source_kind,
        hiring_relationship=relationship,
    )


def _portfolio(*entries, complete=True):
    return JobBoardPortfolio(
        boards=tuple(discovered for discovered, _route_evidence in entries),
        eligible_set_complete=complete,
        route_evidence=tuple(route for _discovered, route in entries),
    )


def _context(portfolio, *, title="Product Designer", location="Portland, OR"):
    context = PipelineContext.from_company(
        CompanyInput(
            company_name="Acme",
            job_title=title,
            job_location=location,
        )
    )
    context.company_website_url = "https://acme.example"
    context.career_page_url = "https://acme.example/careers"
    context.job_board_portfolio = portfolio
    context.discovered_job_board = portfolio.primary
    context.job_list_page_url = portfolio.primary.board.url
    return context


def _exact_trace(url, title, location):
    return {
        "selected": {"url": url, "title": title, "location": location},
        "provider_api": {
            "inventory": {
                "status": "verified",
                "scope": "full",
                "complete": True,
                "candidate_count": 1,
            }
        },
    }


def _no_match_trace():
    return {
        "provider_api": {
            "inventory": {
                "status": "verified_filtered_empty",
                "scope": "title_filtered",
                "complete": True,
                "candidate_count": 0,
            }
        }
    }


def _incomplete_trace():
    return {
        "provider_api": {
            "inventory": {
                "status": "incomplete",
                "scope": "unknown",
                "complete": False,
                "candidate_count": 0,
            }
        }
    }


class _RouteOutcomeService:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.attempted = []

    def match_discovered_board(
        self,
        discovered,
        target_title=None,
        target_location=None,
    ):
        self.attempted.append(discovered.board.url)
        outcome = self.outcomes[discovered.board.provider]
        if outcome == "exact":
            opening = {
                "pinpoint": PINPOINT_OPENING,
                "ashby": ASHBY_OPENING,
                "adp": ADP_OPENING,
            }[discovered.board.provider]
            return (
                opening,
                discovered.board.url,
                _exact_trace(opening, target_title, target_location),
            )
        if outcome == "wrong_location":
            opening = {
                "pinpoint": PINPOINT_OPENING,
                "ashby": ASHBY_OPENING,
            }[discovered.board.provider]
            return (
                opening,
                discovered.board.url,
                _exact_trace(opening, target_title, "Austin, TX"),
            )
        if outcome == "no_match":
            return None, discovered.board.url, _no_match_trace()
        if outcome == "incomplete":
            return None, discovered.board.url, _incomplete_trace()
        raise AssertionError(f"unsupported test outcome: {outcome}")


class RouteOutcomeCompetitionTests(unittest.TestCase):
    def setUp(self):
        self.pinpoint = _typed_board(PINPOINT_BOARD)
        self.ashby = _typed_board(ASHBY_BOARD)
        self.adp = _typed_board(ADP_BOARD)

    def test_authorized_pinpoint_exact_wins_over_unverified_ashby_no_match(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (
                self.ashby,
                _route(
                    self.ashby,
                    authorized=False,
                    source_kind="targeted_board_search",
                ),
            ),
        )
        service = _RouteOutcomeService({"pinpoint": "exact", "ashby": "no_match"})

        execution = OpeningMatchStage(
            service,
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["open_position_url"], PINPOINT_OPENING)
        self.assertEqual(service.attempted, [PINPOINT_BOARD, ASHBY_BOARD])

    def test_authorized_incomplete_is_not_overridden_by_unverified_no_match(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (
                self.ashby,
                _route(
                    self.ashby,
                    authorized=False,
                    source_kind="targeted_board_search",
                ),
            ),
        )
        execution = OpeningMatchStage(
            _RouteOutcomeService(
                {"pinpoint": "incomplete", "ashby": "no_match"}
            ),
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "OPENING_DISCOVERY_INCOMPLETE",
        )

    def test_first_party_generic_incomplete_does_not_hide_adp_exact(self):
        generic = _generic_board("https://kitocrosby.com/careers")
        portfolio = _portfolio(
            (generic, _route(generic, authorized=True, source_kind="first_party_inventory")),
            (self.adp, _route(self.adp, authorized=True)),
        )
        service = _RouteOutcomeService({"generic": "incomplete", "adp": "exact"})

        execution = OpeningMatchStage(
            service,
            max_job_board_attempts=2,
        ).run(
            _context(
                portfolio,
                title="Human Resources Manager",
                location="Longview, TX",
            )
        )

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["open_position_url"], ADP_OPENING)

    def test_route_local_location_rejection_continues_to_later_exact(self):
        portfolio = _portfolio(
            (self.ashby, _route(self.ashby, authorized=True)),
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
        )
        service = _RouteOutcomeService(
            {"ashby": "wrong_location", "pinpoint": "exact"}
        )

        execution = OpeningMatchStage(
            service,
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["open_position_url"], PINPOINT_OPENING)
        attempts = execution.trace["board_portfolio"]["attempts"]
        self.assertEqual(attempts[0]["status"], "identity_rejected")
        self.assertEqual(attempts[1]["status"], "verified_exact")

    def test_incomplete_authorized_route_precedes_verified_no_match(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (self.ashby, _route(self.ashby, authorized=True)),
        )
        execution = OpeningMatchStage(
            _RouteOutcomeService(
                {"pinpoint": "incomplete", "ashby": "no_match"}
            ),
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(
            execution.result.reason_code,
            "OPENING_DISCOVERY_INCOMPLETE",
        )

    def test_all_authorized_complete_no_match_can_claim_opening_not_found(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (self.ashby, _route(self.ashby, authorized=True)),
        )
        execution = OpeningMatchStage(
            _RouteOutcomeService({"pinpoint": "no_match", "ashby": "no_match"}),
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")

    def test_different_verified_exacts_fail_closed(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (self.ashby, _route(self.ashby, authorized=True)),
        )
        execution = OpeningMatchStage(
            _RouteOutcomeService({"pinpoint": "exact", "ashby": "exact"}),
            max_job_board_attempts=2,
        ).run(_context(portfolio))

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "OPENING_IDENTITY_AMBIGUOUS",
        )
        self.assertNotIn("open_position_url", execution.updates)

    def test_route_order_does_not_change_the_only_verified_exact(self):
        entries = (
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (self.ashby, _route(self.ashby, authorized=True)),
        )
        results = []
        for ordered in (entries, tuple(reversed(entries))):
            execution = OpeningMatchStage(
                _RouteOutcomeService(
                    {"pinpoint": "exact", "ashby": "no_match"}
                ),
                max_job_board_attempts=2,
            ).run(_context(_portfolio(*ordered)))
            results.append(execution.updates.get("open_position_url"))

        self.assertEqual(results, [PINPOINT_OPENING, PINPOINT_OPENING])

    def test_route_provenance_survives_checkpoint_round_trip(self):
        portfolio = _portfolio(
            (self.pinpoint, _route(self.pinpoint, authorized=True)),
            (
                self.adp,
                _route(
                    self.adp,
                    authorized=False,
                    source_kind="targeted_board_search",
                ),
            ),
        )

        payload = portfolio.to_checkpoint_payload()
        self.assertIsNotNone(payload)
        restored = JobBoardPortfolio.from_checkpoint_payload(payload)

        self.assertEqual(restored, portfolio)
        self.assertTrue(restored.routes_for(restored.boards[0])[0].authorized)
        self.assertFalse(restored.routes_for(restored.boards[1])[0].authorized)


if __name__ == "__main__":
    unittest.main()
