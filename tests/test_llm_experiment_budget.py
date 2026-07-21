from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, FrozenInstanceError
from decimal import Decimal

from job_source_agent.candidate_reasoning_contracts import (
    StructuredLLMRequest,
    StructuredLLMResponse,
    TokenUsage,
)
from job_source_agent.llm_experiment_budget import (
    BudgetedLLMReasoningClient,
    BudgetExceeded,
    LLMExperimentAccountingError,
    LLMExperimentBudgetConfig,
    LLMExperimentConfigurationError,
)


class RecordingClient:
    def __init__(self, *responses: object, delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def complete(self, request):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        finally:
            with self.lock:
                self.active -= 1


def request(kind: str = "query_plan", payload=None):
    return StructuredLLMRequest(
        decision_kind=kind,
        schema_name="test_schema",
        payload=payload or {"company": "Example"},
    )


def response(prompt=10, completion=4):
    return StructuredLLMResponse(
        payload={"ok": True},
        raw_response_id="provider-response-id",
        token_usage=TokenUsage(prompt, completion, prompt + completion),
    )


def config(**overrides):
    values = {
        "max_calls": 3,
        "hard_cost_cap_usd": Decimal("1"),
        "input_cache_miss_usd_per_million": Decimal("1"),
        "output_usd_per_million": Decimal("2"),
        "prompt_overhead_token_reserve": 100,
        "max_output_tokens_by_decision_kind": {
            "query_plan": 50,
            "candidate_rank": 75,
        },
    }
    values.update(overrides)
    return LLMExperimentBudgetConfig(**values)


class LLMExperimentBudgetTest(unittest.TestCase):
    def test_max_call_limit_fails_before_delegate(self):
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(delegate, config(max_calls=1))

        client.complete(request())
        with self.assertRaises(BudgetExceeded):
            client.complete(request())

        self.assertEqual(delegate.calls, 1)
        self.assertEqual(client.snapshot().call_count, 1)

    def test_zero_call_budget_disables_dispatch(self):
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(delegate, config(max_calls=0))

        with self.assertRaises(BudgetExceeded):
            client.complete(request())

        self.assertEqual(delegate.calls, 0)

    def test_cost_cap_fails_before_delegate(self):
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(
            delegate,
            config(
                hard_cost_cap_usd=Decimal("0.000001"),
                input_cache_miss_usd_per_million=Decimal("1"),
            ),
        )

        with self.assertRaises(BudgetExceeded):
            client.complete(request())

        self.assertEqual(delegate.calls, 0)
        self.assertEqual(client.snapshot().call_count, 0)

    def test_cost_preflight_includes_spend_from_prior_calls(self):
        structured = request()
        serialized_size = len(
            json.dumps(
                {
                    "decision_kind": structured.decision_kind,
                    "schema_name": structured.schema_name,
                    "payload": {"company": "Example"},
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        first_actual_cost = Decimal("0.000010")
        next_reserve_cost = Decimal(serialized_size) / Decimal("1000000")
        delegate = RecordingClient(response(prompt=10, completion=0))
        client = BudgetedLLMReasoningClient(
            delegate,
            config(
                hard_cost_cap_usd=(
                    first_actual_cost
                    + next_reserve_cost
                    - Decimal("0.0000001")
                ),
                output_usd_per_million=Decimal("0"),
                prompt_overhead_token_reserve=0,
            ),
        )

        client.complete(structured)
        with self.assertRaises(BudgetExceeded):
            client.complete(structured)

        self.assertEqual(delegate.calls, 1)

    def test_provider_failure_is_not_retried_and_counts_zero_usage_attempt(self):
        delegate = RecordingClient(RuntimeError("provider unavailable"))
        client = BudgetedLLMReasoningClient(delegate, config())

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            client.complete(request())

        snapshot = client.snapshot()
        self.assertEqual(delegate.calls, 1)
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(snapshot.prompt_tokens, 0)
        self.assertEqual(snapshot.completion_tokens, 0)
        self.assertEqual(snapshot.total_tokens, 0)
        self.assertEqual(snapshot.estimated_actual_cost_usd, Decimal("0"))
        self.assertEqual(len(snapshot.call_latencies_ms), 1)

    def test_success_uses_provider_usage_for_cost_math(self):
        delegate = RecordingClient(response(prompt=500, completion=200))
        client = BudgetedLLMReasoningClient(
            delegate,
            config(
                input_cache_miss_usd_per_million=Decimal("0.25"),
                output_usd_per_million=Decimal("2"),
                prompt_overhead_token_reserve=1000,
                max_output_tokens_by_decision_kind={
                    "query_plan": 200,
                    "candidate_rank": 300,
                },
            ),
        )

        client.complete(request())

        snapshot = client.snapshot()
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(snapshot.prompt_tokens, 500)
        self.assertEqual(snapshot.completion_tokens, 200)
        self.assertEqual(snapshot.total_tokens, 700)
        self.assertEqual(snapshot.estimated_actual_cost_usd, Decimal("0.000525"))
        self.assertEqual(snapshot.remaining_cost_cap_usd, Decimal("0.999475"))
        self.assertEqual(
            snapshot.calls_by_decision_kind,
            (("candidate_rank", 0), ("query_plan", 1)),
        )

    def test_utf8_input_reserve_uses_bytes_not_optimistic_character_estimate(self):
        structured = request(payload={"company": "深度求索"})
        serialized = json.dumps(
            {
                "decision_kind": structured.decision_kind,
                "schema_name": structured.schema_name,
                "payload": {"company": "深度求索"},
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        reserve_cost = Decimal(len(serialized)) / Decimal("1000000")
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(
            delegate,
            config(
                hard_cost_cap_usd=reserve_cost - Decimal("0.0000001"),
                output_usd_per_million=Decimal("0"),
                prompt_overhead_token_reserve=0,
            ),
        )

        with self.assertRaises(BudgetExceeded):
            client.complete(structured)

        self.assertGreater(len(serialized), len(serialized.decode("utf-8")))
        self.assertEqual(delegate.calls, 0)

    def test_lock_serializes_preflight_delegate_and_accounting(self):
        delegate = RecordingClient(*(response() for _ in range(8)), delay=0.01)
        client = BudgetedLLMReasoningClient(delegate, config(max_calls=8))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: client.complete(request()), range(8)))

        self.assertEqual(delegate.calls, 8)
        self.assertEqual(delegate.max_active, 1)
        self.assertEqual(client.snapshot().call_count, 8)

    def test_snapshot_is_immutable_and_contains_no_payload_or_secrets(self):
        secret = "sk-sensitive-secret"
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(
            delegate,
            config(prompt_overhead_token_reserve=1000),
        )
        client.complete(request(payload={"company": secret}))

        snapshot = client.snapshot()
        serialized = repr(asdict(snapshot))
        self.assertNotIn(secret, serialized)
        self.assertNotIn("provider-response-id", serialized)
        self.assertNotIn("url", serialized.lower())
        with self.assertRaises(FrozenInstanceError):
            snapshot.call_count = 99

    def test_invalid_config_response_usage_and_decision_kind_fail_closed(self):
        invalid_configs = (
            {"max_calls": 37},
            {"hard_cost_cap_usd": float("nan")},
            {"input_cache_miss_usd_per_million": float("inf")},
            {"output_usd_per_million": Decimal("-1")},
            {"prompt_overhead_token_reserve": -1},
            {"max_output_tokens_by_decision_kind": {"query_plan": 1}},
        )
        for overrides in invalid_configs:
            with self.subTest(overrides=overrides):
                with self.assertRaises(LLMExperimentConfigurationError):
                    config(**overrides)

        malformed = BudgetedLLMReasoningClient(RecordingClient({"payload": {}}), config())
        with self.assertRaises(LLMExperimentAccountingError):
            malformed.complete(request())
        self.assertEqual(malformed.snapshot().call_count, 1)

        unsupported = object.__new__(StructuredLLMRequest)
        object.__setattr__(unsupported, "decision_kind", "unsupported")
        object.__setattr__(unsupported, "schema_name", "test_schema")
        object.__setattr__(unsupported, "payload", {})
        delegate = RecordingClient(response())
        client = BudgetedLLMReasoningClient(delegate, config())
        with self.assertRaises(LLMExperimentAccountingError):
            client.complete(unsupported)
        self.assertEqual(delegate.calls, 0)

        malformed_usage = response()
        object.__setattr__(malformed_usage, "token_usage", TokenUsage(1, 1, 2))
        object.__setattr__(malformed_usage.token_usage, "total_tokens", 3)
        bad_usage_client = BudgetedLLMReasoningClient(
            RecordingClient(malformed_usage),
            config(),
        )
        with self.assertRaises(LLMExperimentAccountingError):
            bad_usage_client.complete(request())
        self.assertEqual(bad_usage_client.snapshot().call_count, 1)

    def test_config_and_nested_output_limits_are_immutable(self):
        budget_config = config()

        with self.assertRaises(FrozenInstanceError):
            budget_config.max_calls = 10
        with self.assertRaises(TypeError):
            budget_config.max_output_tokens_by_decision_kind["query_plan"] = 999

    def test_usage_beyond_reserved_limits_is_accounted_then_rejected(self):
        delegate = RecordingClient(response(prompt=10, completion=51))
        client = BudgetedLLMReasoningClient(delegate, config())

        with self.assertRaises(LLMExperimentAccountingError):
            client.complete(request())

        snapshot = client.snapshot()
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(snapshot.completion_tokens, 51)
        self.assertGreater(snapshot.estimated_actual_cost_usd, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
