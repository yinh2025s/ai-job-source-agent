from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..contracts import FetchClient


@dataclass(frozen=True)
class JobBoard:
    url: str
    provider: str
    identifier: str | None = None


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
