from __future__ import annotations

from typing import Any

from .provider_candidates import (
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    ProviderCandidate,
    ProviderCandidatePool,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry


class ExternalApplyDiscovery:
    """Turn LinkedIn's declared external apply target into an untrusted lead."""

    def __init__(self, provider_registry: ProviderRegistry | None = None) -> None:
        self._provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        candidate = _direct_candidate(
            url=request.external_apply_url,
            source_kind="external_apply",
            company_name=request.company_name,
            target_title=request.target_title,
            target_location=request.target_location,
            registry=self._provider_registry,
        )
        return _result("external_apply", [candidate] if candidate else [])


class WebsiteCareerDiscovery:
    """Use declared first-party URLs only when their URL identifies an ATS."""

    def __init__(self, provider_registry: ProviderRegistry | None = None) -> None:
        self._provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        candidates: list[ProviderCandidate] = []
        for source_url in (request.career_page_url, request.company_website_url):
            candidate = _direct_candidate(
                url=source_url,
                source_kind="first_party_ats_link",
                company_name=request.company_name,
                target_title=request.target_title,
                target_location=request.target_location,
                registry=self._provider_registry,
                require_provider_hint=True,
            )
            if candidate is not None:
                candidates.append(candidate)
        return _result("website_career", candidates)


def _direct_candidate(
    *,
    url: str | None,
    source_kind: str,
    company_name: str,
    target_title: str | None,
    target_location: str | None,
    registry: ProviderRegistry | None = None,
    require_provider_hint: bool = False,
) -> ProviderCandidate | None:
    if not url:
        return None
    try:
        candidate = ProviderCandidate(
            url=url,
            source_kind=source_kind,
            source_url=url,
            company_name=company_name,
            target_title=target_title,
            target_location=target_location,
        )
    except (TypeError, ValueError):
        return None

    provider_hint = _provider_hint(registry or DEFAULT_PROVIDER_REGISTRY, candidate.url)
    if require_provider_hint and provider_hint is None:
        return None
    return ProviderCandidate(
        url=candidate.url,
        source_kind=candidate.source_kind,
        source_url=candidate.source_url,
        company_name=candidate.company_name,
        target_title=candidate.target_title,
        target_location=candidate.target_location,
        provider_hint=provider_hint,
    )


def _provider_hint(registry: ProviderRegistry, url: str) -> str | None:
    try:
        provider = registry.detect(url)
    except (TypeError, ValueError):
        return None
    return provider if provider != "generic" else None


def _result(
    source: str,
    candidates: list[ProviderCandidate],
) -> CandidateDiscoveryResult:
    pool = ProviderCandidatePool.build(candidates)
    trace: dict[str, Any] = {
        "source": source,
        "candidate_count": len(pool.candidates),
        "candidates": [candidate.to_trace_payload() for candidate in pool.candidates],
    }
    return CandidateDiscoveryResult(candidates=pool.candidates, trace=trace)
