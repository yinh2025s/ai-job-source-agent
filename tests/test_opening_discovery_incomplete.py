import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.evaluation import summarize_results
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.models import CompanyInput
from job_source_agent.opening_availability import diagnose_opening_availability
from job_source_agent.reasons import reason_spec
from job_source_agent.stages import OpeningMatchStage


class OpeningDiscoveryIncompleteTests(unittest.TestCase):
    def test_generic_no_candidates_is_canonical_incomplete(self):
        diagnostic = diagnose_opening_availability({"candidates": []})

        self.assertEqual(diagnostic.disposition, "discovery_incomplete")
        self.assertEqual(diagnostic.reason_code, "OPENING_DISCOVERY_INCOMPLETE")
        self.assertFalse(reason_spec(diagnostic.reason_code).retryable)
        self.assertEqual(reason_spec(diagnostic.reason_code).owner, "matcher")

    def test_verified_nonempty_inventory_is_complete_no_match(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "status": "verified",
                        "complete": True,
                        "candidate_count": 7,
                        "strongest_title_score": 20,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_no_match")
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")

    def test_verified_empty_inventory_is_no_public_openings(self):
        diagnostic = diagnose_opening_availability(
            {
                "provider_api": {
                    "inventory": {
                        "status": "verified_empty",
                        "complete": True,
                        "candidate_count": 0,
                    }
                }
            }
        )

        self.assertEqual(diagnostic.disposition, "verified_inventory_empty")
        self.assertEqual(diagnostic.reason_code, "NO_PUBLIC_OPENINGS")

    def test_provider_timeout_keeps_retryable_reason(self):
        diagnostic = diagnose_opening_availability(
            {"provider_api": {"errors": [{"error": "request timed out"}]}}
        )

        self.assertEqual(diagnostic.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(reason_spec(diagnostic.reason_code).retryable)

    def test_mixed_complete_and_incomplete_boards_stays_incomplete(self):
        boards = tuple(
            DiscoveredJobBoard(
                board=JobBoard(
                    url=f"https://jobs{index}.example.test/search",
                    provider="generic",
                ),
                detection_method="url_evidence",
                evidence_url=f"https://jobs{index}.example.test/search",
            )
            for index in range(2)
        )

        class MixedInventoryService:
            def match_discovered_board(
                self, discovered, target_title=None, target_location=None
            ):
                if discovered is boards[0]:
                    trace = {
                        "provider_api": {
                            "inventory": {
                                "status": "verified",
                                "complete": True,
                                "candidate_count": 4,
                            }
                        }
                    }
                else:
                    trace = {"candidates": []}
                return None, discovered.board.url, trace

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.job_list_page_url = boards[0].board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=boards,
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(
            MixedInventoryService(), max_job_board_attempts=2
        ).run(context)

        self.assertEqual(execution.result.reason_code, "OPENING_DISCOVERY_INCOMPLETE")
        self.assertEqual(execution.trace["board_portfolio"]["attempted_count"], 2)
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 0)

    def test_evaluation_clusters_incomplete_as_discovery_unresolved(self):
        summary = summarize_results(
            [
                {
                    "stages": [
                        {
                            "stage": "opening_match",
                            "status": "partial",
                            "reason_code": "OPENING_DISCOVERY_INCOMPLETE",
                            "retryable": False,
                        }
                    ]
                }
            ]
        )

        self.assertEqual(
            summary["terminal_outcome_counts"], {"discovery_unresolved": 1}
        )


if __name__ == "__main__":
    unittest.main()
