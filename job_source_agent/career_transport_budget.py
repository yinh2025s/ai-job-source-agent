from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterator
from urllib.parse import urlparse

from .browser_interaction import BrowserInteraction
from .contracts import FetchClient
from .reasons import classify_fetch_error
from .request_identity import build_request_identity
from .web import FetchError, Page


_HOST_DENIAL_LIMIT = 2


@dataclass
class _HostDenialState:
    count: int = 0
    statuses: set[int] = field(default_factory=set)
    reason_codes: set[str] = field(default_factory=set)
    last_status: int = 403
    last_reason_code: str = "HTTP_FORBIDDEN"


@dataclass
class _ScopeState:
    limit: int | None
    dispatched: int = 0
    rejected: int = 0
    by_phase: dict[str, int] = field(default_factory=dict)
    host_denials: dict[str, _HostDenialState] = field(default_factory=dict)
    host_circuit_rejected: int = 0


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
            snapshot = {
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
            open_circuits = {
                host: denial
                for host, denial in self._state.host_denials.items()
                if denial.count >= _HOST_DENIAL_LIMIT
            }
            if open_circuits:
                snapshot["host_circuit"] = {
                    "denial_limit": _HOST_DENIAL_LIMIT,
                    "opened": len(open_circuits),
                    "rejected": self._state.host_circuit_rejected,
                    "hosts": {
                        host: {
                            "denials": denial.count,
                            "statuses": sorted(denial.statuses),
                            "reason_codes": sorted(denial.reason_codes),
                        }
                        for host, denial in sorted(open_circuits.items())
                    },
                }
            return snapshot


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
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        host_key = _host_key(url)
        with self._lock:
            state = self._active_scope
            if state is not None:
                denial = state.host_denials.get(host_key) if host_key else None
                if denial is not None and denial.count >= _HOST_DENIAL_LIMIT:
                    state.rejected += 1
                    state.host_circuit_rejected += 1
                    raise FetchError(
                        "career discovery host circuit is open after repeated access denials",
                        status=denial.last_status,
                        reason_code=denial.last_reason_code,
                        retryable=False,
                        request_identity=build_request_identity(
                            url,
                            data=data,
                            headers=headers,
                            interaction=interaction,
                        ).as_dict(),
                    )
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

        try:
            if interaction is None:
                page = self.fetcher.fetch(url, data=data, headers=headers)
            else:
                page = self.fetcher.fetch(
                    url,
                    data=data,
                    headers=headers,
                    interaction=interaction,
                )
        except FetchError as exc:
            denial = _deterministic_denial(exc)
            if state is not None and host_key and denial is not None:
                status, reason_code = denial
                with self._lock:
                    host_denial = state.host_denials.setdefault(
                        host_key,
                        _HostDenialState(),
                    )
                    host_denial.count += 1
                    host_denial.statuses.add(status)
                    host_denial.reason_codes.add(reason_code)
                    host_denial.last_status = status
                    host_denial.last_reason_code = reason_code
            raise
        if state is not None and host_key:
            with self._lock:
                state.host_denials.pop(host_key, None)
        return page

    def remaining_fetch_seconds(self) -> float | None:
        remaining = getattr(self.fetcher, "remaining_fetch_seconds", None)
        return remaining() if callable(remaining) else None

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)


def _host_key(url: str) -> str:
    try:
        hostname = (urlparse(url).hostname or "").casefold()
    except (TypeError, ValueError):
        return ""
    return hostname.removeprefix("www.")


def _deterministic_denial(error: FetchError) -> tuple[int, str] | None:
    if error.status in {401, 403}:
        status = error.status
    else:
        reason_code = error.reason_code or classify_fetch_error(str(error))
        status = {"LOGIN_REQUIRED": 401, "HTTP_FORBIDDEN": 403}.get(reason_code)
        if status is None:
            return None
    reason_code = "LOGIN_REQUIRED" if status == 401 else "HTTP_FORBIDDEN"
    return status, reason_code
