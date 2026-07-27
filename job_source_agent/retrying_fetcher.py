from __future__ import annotations

import random
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .browser_interaction import BrowserInteraction
from .reasons import classify_fetch_error, reason_spec
from .web import FetchError, Page, normalize_transport_exception


@dataclass
class RetryEvent:
    url: str
    attempt: int
    reason_code: str | None
    retryable: bool
    error: str | None
    delay: float
    outcome: str
    transport_phase: str | None = None
    policy: str | None = None


class RetryingFetcher:
    """Retry transient fetch failures within a bounded wall-clock deadline."""

    def __init__(
        self,
        fetcher,
        max_retries: int = 1,
        base_delay: float = 0.25,
        backoff_factor: float = 2.0,
        *,
        max_delay: float = 8.0,
        jitter_ratio: float = 0.25,
        rng: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deadline: float | Callable[[], float | None] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.max_retries = max(0, max_retries)
        self.base_delay = max(0.0, base_delay)
        self.backoff_factor = max(1.0, backoff_factor)
        self.max_delay = max(0.0, max_delay)
        self.jitter_ratio = min(1.0, max(0.0, jitter_ratio))
        self._rng = rng or random.random
        self._sleeper = sleeper
        self._clock = clock
        self._deadline = deadline
        self.timeout = getattr(fetcher, "timeout", None)
        self.retry_events: list[dict[str, Any]] = []
        self._policy_state = threading.local()
        self._stage_budget_state = threading.local()
        self._stage_reservations: dict[str, float] = {}

    def configure_stage_reservations(self, reservations: dict[str, float]) -> None:
        normalized: dict[str, float] = {}
        for stage, seconds in reservations.items():
            if not isinstance(stage, str) or not stage:
                raise ValueError("Stage reservation name is invalid")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                raise ValueError("Stage reservation must be numeric")
            value = float(seconds)
            if value < 0 or value > 300:
                raise ValueError("Stage reservation is outside the supported range")
            normalized[stage] = value
        self._stage_reservations = normalized

    @contextmanager
    def stage_budget_scope(self, stage: str):
        if not isinstance(stage, str) or not stage:
            raise ValueError("Stage budget scope requires a stage name")
        stack = getattr(self._stage_budget_state, "stack", None)
        if stack is None:
            stack = []
            self._stage_budget_state.stack = stack
        stack.append(stage)
        try:
            yield
        finally:
            stack.pop()

    @contextmanager
    def retry_scope(
        self,
        *,
        max_retries: int | None = None,
        max_elapsed_seconds: float | None = None,
        policy: str | None = None,
    ):
        stack = getattr(self._policy_state, "stack", None)
        if stack is None:
            stack = []
            self._policy_state.stack = stack
        local_deadline = (
            self._clock() + max(0.0, max_elapsed_seconds)
            if max_elapsed_seconds is not None
            else None
        )
        stack.append((max_retries, local_deadline, policy))
        try:
            yield
        finally:
            stack.pop()

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        attempt = 1
        last_error: FetchError | None = None
        last_event: dict[str, Any] | None = None
        while True:
            remaining_before_fetch = self._remaining_time()
            if remaining_before_fetch <= 0:
                if last_event is not None:
                    last_event["outcome"] = "deadline_exhausted"
                raise self._deadline_error(last_error)
            original_timeout = getattr(self.fetcher, "timeout", None)
            bounded_timeout = (
                min(float(original_timeout), remaining_before_fetch)
                if original_timeout is not None and remaining_before_fetch != float("inf")
                else None
            )
            try:
                if bounded_timeout is not None:
                    self.fetcher.timeout = max(0.001, bounded_timeout)
                try:
                    if interaction is None:
                        page = self.fetcher.fetch(
                            url, data=data, headers=headers
                        )
                    else:
                        page = self.fetcher.fetch(
                            url,
                            data=data,
                            headers=headers,
                            interaction=interaction,
                        )
                finally:
                    if bounded_timeout is not None:
                        self.fetcher.timeout = original_timeout
            except BaseException as raw_error:
                exc = normalize_transport_exception(
                    raw_error,
                    url=url,
                    data=data,
                    headers=headers,
                )
                if exc is None:
                    raise
                inferred_reason_code = (
                    exc.reason_code or classify_fetch_error(str(exc))
                )
                if (
                    inferred_reason_code == "NETWORK_TIMEOUT"
                    and bounded_timeout is not None
                    and original_timeout is not None
                    and bounded_timeout < float(original_timeout)
                    and self._remaining_time() <= 0
                    and self._active_global_deadline_reason() is not None
                ):
                    exc = self._deadline_error(exc)
                reason_code = exc.reason_code or classify_fetch_error(str(exc))
                spec = reason_spec(reason_code)
                retryable = (
                    exc.retryable
                    if exc.retryable is not None
                    else spec.retryable
                )
                event = RetryEvent(
                    url=url,
                    attempt=attempt,
                    reason_code=reason_code,
                    retryable=retryable,
                    error=str(exc),
                    delay=0.0,
                    outcome="failed",
                    transport_phase=exc.transport_phase,
                    policy=self._current_policy()[2],
                )
                self.retry_events.append(asdict(event))
                event_record = self.retry_events[-1]

                if retryable and spec.owner == "budget":
                    event_record["outcome"] = "retry_deferred"
                    if exc is raw_error:
                        raise
                    raise exc from raw_error
                if not retryable:
                    event_record["outcome"] = "not_retryable"
                    if exc is raw_error:
                        raise
                    raise exc from raw_error
                scoped_retries = self._current_policy()[0]
                allowed_retries = (
                    self.max_retries
                    if scoped_retries is None
                    else max(0, scoped_retries)
                )
                if attempt > allowed_retries:
                    event_record["outcome"] = "retry_budget_exhausted"
                    if exc is raw_error:
                        raise
                    raise exc from raw_error

                delay = self._retry_delay(attempt - 1)
                remaining = self._remaining_time()
                if remaining <= delay:
                    event_record["outcome"] = "deadline_exhausted"
                    deadline_error = self._deadline_error(exc)
                    if deadline_error is raw_error:
                        raise
                    raise deadline_error from raw_error

                event_record["delay"] = delay
                event_record["outcome"] = "retry_scheduled"
                if delay:
                    self._sleeper(delay)
                last_error = exc
                last_event = event_record
                attempt += 1
                continue

            if last_error is not None:
                self.retry_events.append(
                    asdict(
                        RetryEvent(
                            url=url,
                            attempt=attempt,
                            reason_code=None,
                            retryable=False,
                            error=None,
                            delay=0.0,
                            outcome="succeeded",
                            policy=self._current_policy()[2],
                        )
                    )
                )
            return page

    def _retry_delay(self, retry_index: int) -> float:
        if not self.base_delay or not self.max_delay:
            return 0.0
        exponent = min(retry_index, 63)
        bounded = min(self.max_delay, self.base_delay * (self.backoff_factor**exponent))
        sample = min(1.0, max(0.0, float(self._rng())))
        jitter = 1.0 + self.jitter_ratio * ((2.0 * sample) - 1.0)
        return min(self.max_delay, max(0.0, bounded * jitter))

    def _remaining_time(self) -> float:
        deadline = self._deadline() if callable(self._deadline) else self._deadline
        local_deadline = self._current_policy()[1]
        reserve = self._current_stage_reserve()
        now = self._clock()
        global_remaining = (
            float("inf") if deadline is None else deadline - now - reserve
        )
        local_remaining = (
            float("inf") if local_deadline is None else local_deadline - now
        )
        remaining = min(global_remaining, local_remaining)
        if remaining == float("inf"):
            return float("inf")
        return remaining

    def _current_stage_reserve(self) -> float:
        stack = getattr(self._stage_budget_state, "stack", None)
        if not stack:
            return 0.0
        return self._stage_reservations.get(stack[-1], 0.0)

    def _active_global_deadline_reason(self) -> str | None:
        deadline = self._deadline() if callable(self._deadline) else self._deadline
        if deadline is None:
            return None
        reserve = self._current_stage_reserve()
        global_deadline = deadline - reserve
        local_deadline = self._current_policy()[1]
        if local_deadline is not None and local_deadline < global_deadline:
            return None
        return (
            "FETCH_BUDGET_EXHAUSTED"
            if reserve > 0
            else "COMPANY_TIME_BUDGET_EXHAUSTED"
        )

    def _deadline_error(self, cause: FetchError | None = None) -> FetchError:
        reason_code = self._active_global_deadline_reason()
        if reason_code is None:
            return cause or FetchError("operation timed out at retry deadline")
        message = (
            "stage fetch budget reserved for downstream candidate discovery"
            if reason_code == "FETCH_BUDGET_EXHAUSTED"
            else "company time budget exhausted at caller deadline"
        )
        return FetchError(
            message,
            reason_code=reason_code,
            retryable=True,
            request_identity=(cause.request_identity if cause is not None else None),
            transport_phase=(cause.transport_phase if cause is not None else None),
        )

    def _current_policy(self) -> tuple[int | None, float | None, str | None]:
        stack = getattr(self._policy_state, "stack", None)
        return stack[-1] if stack else (None, None, None)

    def remaining_fetch_seconds(self) -> float | None:
        remaining = self._remaining_time()
        if remaining == float("inf"):
            return None
        return max(0.0, remaining)

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)
