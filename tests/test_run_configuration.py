import unittest

from job_source_agent.run_configuration import (
    AgentConfig,
    BatchExecutionConfig,
    DeterministicRunConfig,
    combined_configuration_digest,
)


class DeterministicRunConfigTests(unittest.TestCase):
    def test_equivalent_agent_configs_have_identical_canonical_payload_and_digest(self):
        implicit = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_candidates=17, max_career_candidate_fetches=None)
        )
        explicit = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_candidates=17, max_career_candidate_fetches=17)
        )

        self.assertEqual(implicit.to_payload(), explicit.to_payload())
        self.assertEqual(implicit.digest, explicit.digest)
        self.assertEqual(implicit.to_payload()["schema_version"], "1.3")

    def test_parallel_candidate_discovery_is_explicit_and_deterministic(self):
        disabled = DeterministicRunConfig.from_agent_config(AgentConfig())
        enabled = DeterministicRunConfig.from_agent_config(
            AgentConfig(enable_parallel_candidate_discovery=True)
        )

        self.assertFalse(disabled.enable_parallel_candidate_discovery)
        self.assertTrue(enabled.enable_parallel_candidate_discovery)
        self.assertNotEqual(disabled.digest, enabled.digest)
        self.assertTrue(
            enabled.to_payload()["agent"]["enable_parallel_candidate_discovery"]
        )

    def test_round_trip_faithfully_rebuilds_agent_config(self):
        expected = AgentConfig(
            max_candidates=21,
            max_job_pages=13,
            max_job_board_attempts=6,
            max_career_candidate_fetches=9,
            max_career_discovery_transport_calls=7,
            max_career_search_queries=4,
            max_ats_board_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
            career_search_timeout=12.5,
        )

        restored = DeterministicRunConfig.from_payload(
            DeterministicRunConfig.from_agent_config(expected).to_payload()
        ).to_agent_config()

        self.assertEqual(restored, expected)

    def test_schema_1_2_round_trip_and_digest_include_deterministic_limits(self):
        unbounded = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_career_discovery_transport_calls=None)
        )
        bounded = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_career_discovery_transport_calls=0)
        )

        self.assertIsNone(
            unbounded.to_payload()["agent"]["max_career_discovery_transport_calls"]
        )
        self.assertEqual(
            DeterministicRunConfig.from_payload(bounded.to_payload()),
            bounded,
        )
        self.assertNotEqual(unbounded.digest, bounded.digest)
        one_board = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_job_board_attempts=1)
        )
        two_boards = DeterministicRunConfig.from_agent_config(
            AgentConfig(max_job_board_attempts=2)
        )
        self.assertNotEqual(one_board.digest, two_boards.digest)

    def test_schema_1_2_rejects_invalid_job_board_attempt_limits(self):
        payload = DeterministicRunConfig.from_agent_config(AgentConfig()).to_payload()

        for value in (1, 8):
            with self.subTest(value=value):
                bounded = DeterministicRunConfig.from_agent_config(
                    AgentConfig(max_job_board_attempts=value)
                )
                restored = DeterministicRunConfig.from_payload(
                    bounded.to_payload()
                ).to_agent_config()
                self.assertEqual(
                    restored.max_job_board_attempts,
                    value,
                )

        for value in (0, 9, 1.5, True, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    DeterministicRunConfig.from_payload(
                        {
                            **payload,
                            "agent": {
                                **payload["agent"],
                                "max_job_board_attempts": value,
                            },
                        }
                    )

    def test_schema_1_2_rejects_invalid_transport_call_limits(self):
        payload = DeterministicRunConfig.from_agent_config(AgentConfig()).to_payload()

        for value in (-1, 1001, 1.5, True, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    DeterministicRunConfig.from_payload(
                        {
                            **payload,
                            "agent": {
                                **payload["agent"],
                                "max_career_discovery_transport_calls": value,
                            },
                        }
                    )

    def test_schema_1_0_payload_and_digest_are_preserved_exactly(self):
        payload = {
            "schema_version": "1.0",
            "agent": {
                "max_candidates": 17,
                "max_job_pages": 8,
                "max_career_candidate_fetches": 17,
                "max_career_search_queries": 5,
                "max_ats_board_fetches": 5,
                "enable_sitemap_discovery": True,
                "enable_career_search": True,
                "career_search_timeout": None,
            },
        }
        configuration = DeterministicRunConfig.from_payload(payload)

        self.assertEqual(configuration.to_payload(), payload)
        self.assertEqual(
            configuration.digest,
            "ab4af58ca003e9f16ffddf8b4f2e44b28066ce6792147daaf647bcf8f818b73a",
        )
        self.assertIsNone(configuration.to_agent_config().max_career_discovery_transport_calls)
        self.assertEqual(configuration.to_agent_config().max_job_board_attempts, 1)
        with self.assertRaises(ValueError):
            DeterministicRunConfig.from_payload(
                {
                    **payload,
                    "agent": {
                        **payload["agent"],
                        "max_career_discovery_transport_calls": None,
                    },
                }
            )

    def test_schema_1_1_payload_semantics_and_single_board_behavior_are_preserved(self):
        payload = {
            "schema_version": "1.1",
            "agent": {
                "max_candidates": 17,
                "max_job_pages": 8,
                "max_career_candidate_fetches": 17,
                "max_career_search_queries": 5,
                "max_ats_board_fetches": 5,
                "enable_sitemap_discovery": True,
                "enable_career_search": True,
                "career_search_timeout": None,
                "max_career_discovery_transport_calls": 7,
            },
        }
        configuration = DeterministicRunConfig.from_payload(payload)

        self.assertEqual(configuration.to_payload(), payload)
        self.assertEqual(
            configuration.digest,
            "dee2c95e29b32f27a01e5fece2f4703c461772afb378c1aa945988ede1eaf71a",
        )
        self.assertEqual(configuration.to_agent_config().max_career_discovery_transport_calls, 7)
        self.assertEqual(configuration.to_agent_config().max_job_board_attempts, 1)
        with self.assertRaises(ValueError):
            DeterministicRunConfig.from_payload(
                {
                    **payload,
                    "agent": {
                        **payload["agent"],
                        "max_job_board_attempts": 3,
                    },
                }
            )

    def test_payload_rejects_missing_extra_and_invalid_fields(self):
        payload = DeterministicRunConfig.from_agent_config(AgentConfig()).to_payload()
        invalid_payloads = [
            {"schema_version": "1.0"},
            {**payload, "unexpected": True},
            {**payload, "schema_version": "0.9"},
            {**payload, "agent": {k: v for k, v in payload["agent"].items() if k != "max_job_pages"}},
            {**payload, "agent": {**payload["agent"], "unexpected": 1}},
            {
                **payload,
                "agent": {
                    k: v
                    for k, v in payload["agent"].items()
                    if k != "max_career_discovery_transport_calls"
                },
            },
            {**payload, "agent": {**payload["agent"], "max_candidates": True}},
            {**payload, "agent": {**payload["agent"], "enable_career_search": 1}},
        ]

        for invalid in invalid_payloads:
            with self.subTest(payload=invalid):
                with self.assertRaises(ValueError):
                    DeterministicRunConfig.from_payload(invalid)

    def test_payload_rejects_nonfinite_timeout(self):
        payload = DeterministicRunConfig.from_agent_config(AgentConfig()).to_payload()

        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    DeterministicRunConfig.from_payload(
                        {**payload, "agent": {**payload["agent"], "career_search_timeout": timeout}}
                    )

    def test_batch_execution_configuration_is_strict_and_stable(self):
        payload = {
            "schema_version": "1.0",
            "batch": {
                "company_time_budget": 60,
                "website_time_budget": 20,
                "fetch_timeout": 5,
                "fetch_retries": 1,
                "retry_base_delay": 0,
                "render_mode": "smart",
                "render_budget": 2,
                "verify_limit": 3,
                "offline": False,
            },
        }
        configuration = BatchExecutionConfig.from_payload(payload)

        self.assertEqual(BatchExecutionConfig.from_payload(configuration.to_payload()), configuration)
        self.assertRegex(configuration.digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            combined_configuration_digest(configuration.digest, "a" * 64),
            combined_configuration_digest(configuration.digest, "b" * 64),
        )

        for invalid in (
            {**payload, "unexpected": True},
            {**payload, "batch": {**payload["batch"], "company_time_budget": float("inf")}},
            {**payload, "batch": {**payload["batch"], "retry_base_delay": -0.1}},
            {**payload, "batch": {**payload["batch"], "render_mode": "magic"}},
            {**payload, "batch": {**payload["batch"], "offline": 1}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    BatchExecutionConfig.from_payload(invalid)


if __name__ == "__main__":
    unittest.main()
