from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Protocol, runtime_checkable

from ..contracts import FetchBudget, FetchClient
from ..job_board import JobBoard
from ..web import FetchError, Page


@dataclass(frozen=True)
class JobQuery:
    title: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class JobCandidate:
    title: str
    url: str
    provider: str
    location: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResult:
    provider: str
    board: JobBoard
    candidates: list[JobCandidate] = field(default_factory=list)
    reason_code: str | None = None
    retryable: bool = False
    inventory_scope: str = "full"
    inventory_complete: bool = True
    trace: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str
    supports_listing: bool

    def recognizes(self, url: str) -> bool:
        ...

    def identify_board(self, url: str) -> JobBoard | None:
        ...

    def list_jobs(
        self,
        fetcher: FetchClient,
        board: JobBoard,
        query: JobQuery,
    ) -> AdapterResult:
        ...


@runtime_checkable
class PageAwareProviderAdapter(Protocol):
    """Optional extension for providers hidden behind customer-owned domains."""

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        ...


@runtime_checkable
class PageProbeProviderAdapter(Protocol):
    """Optional extension for provider evidence stored in a linked public payload."""

    def probe_board(self, fetcher: FetchClient, page: Page) -> JobBoard | None:
        ...


def pagination_fetch_reserve_seconds(
    fetcher: FetchClient,
    *,
    publication_reserve_seconds: float = 1.0,
) -> float:
    try:
        publication_reserve = float(publication_reserve_seconds)
    except (TypeError, ValueError):
        return float("inf")
    if not math.isfinite(publication_reserve):
        return float("inf")
    publication_reserve = max(0.0, publication_reserve)
    request_timeout = getattr(fetcher, "timeout", None)
    if (
        isinstance(request_timeout, bool)
        or not isinstance(request_timeout, (int, float))
        or not math.isfinite(request_timeout)
    ):
        return float("inf")
    return publication_reserve + max(0.0, float(request_timeout))


def has_fetch_reserve(fetcher: FetchClient, reserve_seconds: float) -> bool:
    if not isinstance(fetcher, FetchBudget):
        return True
    remaining = fetcher.remaining_fetch_seconds()
    if remaining is None:
        return True
    try:
        reserve = float(reserve_seconds)
    except (TypeError, ValueError):
        return False
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(remaining)
        or not math.isfinite(reserve)
    ):
        return False
    return max(0.0, float(remaining)) > max(0.0, reserve)


def require_fetch_reserve(
    fetcher: FetchClient,
    reserve_seconds: float,
    *,
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    if has_fetch_reserve(fetcher, reserve_seconds):
        return
    error = FetchError(
        "fetch skipped because the cooperative reserve was exhausted",
        reason_code="FETCH_BUDGET_EXHAUSTED",
        retryable=True,
    )
    recorder = getattr(fetcher, "record_fetch_failure", None)
    if callable(recorder):
        recorder(error, url, data=data, headers=headers)
    raise error
