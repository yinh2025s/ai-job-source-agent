from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlparse

from .career_search import CareerSearchResolver
from .errors import DiscoveryError
from .provider_candidates import (
    MAX_PROVIDER_CANDIDATES,
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    ProviderCandidate,
    ProviderCandidatePool,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from .scoring import is_ats_url
from .web import FetchError, domain_of


class CareerSurfaceCandidateDiscovery:
    """Verify branded Career search leads before extracting provider boards."""

    candidate_wave = "search"

    def __init__(
        self,
        resolver: CareerSearchResolver,
        job_board_service,
        *,
        provider_registry: ProviderRegistry = DEFAULT_PROVIDER_REGISTRY,
        max_surface_candidates: int = 2,
        max_candidates: int = MAX_PROVIDER_CANDIDATES,
    ) -> None:
        if not 1 <= max_surface_candidates <= 4:
            raise ValueError("Career surface candidate limit is invalid")
        if not 1 <= max_candidates <= MAX_PROVIDER_CANDIDATES:
            raise ValueError("Career surface provider limit is invalid")
        self.resolver = resolver
        self.job_board_service = job_board_service
        self.provider_registry = provider_registry
        self.max_surface_candidates = max_surface_candidates
        self.max_candidates = max_candidates

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        # An already verified Career input is handled by the direct/legacy route.
        if request.career_page_url:
            return CandidateDiscoveryResult((), {
                "source": "verified_career_surface_search",
                "status": "skipped",
                "reason": "verified_career_input_available",
                "candidate_count": 0,
            })

        search = self.resolver.search(
            request.company_name,
            request.company_website_url or "",
            target_title=request.target_title,
            ats_only=False,
            exhaustive=False,
            allow_unbound_career=True,
            query_diversity_first=True,
        )
        query_by_url = _query_by_url(search.trace)
        candidates: list[ProviderCandidate] = []
        attempts: list[dict[str, Any]] = []

        for rank, lead in enumerate(
            search.candidates[: self.max_surface_candidates], start=1
        ):
            attempt: dict[str, Any] = {"url": lead.url, "status": "rejected"}
            if is_ats_url(lead.url):
                attempt["reason"] = "provider_lead_owned_by_provider_search"
                attempts.append(attempt)
                continue
            try:
                page = self.resolver.fetcher.fetch(lead.url)
            except FetchError as exc:
                attempt.update(
                    reason="surface_fetch_failed",
                    retryable=exc.retryable,
                    error_type=type(exc).__name__,
                )
                attempts.append(attempt)
                continue
            final_url = page.final_url or page.url
            verification = _verify_career_surface(
                request.company_name,
                lead.url,
                final_url,
                page.html,
            )
            attempt["verification"] = verification
            if not verification["verified"]:
                attempt["reason"] = verification["reason"]
                attempts.append(attempt)
                continue
            try:
                _job_list_url, board_trace, portfolio = (
                    self.job_board_service.find_job_board_portfolio(
                        final_url,
                        company_name=request.company_name,
                        target_title=request.target_title,
                        target_location=request.target_location,
                    )
                )
            except (DiscoveryError, FetchError, OSError, TimeoutError, TypeError, ValueError) as exc:
                attempt.update(
                    reason="board_verification_failed",
                    error_type=type(exc).__name__,
                )
                attempts.append(attempt)
                continue
            if portfolio is None:
                attempt["reason"] = "no_typed_provider_board"
                attempts.append(attempt)
                continue

            emitted = 0
            for discovered in portfolio.boards:
                adapter = self.provider_registry.adapter_for(discovered.board.url)
                board = (
                    adapter.identify_board(discovered.board.url)
                    if adapter is not None
                    else None
                )
                if adapter is None or board is None or not adapter.supports_listing:
                    continue
                query = query_by_url.get(lead.url)
                if not query:
                    continue
                try:
                    candidates.append(
                        ProviderCandidate(
                            url=board.url,
                            source_kind="targeted_board_search",
                            source_url=final_url,
                            company_name=request.company_name,
                            target_title=request.target_title,
                            target_location=request.target_location,
                            provider_hint=board.provider,
                            query=query,
                            result_rank=rank,
                        )
                    )
                except (TypeError, ValueError):
                    continue
                emitted += 1
            attempt.update(
                status="verified" if emitted else "rejected",
                reason=(
                    "verified_provider_handoff"
                    if emitted
                    else "no_listable_provider_board"
                ),
                provider_candidate_count=emitted,
                board_trace_status=(
                    board_trace.get("selected_from")
                    if isinstance(board_trace, dict)
                    else None
                ),
            )
            attempts.append(attempt)
            if emitted:
                break

        pool = ProviderCandidatePool.build(candidates, limit=self.max_candidates)
        return CandidateDiscoveryResult(
            pool.candidates,
            {
                "source": "verified_career_surface_search",
                "search": search.trace,
                "attempts": attempts,
                "candidate_count": len(pool.candidates),
                "truncated": pool.truncated,
            },
        )


class _SurfaceMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: list[str] = []
        self.identity_values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() in {"title", "h1", "h2"}:
            self._capture = True
            self._parts = []
        if tag.casefold() == "meta":
            value = attributes.get("content", "").strip()
            if value:
                self.identity_values.append(value)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag.casefold() in {"title", "h1", "h2"}:
            value = " ".join("".join(self._parts).split())
            if value:
                self.identity_values.append(value)
            self._capture = False
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)


def _verify_career_surface(
    company_name: str,
    requested_url: str,
    final_url: str,
    html: str,
) -> dict[str, Any]:
    if _registrable_site(domain_of(requested_url)) != _registrable_site(
        domain_of(final_url)
    ):
        return {"verified": False, "reason": "cross_site_redirect"}
    tokens = _identity_tokens(company_name)
    if not tokens:
        return {"verified": False, "reason": "company_identity_unavailable"}
    host_text = re.sub(r"[^a-z0-9]+", "", domain_of(final_url).casefold())
    compact = "".join(tokens)
    host_match = (
        compact in host_text
        if len(tokens) == 1
        else any(token in host_text for token in tokens)
    )
    if not host_match:
        return {"verified": False, "reason": "career_host_identity_mismatch"}

    parser = _SurfaceMetadataParser()
    try:
        parser.feed((html or "")[:500_000])
        parser.close()
    except (TypeError, ValueError):
        return {"verified": False, "reason": "invalid_career_markup"}
    normalized_values = [
        re.sub(r"[^a-z0-9]+", " ", value.casefold())
        for value in parser.identity_values
    ]
    identity = [
        value
        for value in normalized_values
        if all(re.search(rf"\b{re.escape(token)}\b", value) for token in tokens)
    ]
    career = [
        value
        for value in normalized_values
        if re.search(
            r"\b(?:careers?|jobs?|hiring|openings?|positions?|employment)\b",
            value,
        )
    ]
    if not identity:
        return {"verified": False, "reason": "career_page_identity_mismatch"}
    if not career:
        return {"verified": False, "reason": "career_semantics_missing"}
    return {
        "verified": True,
        "reason": "current_page_identity_and_career_semantics",
        "identity_evidence": identity[:2],
        "career_evidence": career[:2],
    }


def _identity_tokens(company_name: str) -> list[str]:
    ignored = {
        "and", "co", "company", "corp", "corporation", "group", "inc",
        "incorporated", "llc", "ltd", "the",
    }
    return [
        token
        for token in re.findall(r"[a-z0-9]+", company_name.casefold())
        if len(token) >= 3 and token not in ignored
    ]


def _registrable_site(host: str) -> str:
    labels = [label for label in host.casefold().strip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    two_level_suffixes = {"co.jp", "co.nz", "co.uk", "com.au", "com.br", "com.sg"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in two_level_suffixes else suffix


def _query_by_url(trace: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in trace.get("queries", []):
        if not isinstance(item, dict) or not isinstance(item.get("query"), str):
            continue
        for candidate in item.get("candidates", []):
            if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                output.setdefault(candidate["url"], item["query"])
    return output
