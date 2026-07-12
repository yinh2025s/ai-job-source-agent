from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .base import AdapterResult, JobBoard, JobQuery


@dataclass(frozen=True)
class DomainProviderAdapter:
    """Detection-only adapter used while a provider is still on the legacy parser path."""

    name: str
    host_markers: tuple[str, ...]
    supports_listing: bool = False

    def recognizes(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(marker in host for marker in self.host_markers)

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        return JobBoard(url=url, provider=self.name)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        return AdapterResult(
            provider=self.name,
            board=board,
            reason_code="PROVIDER_VARIANT_UNSUPPORTED",
            trace={"adapter": self.name, "mode": "legacy_compatibility"},
        )
