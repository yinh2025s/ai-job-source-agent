import tempfile
import unittest

from job_source_agent.composition import AgentConfig, FetcherConfig, build_application, build_fetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.snapshot import SnapshottingFetcher
from job_source_agent.web import Fetcher


class CompositionTests(unittest.TestCase):
    def test_static_fetcher_is_default(self):
        fetcher = build_fetcher(FetcherConfig(offline=True))

        self.assertIsInstance(fetcher, Fetcher)
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

        self.assertIsInstance(fetcher, SnapshottingFetcher)
        self.assertIsInstance(fetcher.fetcher, RetryingFetcher)
        self.assertIsInstance(fetcher.fetcher.fetcher, SmartRenderedFetcher)

    def test_retry_deadline_is_injected_by_composition(self):
        fetcher = build_fetcher(
            FetcherConfig(offline=True, retries=1, retry_deadline=123.5)
        )

        self.assertIsInstance(fetcher, RetryingFetcher)
        self.assertEqual(fetcher._deadline, 123.5)

    def test_application_shares_registry_between_agent_and_matcher_boundary(self):
        application = build_application(
            FetcherConfig(offline=True),
            AgentConfig(enable_career_search=False),
        )

        self.assertIs(application.agent.fetcher, application.fetcher)
        self.assertIs(application.agent.provider_registry, application.provider_registry)
        self.assertFalse(application.agent.enable_career_search)

    def test_unknown_render_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            build_fetcher(FetcherConfig(render_mode="magic"))


if __name__ == "__main__":
    unittest.main()
