from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .reasons import classify_fetch_error, reason_spec
from .web import FetchError, Page


@dataclass
class RetryEvent:
    url: str
    attempt: int
    reason_code: str
    retryable: bool
    error: str


class RetryingFetcher:
    """Retry transient fetch failures without retrying parser or matcher errors."""

    def __init__(
        self,
        fetcher,
        max_retries: int = 1,
        base_delay: float = 0.25,
        backoff_factor: float = 2.0,
    ) -> None:
        self.fetcher = fetcher
        self.max_retries = max(0, max_retries)
        self.base_delay = max(0.0, base_delay)
        self.backoff_factor = max(1.0, backoff_factor)
        self.timeout = getattr(fetcher, "timeout", None)
        self.retry_events: list[dict[str, Any]] = []

    def fetch(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        attempt = 0
        while True:
            try:
                return self.fetcher.fetch(url, data=data, headers=headers)
            except FetchError as exc:
                reason_code = classify_fetch_error(str(exc))
                spec = reason_spec(reason_code)
                event = RetryEvent(
                    url=url,
                    attempt=attempt + 1,
                    reason_code=reason_code,
                    retryable=spec.retryable,
                    error=str(exc),
                )
                self.retry_events.append(event.__dict__)
                if not spec.retryable or attempt >= self.max_retries:
                    raise
                if self.base_delay:
                    time.sleep(self.base_delay * (self.backoff_factor ** attempt))
                attempt += 1

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)
