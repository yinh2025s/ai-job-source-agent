from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import RLock
from types import MappingProxyType
from typing import Any

from .candidate_reasoning_contracts import (
    DECISION_KINDS,
    LLMReasoningClient,
    StructuredLLMRequest,
    StructuredLLMResponse,
    TokenUsage,
)


_ONE_MILLION = Decimal("1000000")


class BudgetExceeded(RuntimeError):
    """A call was rejected before dispatch because its conservative budget was unavailable."""


class LLMExperimentConfigurationError(ValueError):
    """The experiment budget configuration is invalid."""


class LLMExperimentAccountingError(RuntimeError):
    """A request, response, or provider usage record cannot be accounted safely."""


@dataclass(frozen=True)
class LLMExperimentBudgetConfig:
    max_calls: int
    hard_cost_cap_usd: Decimal | int | float
    input_cache_miss_usd_per_million: Decimal | int | float
    output_usd_per_million: Decimal | int | float
    prompt_overhead_token_reserve: int
    max_output_tokens_by_decision_kind: Mapping[str, int]

    def __post_init__(self) -> None:
        if isinstance(self.max_calls, bool) or not isinstance(self.max_calls, int):
            raise LLMExperimentConfigurationError("max_calls must be an integer")
        if not 0 <= self.max_calls <= 36:
            raise LLMExperimentConfigurationError("max_calls must be between 0 and 36")
        if (
            isinstance(self.prompt_overhead_token_reserve, bool)
            or not isinstance(self.prompt_overhead_token_reserve, int)
        ):
            raise LLMExperimentConfigurationError(
                "prompt_overhead_token_reserve must be an integer"
            )
        if self.prompt_overhead_token_reserve < 0:
            raise LLMExperimentConfigurationError(
                "prompt_overhead_token_reserve must be nonnegative"
            )

        cap = _finite_decimal(self.hard_cost_cap_usd, "hard_cost_cap_usd")
        input_price = _finite_decimal(
            self.input_cache_miss_usd_per_million,
            "input_cache_miss_usd_per_million",
        )
        output_price = _finite_decimal(
            self.output_usd_per_million,
            "output_usd_per_million",
        )
        if cap < 0:
            raise LLMExperimentConfigurationError(
                "hard_cost_cap_usd must be nonnegative"
            )
        if input_price < 0 or output_price < 0:
            raise LLMExperimentConfigurationError("token prices must be nonnegative")

        limits = self.max_output_tokens_by_decision_kind
        if not isinstance(limits, Mapping):
            raise LLMExperimentConfigurationError(
                "max_output_tokens_by_decision_kind must be a mapping"
            )
        if set(limits) != set(DECISION_KINDS):
            raise LLMExperimentConfigurationError(
                "max_output_tokens_by_decision_kind must define every supported decision kind"
            )
        frozen_limits: dict[str, int] = {}
        for decision_kind, limit in limits.items():
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise LLMExperimentConfigurationError(
                    "max output token limits must be positive integers"
                )
            frozen_limits[decision_kind] = limit

        object.__setattr__(self, "hard_cost_cap_usd", cap)
        object.__setattr__(self, "input_cache_miss_usd_per_million", input_price)
        object.__setattr__(self, "output_usd_per_million", output_price)
        object.__setattr__(
            self,
            "max_output_tokens_by_decision_kind",
            MappingProxyType(frozen_limits),
        )


@dataclass(frozen=True)
class LLMBudgetSnapshot:
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_actual_cost_usd: Decimal
    remaining_cost_cap_usd: Decimal
    call_latencies_ms: tuple[float, ...]
    calls_by_decision_kind: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _CallLedgerEntry:
    decision_kind: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    actual_cost_usd: Decimal


class BudgetedLLMReasoningClient:
    """Serialize LLM calls and enforce conservative call and cost limits."""

    def __init__(
        self,
        client: LLMReasoningClient,
        config: LLMExperimentBudgetConfig,
    ) -> None:
        if not isinstance(config, LLMExperimentBudgetConfig):
            raise LLMExperimentConfigurationError(
                "config must use LLMExperimentBudgetConfig"
            )
        complete = getattr(client, "complete", None)
        if not callable(complete):
            raise LLMExperimentConfigurationError(
                "client must implement LLMReasoningClient"
            )
        self._client = client
        self._config = config
        self._lock = RLock()
        self._ledger: list[_CallLedgerEntry] = []

    @property
    def config(self) -> LLMExperimentBudgetConfig:
        return self._config

    def complete(
        self,
        request: StructuredLLMRequest,
        *,
        timeout_seconds: float = 8.0,
    ) -> StructuredLLMResponse:
        if not isinstance(request, StructuredLLMRequest):
            raise LLMExperimentAccountingError(
                "request must use StructuredLLMRequest"
            )

        with self._lock:
            decision_kind = request.decision_kind
            output_reserve = self._config.max_output_tokens_by_decision_kind.get(
                decision_kind
            )
            if output_reserve is None:
                raise LLMExperimentAccountingError("unsupported decision kind")
            if len(self._ledger) >= self._config.max_calls:
                raise BudgetExceeded("LLM experiment call budget exhausted")

            input_reserve = (
                _serialized_request_utf8_bytes(request)
                + self._config.prompt_overhead_token_reserve
            )
            reserved_cost = self._cost(input_reserve, output_reserve)
            if self._actual_cost() + reserved_cost > self._config.hard_cost_cap_usd:
                raise BudgetExceeded("LLM experiment cost budget exhausted")

            started = time.perf_counter()
            try:
                response = self._client.complete(
                    request,
                    timeout_seconds=timeout_seconds,
                )
            except Exception:
                self._append_entry(
                    decision_kind=decision_kind,
                    started=started,
                    usage=TokenUsage(0, 0, 0),
                )
                raise

            latency_ms = _elapsed_ms(started)
            try:
                usage = _validated_response_usage(response)
            except LLMExperimentAccountingError:
                self._ledger.append(
                    _CallLedgerEntry(
                        decision_kind=decision_kind,
                        latency_ms=latency_ms,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        actual_cost_usd=Decimal("0"),
                    )
                )
                raise

            actual_cost = self._cost(usage.prompt_tokens, usage.completion_tokens)
            self._ledger.append(
                _CallLedgerEntry(
                    decision_kind=decision_kind,
                    latency_ms=latency_ms,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    actual_cost_usd=actual_cost,
                )
            )
            if usage.prompt_tokens > input_reserve:
                raise LLMExperimentAccountingError(
                    "provider prompt usage exceeded the conservative input reserve"
                )
            if usage.completion_tokens > output_reserve:
                raise LLMExperimentAccountingError(
                    "provider completion usage exceeded the configured output reserve"
                )
            if self._actual_cost() > self._config.hard_cost_cap_usd:
                raise LLMExperimentAccountingError(
                    "provider usage exceeded the experiment hard cost cap"
                )
            return response

    def snapshot(self) -> LLMBudgetSnapshot:
        with self._lock:
            prompt_tokens = sum(entry.prompt_tokens for entry in self._ledger)
            completion_tokens = sum(entry.completion_tokens for entry in self._ledger)
            total_tokens = sum(entry.total_tokens for entry in self._ledger)
            actual_cost = self._actual_cost()
            call_counts = {
                decision_kind: sum(
                    entry.decision_kind == decision_kind for entry in self._ledger
                )
                for decision_kind in sorted(DECISION_KINDS)
            }
            return LLMBudgetSnapshot(
                call_count=len(self._ledger),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_actual_cost_usd=actual_cost,
                remaining_cost_cap_usd=max(
                    Decimal("0"),
                    self._config.hard_cost_cap_usd - actual_cost,
                ),
                call_latencies_ms=tuple(
                    entry.latency_ms for entry in self._ledger
                ),
                calls_by_decision_kind=tuple(call_counts.items()),
            )

    def _append_entry(
        self,
        *,
        decision_kind: str,
        started: float,
        usage: TokenUsage,
    ) -> None:
        self._ledger.append(
            _CallLedgerEntry(
                decision_kind=decision_kind,
                latency_ms=_elapsed_ms(started),
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                actual_cost_usd=self._cost(
                    usage.prompt_tokens,
                    usage.completion_tokens,
                ),
            )
        )

    def _actual_cost(self) -> Decimal:
        return sum(
            (entry.actual_cost_usd for entry in self._ledger),
            start=Decimal("0"),
        )

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return (
            Decimal(prompt_tokens)
            * self._config.input_cache_miss_usd_per_million
            + Decimal(completion_tokens) * self._config.output_usd_per_million
        ) / _ONE_MILLION


def _finite_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise LLMExperimentConfigurationError(f"{label} must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise LLMExperimentConfigurationError(f"{label} must be finite")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LLMExperimentConfigurationError(f"{label} must be finite") from exc
    if not decimal_value.is_finite():
        raise LLMExperimentConfigurationError(f"{label} must be finite")
    return decimal_value


def _serialized_request_utf8_bytes(request: StructuredLLMRequest) -> int:
    try:
        encoded = json.dumps(
            {
                "decision_kind": request.decision_kind,
                "schema_name": request.schema_name,
                "payload": _thaw_json(request.payload),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LLMExperimentAccountingError(
            "structured request cannot be serialized for budget accounting"
        ) from exc
    return len(encoded)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validated_response_usage(response: object) -> TokenUsage:
    if not isinstance(response, StructuredLLMResponse):
        raise LLMExperimentAccountingError(
            "provider response must use StructuredLLMResponse"
        )
    usage = response.token_usage
    if not isinstance(usage, TokenUsage):
        raise LLMExperimentAccountingError("provider response has invalid token usage")
    values = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise LLMExperimentAccountingError("provider token usage must use integers")
    if any(value < 0 for value in values):
        raise LLMExperimentAccountingError("provider token usage must be nonnegative")
    if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
        raise LLMExperimentAccountingError("provider token usage total is inconsistent")
    return usage


def _elapsed_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1000.0)
