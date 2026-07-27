from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .web import Fetcher


SearchDisposition = Literal["ok", "challenge", "invalid_response"]


@dataclass(frozen=True)
class SearchQuery:
    text: str


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True, repr=False)
class SearchBackendResponse:
    request_url: str
    final_url: str
    hits: tuple[SearchHit, ...]
    disposition: SearchDisposition = "ok"
    reason: str | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"request_url={self.request_url!r}, "
            f"final_url={self.final_url!r}, "
            f"hit_count={len(self.hits)!r}, "
            f"disposition={self.disposition!r}, "
            f"reason={self.reason!r})"
        )


class SearchBackend(Protocol):
    name: str

    def search(
        self,
        query: SearchQuery,
        *,
        fetcher: Fetcher,
    ) -> SearchBackendResponse:
        """Dispatch at most one fetch and return untrusted search hits."""
        ...

    def public_configuration(self) -> dict[str, str]:
        """Return deterministic configuration metadata without endpoint secrets."""
        ...
