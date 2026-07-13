from __future__ import annotations

from collections.abc import Iterable
import importlib
import pkgutil

from ..web import Page
from .base import JobBoard, PageAwareProviderAdapter, PageProbeProviderAdapter, ProviderAdapter
from .domain import DomainProviderAdapter


class ProviderRegistry:
    def __init__(self, adapters: Iterable[ProviderAdapter] = ()) -> None:
        self._adapters: list[ProviderAdapter] = []
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        if any(existing.name == adapter.name for existing in self._adapters):
            raise ValueError(f"Provider adapter already registered: {adapter.name}")
        self._adapters.append(adapter)

    def adapter_for(self, url: str) -> ProviderAdapter | None:
        return next((adapter for adapter in self._adapters if adapter.recognizes(url)), None)

    def adapter_named(self, provider: str) -> ProviderAdapter | None:
        return next((adapter for adapter in self._adapters if adapter.name == provider), None)

    def detect(self, url: str) -> str:
        adapter = self.adapter_for(url)
        return adapter.name if adapter else "generic"

    def board_for_page(self, page: Page, fetcher=None) -> tuple[ProviderAdapter, JobBoard] | None:
        """Identify a provider from fetched page evidence when its URL is opaque."""
        for adapter in self._adapters:
            if not isinstance(adapter, PageAwareProviderAdapter):
                continue
            board = adapter.identify_board_from_page(page)
            if board is not None:
                return adapter, board
        if fetcher is not None:
            for adapter in self._adapters:
                if not isinstance(adapter, PageProbeProviderAdapter):
                    continue
                board = adapter.probe_board(fetcher, page)
                if board is not None:
                    return adapter, board
        return None

    @property
    def adapters(self) -> tuple[ProviderAdapter, ...]:
        return tuple(self._adapters)


def build_default_provider_registry() -> ProviderRegistry:
    native_adapters = discover_native_adapters()
    native_names = {adapter.name for adapter in native_adapters}
    compatibility_adapters = [
        adapter
        for adapter in _domain_compatibility_adapters()
        if adapter.name not in native_names
    ]
    return ProviderRegistry((*native_adapters, *compatibility_adapters))


def discover_native_adapters() -> tuple[ProviderAdapter, ...]:
    package = importlib.import_module(__package__)
    adapters = []
    excluded_modules = {"base", "domain", "registry"}
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in excluded_modules or module_info.ispkg:
            continue
        module = importlib.import_module(f"{__package__}.{module_info.name}")
        adapter = getattr(module, "ADAPTER", None)
        if adapter is not None:
            adapters.append(adapter)
    return tuple(sorted(adapters, key=lambda adapter: adapter.name))


def _domain_compatibility_adapters() -> tuple[DomainProviderAdapter, ...]:
    return (
        DomainProviderAdapter("google_careers", ("google.com",)),
        DomainProviderAdapter("meta_careers", ("metacareers.com",)),
        DomainProviderAdapter("lever", ("lever.co",)),
        DomainProviderAdapter("ashby", ("ashbyhq.com",)),
        DomainProviderAdapter("workable", ("workable.com",)),
        DomainProviderAdapter("smartrecruiters", ("smartrecruiters.com",)),
        DomainProviderAdapter("icims", ("icims.com",)),
        DomainProviderAdapter("workday", ("workdayjobs.com", "myworkdayjobs.com")),
        DomainProviderAdapter("successfactors", ("successfactors.com", "sapsf.com")),
        DomainProviderAdapter("bamboohr", ("bamboohr.com",)),
        DomainProviderAdapter("rippling", ("rippling.com",)),
    )


DEFAULT_PROVIDER_REGISTRY = build_default_provider_registry()
