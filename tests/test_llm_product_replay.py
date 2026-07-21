from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote_plus

from job_source_agent.candidate_reasoning_contracts import (
    CandidateRankerDecision,
    QueryPlannerDecision,
    RankedCandidate,
    SearchQuerySpec,
)
from job_source_agent.candidate_reasoning_coordinator import CandidateReasoningCoordinator
from job_source_agent.candidate_reasoning_search import ResolverCandidateSearchBackend
from job_source_agent.candidate_reasoning_service import (
    CandidateReasoningInvocationService,
    CandidateReasoningRuntime,
)
from job_source_agent.composition import build_application_from_fetcher
from job_source_agent.llm_decision_bundle import AuditedLLMDecisionStore
from job_source_agent.models import CompanyInput, STAGE_WEBSITE_RESOLUTION
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from job_source_agent.web import Fetcher, Page
from job_source_agent.website_resolver import CompanyWebsiteResolver
from scripts.replay_failure_bundle import FailureReplayError, _prepare_llm_decision_replay


EXECUTION = "7" * 64
ADAPTER = "fake-adapter-v1"


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, request):
        self.calls += 1
        return QueryPlannerDecision(
            "Example Labs",
            ("Example", "Labs"),
            (),
            (),
            (
                SearchQuerySpec(
                    "Example Labs Seattle software corporate",
                    "official_website",
                ),
            ),
            False,
            ("NO_SOURCE_BACKED_CANDIDATE",),
        )


class _Ranker:
    def __init__(self) -> None:
        self.calls = 0

    def rank(self, request):
        self.calls += 1
        return CandidateRankerDecision(
            tuple(
                RankedCandidate(
                    candidate.candidate_id,
                    "high",
                    (candidate.candidate_id,),
                    ("BRAND_MATCH", "LOCATION_MATCH"),
                )
                for candidate in request.candidates
            ),
            False,
        )


class _DeterministicProductFetcher(Fetcher):
    def __init__(self) -> None:
        super().__init__(offline=True)

    def fetch(self, url, data=None, headers=None, *, interaction=None):
        decoded = unquote_plus(url)
        if "Seattle software corporate" in decoded:
            return Page(
                url=url,
                final_url=url,
                html=(
                    "<rss><channel><item><title>Example Labs official website</title>"
                    "<link>https://official-example.test/</link>"
                    "<description>Example Labs software company in Seattle</description>"
                    "</item></channel></rss>"
                ),
            )
        if "bing.com/" in url or "duckduckgo.com/" in url:
            return Page(url=url, final_url=url, html="<rss><channel></channel></rss>")
        if url.startswith("https://official-example.test"):
            return Page(
                url=url,
                final_url="https://official-example.test/",
                html=(
                    "<html><title>Example Labs</title><body>"
                    "Example Labs software company in Seattle"
                    "<a href='/careers'>Careers</a></body></html>"
                ),
            )
        return Page(
            url=url,
            final_url=url,
            html="<html><title>Unrelated Corporation</title></html>",
        )


class LLMProductReplayTest(unittest.TestCase):
    def test_live_decisions_enter_bundle_and_product_replay_calls_no_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_configuration = self._run_configuration()
            planner = _Planner()
            ranker = _Ranker()
            fetcher = _DeterministicProductFetcher()
            live_resolver = CompanyWebsiteResolver(fetcher)
            audited_store = AuditedLLMDecisionStore(
                root / "live-decisions",
                execution_identity=EXECUTION,
                run_configuration_digest=run_configuration.digest,
                llm_provider=run_configuration.llm_provider,
                model_id=run_configuration.llm_model,
                prompt_version=run_configuration.llm_prompt_version,
                adapter_version=ADAPTER,
            )
            live_service = self._live_service(
                planner,
                ranker,
                live_resolver,
                audited_store,
                run_configuration,
            )
            company = CompanyInput(
                company_name="Example Labs",
                linkedin_company_url="https://www.linkedin.com/company/example-labs",
                job_title="AI Engineer",
                job_location="Seattle, WA",
            )
            live_application = build_application_from_fetcher(
                fetcher,
                run_configuration=run_configuration,
                candidate_reasoning_service=live_service,
            )

            live = live_application.pipeline.discover(
                company,
                stop_after=STAGE_WEBSITE_RESOLUTION,
                execution_fingerprint_override=EXECUTION,
            )

            self.assertEqual(live.company_website_url, "https://official-example.test/")
            self.assertEqual(planner.calls, 1)
            self.assertEqual(ranker.calls, 1)
            replay_store, service_factory, provenance = _prepare_llm_decision_replay(
                SimpleNamespace(llm_decision_dir=str(root / "live-decisions")),
                root / "bundle",
                [company],
                run_configuration,
            )
            self.assertEqual(provenance["record_count"], 2)

            replay_application = build_application_from_fetcher(
                _DeterministicProductFetcher(),
                run_configuration=run_configuration,
                candidate_reasoning_service_factory=service_factory,
            )
            replay = replay_application.pipeline.discover(
                company,
                stop_after=STAGE_WEBSITE_RESOLUTION,
                execution_fingerprint_override=EXECUTION,
            )

            self.assertEqual(replay.company_website_url, live.company_website_url)
            replay_store.assert_consumed()
            self.assertEqual(planner.calls, 1)
            self.assertEqual(ranker.calls, 1)

            incompatible = DeterministicRunConfig.from_agent_config(
                AgentConfig(
                    enable_parallel_candidate_discovery=True,
                    enable_llm_candidate_reasoning=True,
                    llm_provider="fake-provider",
                    llm_model="different-model",
                    llm_prompt_version="prompt-v1",
                )
            )
            with self.assertRaisesRegex(
                FailureReplayError,
                "LLM_DECISION_BUNDLE_INCOMPATIBLE",
            ):
                _prepare_llm_decision_replay(
                    SimpleNamespace(
                        llm_decision_dir=str(root / "live-decisions")
                    ),
                    root / "incompatible-bundle",
                    [company],
                    incompatible,
                )

    def test_flag_off_needs_no_decision_artifact_and_changes_nothing(self):
        run_configuration = DeterministicRunConfig.from_agent_config(AgentConfig())
        company = CompanyInput(company_name="Example Labs")

        store, factory, provenance = _prepare_llm_decision_replay(
            SimpleNamespace(llm_decision_dir=None),
            Path("/not-used"),
            [company],
            run_configuration,
        )

        self.assertIsNone(store)
        self.assertIsNone(factory)
        self.assertIsNone(provenance)
        self.assertEqual(run_configuration.to_payload()["schema_version"], "1.4")

    @staticmethod
    def _run_configuration() -> DeterministicRunConfig:
        return DeterministicRunConfig.from_agent_config(
            AgentConfig(
                enable_parallel_candidate_discovery=True,
                enable_llm_candidate_reasoning=True,
                llm_provider="fake-provider",
                llm_model="fake-model",
                llm_prompt_version="prompt-v1",
                llm_timeout=8.0,
                llm_max_candidates=10,
                llm_max_calls_per_company=2,
            )
        )

    @staticmethod
    def _live_service(
        planner,
        ranker,
        resolver,
        store,
        run_configuration,
    ) -> CandidateReasoningInvocationService:
        coordinator = CandidateReasoningCoordinator(
            planner=planner,
            ranker=ranker,
            search_backend=ResolverCandidateSearchBackend(resolver),
            decision_store=store,
            clock=time.monotonic,
            max_candidates=run_configuration.llm_max_candidates,
            max_calls_per_company=run_configuration.llm_max_calls_per_company,
        )
        runtime = CandidateReasoningRuntime(
            True,
            run_configuration.llm_provider,
            run_configuration.llm_model,
            run_configuration.llm_prompt_version,
            run_configuration.llm_timeout,
            ADAPTER,
            EXECUTION,
        )
        return CandidateReasoningInvocationService(
            coordinator,
            runtime,
            monotonic_clock=time.monotonic,
            wall_clock=time.time,
        )


if __name__ == "__main__":
    unittest.main()
