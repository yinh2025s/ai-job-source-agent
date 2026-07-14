from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from threading import Lock

from .web import Page


class PageCacheFetcher:
    """Reuse a bounded set of successful, uncredentialed GET responses."""

    def __init__(self, fetcher, *, max_entries: int = 64) -> None:
        self.fetcher = fetcher
        self.max_entries = max(0, max_entries)
        self.timeout = getattr(fetcher, "timeout", None)
        self.cache_hits = 0
        self.cache_misses = 0
        self._pages: OrderedDict[str, Page] = OrderedDict()
        self._lock = Lock()

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Page:
        cacheable = self.max_entries > 0 and data is None and not headers
        if cacheable:
            with self._lock:
                cached = self._pages.get(url)
                if cached is not None:
                    self._pages.move_to_end(url)
                    self.cache_hits += 1
                    return _copy_page(cached)
                self.cache_misses += 1

        page = self.fetcher.fetch(url, data=data, headers=headers)
        if cacheable:
            with self._lock:
                self._pages[url] = _copy_page(page)
                self._pages.move_to_end(url)
                while len(self._pages) > self.max_entries:
                    self._pages.popitem(last=False)
        return page

    def remaining_fetch_seconds(self) -> float | None:
        remaining = getattr(self.fetcher, "remaining_fetch_seconds", None)
        return remaining() if callable(remaining) else None

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)


def _copy_page(page: Page) -> Page:
    return replace(page, artifacts=dict(page.artifacts))
