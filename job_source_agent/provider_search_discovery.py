from __future__ import annotations

from dataclasses import dataclass
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
    ProviderPublishedEmployerEvidence,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from .providers.base import JobBoard, JobQuery
from .opening_selection_validation import classify_location, _target_state_code
from .result_identity import canonicalize_identity_url
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
        max_probe_attempts: int = 8,
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
        if isinstance(max_probe_attempts, bool) or not 1 <= max_probe_attempts <= 16:
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
    slugs = _provider_probe_slug_candidates(
        request.company_name,
        website_url,
        request.linkedin_company_url,
    )
    attempts: list[TenantProbeAttempt] = []
    primary_provider_urls = (
        ("greenhouse", "https://boards.greenhouse.io/{slug}"),
        ("ashby", "https://jobs.ashbyhq.com/{slug}"),
        ("lever", "https://jobs.lever.co/{slug}"),
        ("workable", "https://apply.workable.com/{slug}"),
    )
    secondary_provider_urls = (
        ("greenhouse", "https://job-boards.greenhouse.io/{slug}"),
        ("pinpoint", "https://{slug}.pinpointhq.com"),
        ("smartrecruiters", "https://jobs.smartrecruiters.com/{slug}"),
    )
    case_preserved_lever = _case_preserved_lever_tenant(request.company_name, slugs)
    provider_urls: list[tuple[str, str, ProviderProbeSlug]] = []
    for slug in slugs:
        for provider, template in primary_provider_urls:
            if (
                provider == "lever"
                and case_preserved_lever is not None
                and slug.value.casefold() == case_preserved_lever.casefold()
            ):
                preserved_slug = ProviderProbeSlug(
                    value=case_preserved_lever,
                    kind="case_preserved",
                    stripped_tokens=(),
                )
                provider_urls.append(
                    (
                        provider,
                        template.format(slug=case_preserved_lever),
                        preserved_slug,
                    )
                )
            provider_urls.append(
                (provider, template.format(slug=slug.value), slug)
            )
    provider_urls.extend(
        (provider, template.format(slug=slug.value), slug)
        for slug in slugs
        for provider, template in secondary_provider_urls
    )
    for provider, url, slug in provider_urls:
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
            "slug": slug.value,
            "probe_kind": slug.kind,
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
        if not _same_provider_tenant(board, result.board, result.provider):
            attempt["reason"] = "provider_tenant_mismatch"
            attempts.append(attempt)
            continue
        if slug.kind == "acronym_suffix_stripped":
            opening, evidence, rejection_reason = _verified_acronym_suffix_opening(
                request,
                adapter,
                result,
                slug,
            )
            if opening is None or evidence is None:
                attempt["reason"] = rejection_reason
                attempts.append(attempt)
                continue
            attempt["status"] = "verified"
            attempt["reason"] = "provider_published_employer_verified"
            attempt["candidate_count"] = len(result.candidates)
            attempt["opening_url"] = opening.url
            attempts.append(attempt)
            candidate = ProviderCandidate(
                url=result.board.url,
                source_kind="verified_tenant_probe",
                source_url=source_url,
                company_name=request.company_name,
                target_title=request.target_title,
                target_location=request.target_location,
                provider_hint=result.provider,
                provider_employer_evidence=evidence,
            )
            return [candidate], {
                "status": "used",
                "candidate_count": 1,
                "reason": "verified_provider_tenant_probe",
                "attempts": attempts,
            }
        full_inventory_verified = bool(
            result.inventory_complete and result.inventory_scope == "full"
        )
        target_opening_verified = _probe_contains_exact_title(
            result.candidates,
            request.target_title,
        )
        if not full_inventory_verified and not target_opening_verified:
            attempt["reason"] = "provider_inventory_incomplete"
            attempts.append(attempt)
            continue
        if not result.candidates:
            attempt["reason"] = "provider_inventory_empty"
            attempts.append(attempt)
            continue
        attempt["status"] = "verified"
        attempt["reason"] = (
            "provider_inventory_verified"
            if full_inventory_verified
            else "provider_target_opening_verified"
        )
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
    opening_url: str
    slug: str
    probe_kind: Literal["ordinary", "case_preserved", "acronym_suffix_stripped"]


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


def _probe_contains_exact_title(
    candidates: list[JobCandidate],
    target_title: str | None,
) -> bool:
    normalized_target = " ".join(
        re.findall(r"[a-z0-9]+", (target_title or "").casefold())
    )
    return bool(
        normalized_target
        and any(
            " ".join(re.findall(r"[a-z0-9]+", candidate.title.casefold()))
            == normalized_target
            for candidate in candidates
        )
    )


def _verified_acronym_suffix_opening(
    request: CandidateDiscoveryRequest,
    adapter,
    result,
    slug: "ProviderProbeSlug",
) -> tuple[
    JobCandidate | None,
    ProviderPublishedEmployerEvidence | None,
    str,
]:
    """Require opening-scoped provider evidence for a stripped acronym slug."""

    if not request.target_title:
        return None, None, "provider_target_title_required"
    matching_titles = [
        candidate
        for candidate in result.candidates
        if _normalized_text(candidate.title) == _normalized_text(request.target_title)
    ]
    if not matching_titles:
        return None, None, "provider_opening_title_mismatch"
    matching_locations = [
        candidate
        for candidate in matching_titles
        if not request.target_location
        or _provider_probe_location_matches(
            candidate.location,
            request.target_location,
        )
    ]
    if not matching_locations:
        return None, None, "provider_opening_location_mismatch"

    full_tokens = _meaningful_company_tokens(request.company_name)
    leading_tokens = full_tokens[: -len(slug.stripped_tokens)]
    for opening in matching_locations:
        try:
            opening_url = canonicalize_identity_url(opening.url)
        except (TypeError, ValueError):
            continue
        opening_board = _canonical_provider_board(adapter, opening.url)
        if opening_board is None or not _same_provider_tenant(
            result.board,
            opening_board,
            result.provider,
        ):
            continue
        for evidence in result.employer_evidence:
            if evidence.opening_url != opening_url:
                continue
            employer_tokens = _meaningful_company_tokens(evidence.employer_name)
            if employer_tokens != leading_tokens:
                continue
            if not set(slug.stripped_tokens).issubset(evidence.descriptor_terms):
                continue
            return opening, evidence, "provider_published_employer_verified"

    if not result.employer_evidence:
        return None, None, "provider_employer_evidence_missing"
    for evidence in result.employer_evidence:
        if any(
            _same_identity_url(evidence.opening_url, opening.url)
            for opening in matching_locations
        ):
            employer_tokens = _meaningful_company_tokens(evidence.employer_name)
            if employer_tokens != leading_tokens:
                return None, None, "provider_employer_name_mismatch"
            if not set(slug.stripped_tokens).issubset(evidence.descriptor_terms):
                return None, None, "provider_employer_descriptor_mismatch"
            return None, None, "provider_opening_tenant_mismatch"
    return None, None, "provider_employer_opening_mismatch"


def _canonical_provider_board(adapter, url: str) -> JobBoard | None:
    board = adapter.identify_board(url)
    if board is None:
        return None
    canonicalize = getattr(adapter, "canonicalize_board", None)
    if not callable(canonicalize):
        return board
    try:
        return canonicalize(board)
    except (TypeError, ValueError):
        return None


def _same_identity_url(left: str, right: str) -> bool:
    try:
        return canonicalize_identity_url(left) == canonicalize_identity_url(right)
    except (TypeError, ValueError):
        return False


def _normalized_text(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _provider_probe_location_matches(
    candidate_location: str | None,
    target_location: str,
) -> bool:
    classification = classify_location(candidate_location, target_location)
    if classification in {"exact", "overlap"}:
        return True
    if classification != "region" or not candidate_location:
        return False
    candidate_state = _target_state_code(candidate_location)
    target_state = _target_state_code(target_location)
    if not candidate_state or candidate_state != target_state:
        return False

    def city(value: str, state: str) -> tuple[str, ...]:
        first_component = value.split(",", 1)[0]
        return tuple(
            token
            for token in re.findall(r"[a-z0-9]+", first_component.casefold())
            if token not in {state.casefold(), "remote"}
        )

    candidate_city = city(candidate_location, candidate_state)
    target_city = city(target_location, target_state)
    return bool(candidate_city and candidate_city == target_city)


def _meaningful_company_tokens(value: str) -> tuple[str, ...]:
    ignored = {
        "co",
        "company",
        "corp",
        "corporation",
        "group",
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "the",
    }
    return tuple(
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in ignored
    )


@dataclass(frozen=True)
class ProviderProbeSlug:
    value: str
    kind: Literal["ordinary", "case_preserved", "acronym_suffix_stripped"]
    stripped_tokens: tuple[str, ...]


def _provider_probe_slug_candidates(
    company_name: str,
    website_url: str,
    linkedin_company_url: str | None = None,
) -> tuple[ProviderProbeSlug, ...]:
    ordinary = list(
        _provider_slug_candidates(company_name, website_url, linkedin_company_url)
    )
    stripped = _acronym_suffix_stripped_slug(company_name)
    if stripped is None:
        return tuple(
            ProviderProbeSlug(
                value=value,
                kind="ordinary",
                stripped_tokens=(),
            )
            for value in ordinary
        )
    exact_slug, stripped_slug, stripped_tokens = stripped
    # A hostname may already have supplied the stripped value. Reposition it
    # immediately after the full company slug so it cannot bypass the strict
    # provider-published employer binding below.
    ordinary = [value for value in ordinary if value != stripped_slug]
    if exact_slug not in ordinary:
        ordinary.insert(0, exact_slug)
    exact_index = ordinary.index(exact_slug)
    ordered = ordinary[: exact_index + 1] + [stripped_slug] + ordinary[exact_index + 1 :]
    return tuple(
        ProviderProbeSlug(
            value=value,
            kind=("acronym_suffix_stripped" if value == stripped_slug else "ordinary"),
            stripped_tokens=(stripped_tokens if value == stripped_slug else ()),
        )
        for value in ordered
    )


def _acronym_suffix_stripped_slug(
    company_name: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    original_tokens = re.findall(r"[A-Za-z0-9]+", company_name)
    meaningful_tokens = _meaningful_company_tokens(company_name)
    if (
        len(meaningful_tokens) != 2
        or len(original_tokens) < 2
        or original_tokens[-1] != meaningful_tokens[-1].upper()
        or not re.fullmatch(r"[A-Z]{2,8}", original_tokens[-1])
    ):
        return None
    return (
        "-".join(meaningful_tokens),
        "-".join(meaningful_tokens[:-1]),
        meaningful_tokens[-1:],
    )


def _case_preserved_lever_tenant(
    company_name: str,
    slugs: tuple[ProviderProbeSlug, ...],
) -> str | None:
    original_tokens = re.findall(r"[A-Za-z0-9]+", company_name)
    meaningful_tokens = _meaningful_company_tokens(company_name)
    if len(original_tokens) != 1 or len(meaningful_tokens) != 1:
        return None
    original = original_tokens[0]
    if original == original.casefold() or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", original):
        return None
    if not any(slug.value.casefold() == original.casefold() for slug in slugs):
        return None
    return original


def _provider_slug_candidates(
    company_name: str,
    website_url: str,
    linkedin_company_url: str | None = None,
) -> tuple[str, ...]:
    legal_ignored = {"the"}
    brand_ignored = {
        "co",
        "company",
        "corp",
        "corporation",
        "group",
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "the",
    }
    all_tokens = re.findall(r"[a-z0-9]+", company_name.casefold())
    legal_tokens = [token for token in all_tokens if token not in legal_ignored]
    brand_tokens = [
        token
        for token in all_tokens
        if token not in brand_ignored
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
        host_label,
        "-".join(legal_tokens),
        *linkedin_variants,
        "".join(legal_tokens),
        "".join(brand_tokens),
        "-".join(brand_tokens),
    )
    return tuple(dict.fromkeys(value for value in values if value))[:5]
