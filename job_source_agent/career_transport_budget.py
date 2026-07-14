from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterator

from .contracts import FetchClient
from .web import FetchError, Page


@dataclass
class _ScopeState:
    limit: int | None
    dispatched: int = 0
    rejected: int = 0
    by_phase: dict[str, int] = field(default_factory=dict)


_PHASE_STACK: ContextVar[tuple[tuple[_ScopeState, str], ...]] = ContextVar(
    "career_transport_budget_phase_stack",
    default=(),
)


class CareerDiscoveryBudget:
    """Stable handle for reading one career-discovery scope's aggregate trace."""

    def __init__(self, state: _ScopeState, lock: RLock) -> None:
        self._state = state
        self._lock = lock

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            limit = self._state.limit
            dispatched = self._state.dispatched
            remaining = None if limit is None else max(0, limit - dispatched)
            return {
                "limit": limit,
                "dispatched": dispatched,
                "remaining": remaining,
                "exhausted": limit is not None and remaining == 0,
                "rejected": self._state.rejected,
                "by_phase": {
                    name: self._state.by_phase[name]
                    for name in sorted(self._state.by_phase)
                },
            }


class CareerTransportBudgetFetcher:
    """Count delegate dispatches made during a bounded career-discovery scope."""

    def __init__(self, fetcher: FetchClient) -> None:
        self.fetcher = fetcher
        self._lock = RLock()
        self._active_scope: _ScopeState | None = None

    @property
    def timeout(self):
        return getattr(self.fetcher, "timeout", None)

    @timeout.setter
    def timeout(self, value) -> None:
        setattr(self.fetcher, "timeout", value)

    @contextmanager
    def career_discovery_scope(
        self,
        limit: int | None,
    ) -> Iterator[CareerDiscoveryBudget]:
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            raise TypeError("career discovery fetch limit must be a nonnegative integer or None")
        if limit is not None and limit < 0:
            raise ValueError("career discovery fetch limit must be nonnegative")

        state = _ScopeState(limit=limit)
        with self._lock:
            if self._active_scope is not None:
                raise RuntimeError("career discovery fetch scopes cannot be nested")
            self._active_scope = state

        try:
            yield CareerDiscoveryBudget(state, self._lock)
        finally:
            with self._lock:
                if self._active_scope is state:
                    self._active_scope = None

    @contextmanager
    def career_discovery_phase(self, name: str) -> Iterator[None]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("career discovery phase name must be a nonempty string")
        with self._lock:
            state = self._active_scope
            if state is None:
                raise RuntimeError("career discovery phases require an active scope")

        token = _PHASE_STACK.set((*_PHASE_STACK.get(), (state, name)))
        try:
            yield
        finally:
            _PHASE_STACK.reset(token)

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Page:
        with self._lock:
            state = self._active_scope
            if state is not None:
                if state.limit is not None and state.dispatched >= state.limit:
                    state.rejected += 1
                    raise FetchError(
                        "career discovery fetch budget exhausted",
                        reason_code="FETCH_BUDGET_EXHAUSTED",
                        retryable=True,
                    )
                state.dispatched += 1
                phase = next(
                    (name for scope, name in reversed(_PHASE_STACK.get()) if scope is state),
                    None,
                )
                if phase is not None:
                    state.by_phase[phase] = state.by_phase.get(phase, 0) + 1

        return self.fetcher.fetch(url, data=data, headers=headers)

    def remaining_fetch_seconds(self) -> float | None:
        remaining = getattr(self.fetcher, "remaining_fetch_seconds", None)
        return remaining() if callable(remaining) else None

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)
