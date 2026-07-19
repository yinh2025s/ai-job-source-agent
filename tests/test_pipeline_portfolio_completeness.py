import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import CompanyInput
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.registry import ProviderRegistry
from job_source_agent.stages import OpeningMatchStage


class NullFetcher:
    def fetch(self, url, data=None, headers=None):
        raise AssertionError(f"unexpected fetch: {url}")


class AlternativeAdapter:
    name = "alternative"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://nested.example/")

    def identify_board(self, url):
        if not self.recognizes(url):
            return None
        return JobBoard(url, self.name, url.rstrip("/").rsplit("/", 1)[-1])


class PipelinePortfolioCompletenessTests(unittest.TestCase):
    career = "https://careers.example/jobs"
    portal = "https://jobs.example/search"
    nested = "https://nested.example/jobs"

    def _agent_with_trace(self, trace):
        agent = JobSourceAgent(
            NullFetcher(),
            provider_registry=ProviderRegistry((AlternativeAdapter(),)),
        )
        primary = DiscoveredJobBoard(
            board=JobBoard(self.nested, "alternative", "jobs"),
            detection_method="linked_url_evidence",
            evidence_url=self.nested,
            relationship_evidence_url=self.portal,
        )
        agent.find_job_board_with_evidence = lambda *args, **kwargs: (
            self.nested,
            trace,
            primary,
        )
        return agent

    def _action_trace(self, status):
        return {
            "pages_visited": [{"url": self.career}, {"url": self.portal}],
            "candidates": [],
            "career_actions": [
                {
                    "target_url": self.portal,
                    "source_url": self.career,
                    "kind": "search_jobs",
                    "confidence": "high",
                    "status": status,
                }
            ],
        }

    def test_exhausted_first_party_actions_allow_complete_empty_portfolio(self):
        agent = self._agent_with_trace(self._action_trace("visited"))
        _selected, _trace, portfolio = agent.find_job_board_portfolio(
            self.career,
            company_name="Example",
            target_title="Designer",
        )

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(len(portfolio.boards), 2)
        self.assertTrue(portfolio.eligible_set_complete)

        class EmptyInventoryService:
            def match_discovered_board(
                self, discovered, target_title=None, target_location=None
            ):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified_empty",
                            "complete": True,
                            "candidate_count": 0,
                        }
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(company_name="Example", job_title="Designer")
        )
        context.job_list_page_url = portfolio.primary.board.url
        context.job_board_portfolio = portfolio
        execution = OpeningMatchStage(
            EmptyInventoryService(), max_job_board_attempts=2
        ).run(context)

        self.assertEqual(execution.result.reason_code, "NO_PUBLIC_OPENINGS")
        self.assertEqual(execution.trace["board_portfolio"]["eligible_count"], 2)
        self.assertEqual(execution.trace["board_portfolio"]["attempted_count"], 2)
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 0)

    def test_unvisited_first_party_action_keeps_portfolio_incomplete(self):
        trace = self._action_trace("visited")
        trace["career_actions"].append(
            {
                "target_url": "https://jobs.example/retail",
                "source_url": self.career,
                "kind": "browse_jobs",
                "confidence": "high",
                "status": "scheduled",
            }
        )
        agent = self._agent_with_trace(trace)

        _selected, _trace, portfolio = agent.find_job_board_portfolio(self.career)

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertFalse(portfolio.eligible_set_complete)

    def test_provider_alternative_without_action_set_stays_incomplete(self):
        primary_url = "https://nested.example/primary"
        trace = {
            "pages_visited": [{"url": self.career}],
            "candidates": [
                {
                    "url": self.nested,
                    "source_url": self.career,
                    "origin": "embedded_url",
                }
            ],
        }
        agent = self._agent_with_trace(trace)
        primary = DiscoveredJobBoard(
            board=JobBoard(primary_url, "alternative", "primary"),
            detection_method="linked_url_evidence",
            evidence_url=primary_url,
            relationship_evidence_url=self.career,
        )
        agent.find_job_board_with_evidence = lambda *args, **kwargs: (
            primary_url,
            trace,
            primary,
        )

        _selected, _trace, portfolio = agent.find_job_board_portfolio(self.career)

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(len(portfolio.boards), 2)
        self.assertFalse(portfolio.eligible_set_complete)

    def test_all_explicit_provider_page_links_can_complete_portfolio(self):
        primary_url = "https://nested.example/primary"
        alternative_url = "https://nested.example/retail"
        trace = {
            "pages_visited": [{"url": self.career}],
            "candidates": [
                {
                    "url": primary_url,
                    "source_url": self.career,
                    "origin": "page_link",
                    "text": "Corporate Opportunities",
                },
                {
                    "url": alternative_url,
                    "source_url": self.career,
                    "origin": "page_link",
                    "text": "Retail Opportunities",
                },
            ],
        }
        agent = self._agent_with_trace(trace)
        primary = DiscoveredJobBoard(
            board=JobBoard(primary_url, "alternative", "primary"),
            detection_method="linked_url_evidence",
            evidence_url=primary_url,
            relationship_evidence_url=self.career,
        )
        agent.find_job_board_with_evidence = lambda *args, **kwargs: (
            primary_url,
            trace,
            primary,
        )

        _selected, _trace, portfolio = agent.find_job_board_portfolio(self.career)

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(len(portfolio.boards), 2)
        self.assertTrue(portfolio.eligible_set_complete)


if __name__ == "__main__":
    unittest.main()
