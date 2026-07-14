from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from threading import Lock
from urllib.parse import urlsplit

from .web import Page


class PageCacheFetcher:
    """Reuse a bounded set of successful, uncredentialed GET responses."""

    def __init__(self, fetcher, *, max_entries: int = 64) -> None:
        self.fetcher = fetcher
        self.max_entries = max(0, max_entries)
        self.timeout = getattr(fetcher, "timeout", None)
        self.cache_hits = 0
        self.cache_misses = 0
        self._pages: OrderedDict[int, Page] = OrderedDict()
        self._aliases: dict[str, int] = {}
        self._entry_aliases: dict[int, set[str]] = {}
        self._next_entry_id = 0
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
                entry_id = self._aliases.get(_canonical_cache_alias(url))
                cached = self._pages.get(entry_id) if entry_id is not None else None
                if cached is not None and entry_id is not None:
                    self._pages.move_to_end(entry_id)
                    self.cache_hits += 1
                    return _copy_page(cached)
                self.cache_misses += 1

        page = self.fetcher.fetch(url, data=data, headers=headers)
        if cacheable:
            with self._lock:
                self._next_entry_id += 1
                entry_id = self._next_entry_id
                aliases = {
                    _canonical_cache_alias(alias)
                    for alias in (url, page.url, page.final_url)
                    if isinstance(alias, str) and alias
                }
                self._pages[entry_id] = _copy_page(page)
                self._entry_aliases[entry_id] = aliases
                for alias in aliases:
                    previous_entry_id = self._aliases.get(alias)
                    if previous_entry_id is not None:
                        self._entry_aliases.get(previous_entry_id, set()).discard(alias)
                    self._aliases[alias] = entry_id
                while len(self._pages) > self.max_entries:
                    evicted_entry_id, _ = self._pages.popitem(last=False)
                    for alias in self._entry_aliases.pop(evicted_entry_id, set()):
                        if self._aliases.get(alias) == evicted_entry_id:
                            self._aliases.pop(alias, None)
        return page

    def remaining_fetch_seconds(self) -> float | None:
        remaining = getattr(self.fetcher, "remaining_fetch_seconds", None)
        return remaining() if callable(remaining) else None

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)


def _copy_page(page: Page) -> Page:
    return replace(page, artifacts=dict(page.artifacts))


def _canonical_cache_alias(url: str) -> str:
    """Treat the two spellings of an HTTP(S) origin root as one cache key."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or parsed.path:
        return url

    authority_end = url.find("://") + 3 + len(parsed.netloc)
    return f"{url[:authority_end]}/{url[authority_end:]}"
