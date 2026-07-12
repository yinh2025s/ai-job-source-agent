from __future__ import annotations

from collections.abc import Iterable

from .base import ProviderAdapter
from .domain import DomainProviderAdapter
from .greenhouse import GreenhouseAdapter


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

    def detect(self, url: str) -> str:
        adapter = self.adapter_for(url)
        return adapter.name if adapter else "generic"

    @property
    def adapters(self) -> tuple[ProviderAdapter, ...]:
        return tuple(self._adapters)


def build_default_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            GreenhouseAdapter(),
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
    )


DEFAULT_PROVIDER_REGISTRY = build_default_provider_registry()

