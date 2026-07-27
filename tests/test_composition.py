import tempfile
import unittest
from pathlib import Path

from job_source_agent.composition import (
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    AgentConfig,
    FetcherConfig,
    build_application,
    build_fetcher,
    _run_candidate_route,
)
from job_source_agent.candidate_discovery_coordinator import (
    CandidateDiscoveryRouteStatus,
)
from job_source_agent.provider_candidates import (
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
)
from job_source_agent.reasons import canonical_reason_code
from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.identity_evidence import FilesystemLinkedInWebsiteEvidenceStore
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.searxng_search_backend import SearxngSearchBackend
from job_source_agent.snapshot import SnapshottingFetcher
from job_source_agent.web import Fetcher


class CompositionTests(unittest.TestCase):
    def test_candidate_route_preserves_deadline_exhaustion(self):
        class DeadlineDiscovery:
            def discover(self, request):
                return CandidateDiscoveryResult(
                    (),
                    {
                        "search": {
                            "queries": [],
                            "stopped_reason": "deadline_exhausted",
                        }
                    },
                )

        output = _run_candidate_route(
            (DeadlineDiscovery(),),
            CandidateDiscoveryRequest(company_name="Acme"),
            producer="provider_search",
            limit=12,
        )

        self.assertEqual(
            output.status,
            CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
        )
        self.assertTrue(output.failure.retryable)
        self.assertEqual(
            canonical_reason_code(output.failure.reason_code),
            "FETCH_BUDGET_EXHAUSTED",
        )
        self.assertIn(
            ("stopped_reasons", "deadline_exhausted"),
            output.provenance.trace,
        )

    def test_candidate_route_counts_queries_and_tenant_probes_without_urls(self):
        class DiagnosticDiscovery:
            def discover(self, request):
                return CandidateDiscoveryResult(
                    (),
                    {
                        "search": {
                            "queries": [
                                {
                                    "result_count": 4,
                                    "candidates": [{"url": "https://example.test"}],
                                    "error": None,
                                },
                                {
                                    "result_count": 0,
                                    "candidates": [],
                                    "error": "search_challenge",
                                },
                            ],
                            "stopped_reason": "no_valid_candidates",
                            "fetch_budget_unavailable": False,
                        },
                        "skipped_candidate_count": 1,
                        "tenant_probe_fallback": {
                            "status": "rejected",
                            "reason": "provider_tenant_probe_limit_reached",
                            "attempts": [{"url": "secret-a"}, {"url": "secret-b"}],
                        },
                    },
                )

        output = _run_candidate_route(
            (DiagnosticDiscovery(),),
            CandidateDiscoveryRequest(company_name="Acme"),
            producer="provider_search",
            limit=12,
        )
        diagnostics = dict(output.provenance.trace)

        self.assertEqual(output.provenance.query_count, 2)
        self.assertEqual(output.provenance.request_count, 4)
        self.assertEqual(diagnostics["raw_result_count"], "4")
        self.assertEqual(diagnostics["accepted_search_result_count"], "1")
        self.assertEqual(diagnostics["query_error_count"], "1")
        self.assertEqual(diagnostics["tenant_probe_attempt_count"], "2")
        self.assertNotIn("secret", repr(output.provenance.trace))

    def test_static_fetcher_is_default(self):
        fetcher = build_fetcher(FetcherConfig(offline=True))

        self.assertIsInstance(fetcher, PageCacheFetcher)
        self.assertIsInstance(fetcher.fetcher, CareerTransportBudgetFetcher)
        self.assertIsInstance(fetcher.fetcher.fetcher, Fetcher)
        self.assertTrue(fetcher.offline)

    def test_fetch_behaviors_are_composed_in_one_place(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher = build_fetcher(
                FetcherConfig(
                    render_mode="smart",
                    retries=2,
                    snapshot_dir=directory,
                )
            )

        self.assertIsInstance(fetcher, PageCacheFetcher)
        self.assertIsInstance(fetcher.fetcher, SnapshottingFetcher)
        self.assertIsInstance(fetcher.fetcher.fetcher, RetryingFetcher)
        self.assertIsInstance(
            fetcher.fetcher.fetcher.fetcher,
            CareerTransportBudgetFetcher,
        )
        self.assertIsInstance(
            fetcher.fetcher.fetcher.fetcher.fetcher,
            SmartRenderedFetcher,
        )

    def test_retry_deadline_is_injected_by_composition(self):
        fetcher = build_fetcher(
            FetcherConfig(offline=True, retries=1, retry_deadline=123.5)
        )

        self.assertIsInstance(fetcher, PageCacheFetcher)
        self.assertIsInstance(fetcher.fetcher, RetryingFetcher)
        self.assertEqual(fetcher._deadline, 123.5)

    def test_deadline_wrapper_is_present_even_when_retries_are_disabled(self):
        fetcher = build_fetcher(
            FetcherConfig(offline=True, retries=0, retry_deadline=123.5)
        )

        self.assertIsInstance(fetcher, PageCacheFetcher)
        self.assertIsInstance(fetcher.fetcher, RetryingFetcher)
        self.assertEqual(fetcher.max_retries, 0)

    def test_application_shares_registry_between_agent_and_matcher_boundary(self):
        application = build_application(
            FetcherConfig(offline=True),
            AgentConfig(enable_career_search=False),
        )

        self.assertIs(application.agent.fetcher, application.fetcher)
        self.assertIs(application.agent.provider_registry, application.provider_registry)
        self.assertFalse(application.agent.enable_career_search)

    def test_application_wires_career_transport_limit_to_agent(self):
        application = build_application(
            FetcherConfig(offline=True),
            AgentConfig(max_career_discovery_transport_calls=17),
        )

        self.assertEqual(application.agent.max_career_discovery_transport_calls, 17)

    def test_application_shares_configured_search_backend_across_search_routes(self):
        backend = SearxngSearchBackend("https://search.example")
        backend_configuration = backend.public_configuration()
        application = build_application(
            FetcherConfig(offline=True),
            AgentConfig(**backend_configuration),
            search_backend=backend,
        )

        candidate_discovery = application.pipeline.runner.stages[4].candidate_discovery
        search_discoveries = candidate_discovery._discoveries[2:]
        self.assertIs(application.agent.search_backend, backend)
        self.assertEqual(len(search_discoveries), 2)
        self.assertTrue(
            all(discovery.resolver.search_backend is backend for discovery in search_discoveries)
        )

    def test_application_rejects_search_backend_configuration_mismatch(self):
        backend = SearxngSearchBackend("https://search.example")

        with self.assertRaisesRegex(ValueError, "search_backend"):
            build_application(
                FetcherConfig(offline=True),
                AgentConfig(),
                search_backend=backend,
            )

    def test_coordinator_configures_s4_provider_search_reservation(self):
        application = build_application(
            FetcherConfig(offline=True, retry_deadline=20),
            AgentConfig(
                enable_parallel_candidate_discovery=True,
                candidate_discovery_engine="coordinator_v2",
                provider_search_reserve_seconds=7.5,
            ),
        )
        retrying = application.fetcher.fetcher

        self.assertIsInstance(retrying, RetryingFetcher)
        self.assertEqual(
            retrying._stage_reservations,
            {"career_discovery": 7.5},
        )
        self.assertIs(
            application.pipeline.runner._stage_budget_controller,
            application.fetcher,
        )

    def test_application_uses_explicit_linkedin_evidence_cache_path(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit_path = Path(directory) / "shared" / "evidence.json"
            application = build_application(
                FetcherConfig(offline=True),
                checkpoint_dir=Path(directory) / "checkpoints",
                linkedin_evidence_cache_path=explicit_path,
            )

        store = application.pipeline.runner.stages[1].service.linkedin_evidence_store
        self.assertIsInstance(store, FilesystemLinkedInWebsiteEvidenceStore)
        self.assertEqual(store.path, explicit_path)

    def test_application_defaults_evidence_cache_to_checkpoint_root(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_dir = Path(directory) / "checkpoints"
            application = build_application(
                FetcherConfig(offline=True),
                checkpoint_dir=checkpoint_dir,
            )

        store = application.pipeline.runner.stages[1].service.linkedin_evidence_store
        self.assertIsInstance(store, FilesystemLinkedInWebsiteEvidenceStore)
        self.assertEqual(
            store.path,
            checkpoint_dir / LINKEDIN_EVIDENCE_CACHE_FILENAME,
        )

    def test_unknown_render_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            build_fetcher(FetcherConfig(render_mode="magic"))


if __name__ == "__main__":
    unittest.main()
