from __future__ import annotations

import re
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

from .career_search import CareerSearchResolver
from .provider_candidates import (
    MAX_PROVIDER_CANDIDATES,
    CandidateDiscovery,
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    ProviderCandidate,
    ProviderCandidatePool,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from .providers.base import JobBoard, JobQuery
from .scoring import is_likely_job_detail
from .web import FetchError


class ProviderSearchCandidateDiscovery(CandidateDiscovery):
    """Turn ATS-only search links into untrusted provider candidate leads."""

    candidate_wave = "search"

    def __init__(
        self,
        resolver: CareerSearchResolver,
        *,
        provider_registry: ProviderRegistry = DEFAULT_PROVIDER_REGISTRY,
        max_candidates: int = MAX_PROVIDER_CANDIDATES,
        max_probe_attempts: int = 6,
    ) -> None:
        if (
            isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int)
            or not 1 <= max_candidates <= MAX_PROVIDER_CANDIDATES
        ):
            raise ValueError("Provider search candidate limit is invalid")
        self.resolver = resolver
        self.provider_registry = provider_registry
        self.max_candidates = max_candidates
        if isinstance(max_probe_attempts, bool) or not 1 <= max_probe_attempts <= 12:
            raise ValueError("Provider tenant probe limit is invalid")
        self.max_probe_attempts = max_probe_attempts

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        search_result = self.resolver.search(
            request.company_name,
            request.company_website_url or "",
            # The search route has a bounded query budget, so spend it on the
            # requested role. Results remain untrusted leads and still pass
            # provider, tenant, inventory, and S7 identity validation.
            target_title=request.target_title,
            ats_only=True,
            exhaustive=False,
            query_diversity_first=True,
        )
        query_by_url = _query_by_url(search_result.trace)
        candidates: list[ProviderCandidate] = []
        skipped_count = 0

        for result_rank, link in enumerate(search_result.candidates, start=1):
            query = query_by_url.get(link.url)
            if query is None:
                skipped_count += 1
                continue
            provider_hint = self.provider_registry.detect(link.url)
            try:
                candidates.append(
                    ProviderCandidate(
                        url=link.url,
                        source_kind=(
                            "targeted_opening_search"
                            if is_likely_job_detail(link)
                            else "targeted_board_search"
                        ),
                        source_url=link.source_url,
                        company_name=request.company_name,
                        target_title=request.target_title,
                        target_location=request.target_location,
                        provider_hint=(
                            None if provider_hint == "generic" else provider_hint
                        ),
                        query=query,
                        result_rank=result_rank,
                    )
                )
                break
            except (TypeError, ValueError):
                # Search results are leads only; malformed or non-public URLs
                # cannot enter the candidate contract.
                skipped_count += 1

        probe_candidates: list[ProviderCandidate] = []
        probe_trace: TenantProbeTrace = {
            "status": "skipped",
            "candidate_count": 0,
            "reason": (
                "search_candidate_available"
                if candidates
                else "probe_source_unavailable"
            ),
            "attempts": [],
        }
        if not candidates and (
            request.company_website_url or request.linkedin_company_url
        ):
            probe_candidates, probe_trace = _verified_provider_tenant_probes(
                request,
                self.resolver.fetcher,
                self.provider_registry,
                max_attempts=self.max_probe_attempts,
            )
            candidates.extend(probe_candidates)

        pool = ProviderCandidatePool.build(candidates, limit=self.max_candidates)
        return CandidateDiscoveryResult(
            candidates=pool.candidates,
            trace={
                "source": "provider_targeted_search",
                "search": search_result.trace,
                "candidate_count": len(pool.candidates),
                "truncated": pool.truncated,
                "skipped_candidate_count": skipped_count,
                "tenant_probe_fallback": probe_trace,
            },
        )


def _query_by_url(trace: dict[str, Any]) -> dict[str, str]:
    queries = trace.get("queries")
    if not isinstance(queries, list):
        return {}
    values: dict[str, str] = {}
    for query_trace in queries:
        if not isinstance(query_trace, dict):
            continue
        query = query_trace.get("query")
        candidates = query_trace.get("candidates")
        if not isinstance(query, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                values.setdefault(candidate["url"], query)
    return values


def _verified_provider_tenant_probes(
    request: CandidateDiscoveryRequest,
    fetcher,
    registry: ProviderRegistry,
    *,
    max_attempts: int,
) -> tuple[list[ProviderCandidate], TenantProbeTrace]:
    website_url = request.company_website_url or ""
    source_url = website_url or request.linkedin_company_url or ""
    slugs = _provider_slug_candidates(
        request.company_name,
        website_url,
        request.linkedin_company_url,
    )
    attempts: list[TenantProbeAttempt] = []
    for slug in slugs:
        provider_urls = (
            ("greenhouse", f"https://boards.greenhouse.io/{slug}"),
            ("greenhouse", f"https://job-boards.greenhouse.io/{slug}"),
            ("ashby", f"https://jobs.ashbyhq.com/{slug}"),
            ("lever", f"https://jobs.lever.co/{slug}"),
            ("pinpoint", f"https://{slug}.pinpointhq.com"),
            ("smartrecruiters", f"https://jobs.smartrecruiters.com/{slug}"),
        )
        for provider, url in provider_urls:
            if len(attempts) >= max_attempts:
                return [], {
                    "status": "rejected",
                    "candidate_count": 0,
                    "reason": "provider_tenant_probe_limit_reached",
                    "attempts": attempts,
                }
            adapter = registry.adapter_for(url)
            board = adapter.identify_board(url) if adapter is not None else None
            attempt: TenantProbeAttempt = {
                "url": url,
                "provider": provider,
                "status": "rejected",
            }
            if adapter is None or board is None or not adapter.supports_listing:
                attempt["reason"] = "provider_not_listable"
                attempts.append(attempt)
                continue
            try:
                result = adapter.list_jobs(
                    fetcher,
                    board,
                    JobQuery(
                        title=request.target_title,
                        location=request.target_location,
                    ),
                )
            except (FetchError, OSError, TimeoutError, TypeError, ValueError) as exc:
                attempt["reason"] = "provider_probe_failed"
                attempt["error_type"] = type(exc).__name__
                attempts.append(attempt)
                continue
            if result.retryable:
                attempt["reason"] = "provider_inventory_retryable"
                attempts.append(attempt)
                continue
            if not result.inventory_complete or result.inventory_scope != "full":
                attempt["reason"] = "provider_inventory_incomplete"
                attempts.append(attempt)
                continue
            if not _same_provider_tenant(board, result.board, result.provider):
                attempt["reason"] = "provider_tenant_mismatch"
                attempts.append(attempt)
                continue
            if not result.candidates:
                attempt["reason"] = "provider_inventory_empty"
                attempts.append(attempt)
                continue
            attempt["status"] = "verified"
            attempt["reason"] = "provider_inventory_verified"
            attempt["candidate_count"] = len(result.candidates)
            attempts.append(attempt)
            candidate = ProviderCandidate(
                url=result.board.url,
                source_kind="verified_tenant_probe",
                source_url=source_url,
                company_name=request.company_name,
                target_title=request.target_title,
                target_location=request.target_location,
                provider_hint=result.provider,
            )
            return [candidate], {
                "status": "used",
                "candidate_count": 1,
                "reason": "verified_provider_tenant_probe",
                "attempts": attempts,
            }
    return [], {
        "status": "rejected",
        "candidate_count": 0,
        "reason": "no_provider_tenant_probe_verified",
        "attempts": attempts,
    }


class TenantProbeAttempt(TypedDict, total=False):
    url: str
    provider: str
    status: Literal["rejected", "verified"]
    reason: str
    error_type: str
    candidate_count: int


class TenantProbeTrace(TypedDict):
    status: Literal["skipped", "used", "rejected"]
    candidate_count: int
    reason: str
    attempts: list[TenantProbeAttempt]


def _same_provider_tenant(
    board: JobBoard,
    result_board: JobBoard,
    result_provider: str,
) -> bool:
    return (
        result_provider == board.provider
        and result_board.provider == board.provider
        and result_board.identifier == board.identifier
    )


def _provider_slug_candidates(
    company_name: str,
    website_url: str,
    linkedin_company_url: str | None = None,
) -> tuple[str, ...]:
    ignored = {"co", "company", "corp", "corporation", "group", "inc", "llc", "ltd", "the"}
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", company_name.casefold())
        if token not in ignored
    ]
    hostname = (urlparse(website_url).hostname or "").casefold().removeprefix("www.")
    host_label = hostname.split(".", 1)[0]
    linkedin_path = (urlparse(linkedin_company_url or "").path or "").strip("/")
    linkedin_slug = ""
    if linkedin_path.casefold().startswith("company/"):
        linkedin_slug = linkedin_path.split("/", 1)[1].split("/", 1)[0].casefold()
    linkedin_tokens = re.findall(r"[a-z0-9]+", linkedin_slug)
    linkedin_variants = (
        linkedin_slug,
        "".join(linkedin_tokens),
        "-".join(linkedin_tokens),
    )
    values = (
        *linkedin_variants,
        host_label,
        "".join(tokens),
        "-".join(tokens),
    )
    return tuple(dict.fromkeys(value for value in values if value))[:5]
