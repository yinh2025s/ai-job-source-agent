import tempfile
import unittest
from pathlib import Path

from job_source_agent.composition import (
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    AgentConfig,
    FetcherConfig,
    build_application,
    build_fetcher,
)
from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.identity_evidence import FilesystemLinkedInWebsiteEvidenceStore
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.snapshot import SnapshottingFetcher
from job_source_agent.web import Fetcher


class CompositionTests(unittest.TestCase):
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
