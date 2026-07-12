from .base import (
    AdapterResult,
    JobBoard,
    JobCandidate,
    JobQuery,
    PageAwareProviderAdapter,
    ProviderAdapter,
)
from .greenhouse import GreenhouseAdapter
from .registry import (
    DEFAULT_PROVIDER_REGISTRY,
    ProviderRegistry,
    build_default_provider_registry,
    discover_native_adapters,
)

__all__ = [
    "AdapterResult",
    "JobBoard",
    "JobCandidate",
    "JobQuery",
    "PageAwareProviderAdapter",
    "ProviderAdapter",
    "GreenhouseAdapter",
    "ProviderRegistry",
    "DEFAULT_PROVIDER_REGISTRY",
    "build_default_provider_registry",
    "discover_native_adapters",
]
