from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from html import unescape
from urllib.parse import parse_qs, parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from .career_search import search_site_openings
from .job_board import DiscoveredJobBoard
from .content_probe import probe_first_party_provider_assets
from .generic_opening_inventory import collect_generic_opening_inventory
from .js_declared_inventory import discover_js_declared_inventory
from .browser_interaction import JobSearchInteraction
from .job_search_actions import (
    JobSearchAction,
    TitleSearchQuery,
    discover_job_search_actions,
    resolve_declared_search_route,
    submit_job_search_action,
    title_search_queries,
    verify_job_search_submission,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, JobQuery, ProviderRegistry
from .rendered_fetcher import FORCE_RENDER_HEADER
from .listing_extraction import (
    extract_detail_page_candidates,
    extract_listing_candidates,
    validate_output_url,
)
from .scoring import is_likely_job_detail, score_job_link
from .web import (
    FetchError,
    Fetcher,
    Page,
    RawLink,
    domain_of,
    extract_links,
    safe_normalize_url,
)
from .website_resolver import location_region


STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "i",
    "ii",
    "iii",
    "in",
    "of",
    "the",
    "to",
}

MIN_TITLE_MATCH_SCORE = 45
MIN_PROVIDER_TITLE_MATCH_SCORE = 65
@dataclass
class OpeningMatch:
    url: str
    title: str
    score: int
    provider: str
    reasons: list[str]
    job_list_page_url: str | None = None
    location_score: int = 0
    location: str | None = None
    hiring_organization_name: str | None = None


@dataclass
class ProviderApiRequest:
    url: str
    data: bytes | None = None
    headers: dict[str, str] | None = None


class JobOpeningMatcher:
    def __init__(
        self,
        fetcher: Fetcher,
        provider_registry: ProviderRegistry | None = None,
        *,
        max_generic_job_pages: int = 3,
    ) -> None:
        self.fetcher = fetcher
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.max_generic_job_pages = max_generic_job_pages

    def match(
        self,
        job_list_url: str,
        target_title: str | None,
        target_location: str | None = None,
        *,
        discovered_board: DiscoveredJobBoard | None = None,
    ) -> tuple[OpeningMatch | None, dict]:
        trace = {
            "job_list_url": job_list_url,
            "target_title": target_title,
            "target_location": target_location,
            "provider": self.provider_registry.detect(job_list_url),
            "searched_urls": [],
            "candidates": [],
        }
        if not target_title:
            return None, trace
        title_queries = title_search_queries(target_title)

        api_match, api_trace, landing_page = self._match_provider_api(
            job_list_url,
            target_title,
            target_location,
            discovered_board=discovered_board,
        )
        trace["provider_api"] = api_trace
        if api_trace.get("provider") and api_trace["provider"] != "generic":
            trace["provider"] = api_trace["provider"]
        if api_match:
            trace["selected"] = {
                "url": api_match.url,
                "title": api_match.title,
                "location": api_match.location,
                "hiring_organization_name": api_match.hiring_organization_name,
                "score": api_match.score,
                "reasons": api_match.reasons,
            }
            return api_match, trace

        inventory = api_trace.get("inventory")
        if (
            isinstance(inventory, dict)
            and inventory.get("source") == "native_adapter"
            and inventory.get("complete") is True
        ):
            trace["search_plan"] = []
            trace["search_skipped"] = "verified_native_inventory_no_match"
            return None, trace
        if (
            isinstance(inventory, dict)
            and inventory.get("source") == "native_adapter"
            and inventory.get("reason_code")
            in {"COMPANY_TIME_BUDGET_EXHAUSTED", "FETCH_BUDGET_EXHAUSTED"}
        ):
            trace["search_plan"] = []
            trace["search_skipped"] = "native_inventory_budget_exhausted"
            return None, trace

        action_discovery = (
            discover_job_search_actions(landing_page, target_title)
            if landing_page is not None
            else None
        )
        if action_discovery is not None:
            trace["job_search_actions"] = list(action_discovery.trace)
        declared_search_routes = []
        if landing_page is not None:
            for query in title_queries:
                route = resolve_declared_search_route(self.fetcher, landing_page, query.value)
                declared_search_routes.append((route, query))
        if declared_search_routes:
            declared_search_route, first_route_query = declared_search_routes[0]
            trace["declared_search_route"] = {
                "status": declared_search_route.status,
                "helper_url": declared_search_route.helper_url,
                "route_url": declared_search_route.route_url,
                "query": first_route_query.value,
                "query_source": first_route_query.source,
            }
            trace["declared_search_routes"] = [
                {"status": route.status, "helper_url": route.helper_url,
                 "route_url": route.route_url, "query": query.value,
                 "query_source": query.source}
                for route, query in declared_search_routes
            ]
        search_plan = _build_search_plan(
            job_list_url,
            target_title,
            landing_page,
            declared_actions=(
                action_discovery.actions if action_discovery is not None else ()
            ),
            interactive_actions=(
                action_discovery.interactive_actions
                if action_discovery is not None
                else ()
            ),
            declared_search_routes=tuple(
                (route.route_url, query)
                for route, query in declared_search_routes
                if route.route_url is not None
            ),
            title_queries=title_queries,
        )
        trace["search_plan"] = []
        for search_url, _page, source, interaction, query, _action in search_plan:
            item = {"url": search_url, "source": source}
            if interaction is not None:
                item["interaction"] = _interaction_trace(interaction)
            if query is not None:
                item["query"] = query.value
                item["query_source"] = query.source
            trace["search_plan"].append(item)
        for search_url, reusable_page, search_source, interaction, query, action in search_plan:
            trace["searched_urls"].append(search_url)
            if reusable_page is not None:
                page = reusable_page
            elif action is not None and action.method.upper() == "POST":
                if landing_page is None or query is None:
                    continue
                submission = submit_job_search_action(
                    self.fetcher,
                    landing_page,
                    action,
                    query.value,
                )
                trace.setdefault("job_search_submissions", []).append(
                    {
                        "url": submission.request_url,
                        "source": search_source,
                        "status": submission.status,
                        "change_kind": submission.change_kind,
                        "query": query.value,
                        "query_source": query.source,
                    }
                )
                if submission.page is None:
                    continue
                page = submission.page
            else:
                try:
                    if interaction is None:
                        page = self.fetcher.fetch(search_url)
                    else:
                        trace["interactive_search"] = {
                            "disposition": "attempted",
                            "url": search_url,
                            "interaction": _interaction_trace(interaction),
                        }
                        page = self.fetcher.fetch(
                            search_url,
                            interaction=interaction,
                        )
                except TypeError as exc:
                    if interaction is None or "interaction" not in str(exc):
                        raise
                    _record_interactive_failure(
                        trace,
                        search_url,
                        "capability_unavailable",
                        str(exc),
                    )
                    continue
                except FetchError as exc:
                    if interaction is not None:
                        _record_interactive_failure(
                            trace,
                            search_url,
                            "fetch_failed",
                            str(exc),
                            detail_reason_code=exc.reason_code,
                            retryable=exc.retryable,
                        )
                    else:
                        trace.setdefault("errors", []).append(
                            {"url": search_url, "error": str(exc)}
                        )
                    continue

            if interaction is not None:
                if landing_page is None:
                    continue
                submission = verify_job_search_submission(
                    landing_page,
                    page,
                    request_url=search_url,
                )
                if submission.page is None:
                    _record_interactive_failure(
                        trace,
                        search_url,
                        submission.status,
                        "interactive search did not produce verified transport progress",
                    )
                    continue
                page = submission.page
                page_url = page.final_url or page.url
                trace["interactive_search"]["disposition"] = "fetched"
                trace["interactive_search"]["final_url"] = page_url
                trace["interactive_search"]["change_kind"] = submission.change_kind
            else:
                page_url = page.final_url or page.url
            page_links = (
                extract_links(page)
                + structured_job_links(
                    page.html,
                    page_url,
                    trusted_declared_inventory=(
                        "first_party_declared_inventory" in page.source
                    ),
                )
            )
            candidates = _opening_candidates_from_links(
                page_links,
                page_url=page_url,
                target_title=target_title,
                target_location=target_location,
                provider=trace["provider"],
                excluded_urls=(job_list_url,),
            )
            if candidates:
                _record_candidates(trace, candidates)
                if interaction is not None:
                    trace["interactive_search"]["disposition"] = "matched"
                selected = self._select_with_verified_detail(
                    candidates,
                    job_list_url=job_list_url,
                    target_title=target_title,
                    target_location=target_location,
                    provider=trace["provider"],
                    trace=trace,
                )
                if selected is not None:
                    trace["selected"] = _selected_candidate_trace(selected)
                    return selected, trace

            inventory_links: list[RawLink] = []
            title_filtered_fallback = (
                search_source == "provider_fallback"
                and _is_title_filtered_fallback(page_url, target_title)
            )
            if search_source in {
                "reused_landing_page",
                "declared_get_form",
                "declared_search_route",
                "interactive_job_search",
            } or title_filtered_fallback:
                generic_inventory = collect_generic_opening_inventory(
                    self.fetcher,
                    page,
                    max_pages=self.max_generic_job_pages,
                    max_candidates=1_000,
                )
                inventory_links = [
                    candidate.as_raw_link()
                    for candidate in generic_inventory.candidates
                ]
                strongest_title_score = max(
                    (
                        score_title_match(candidate.title, target_title)[0]
                        for candidate in generic_inventory.candidates
                    ),
                    default=0,
                )
                inventory_trace = {
                    "source": search_source,
                    "start_url": page_url,
                    "pages_fetched": generic_inventory.pages_fetched,
                    "candidate_count": len(generic_inventory.candidates),
                    "complete": generic_inventory.inventory_complete,
                    "stop_reason": generic_inventory.stop_reason,
                    "pages": [
                        {
                            "url": item.url,
                            "candidate_count": item.candidate_count,
                            "reason": item.reason,
                        }
                        for item in generic_inventory.trace
                    ],
                }
                trace.setdefault("generic_inventory", []).append(inventory_trace)
                existing_inventory = api_trace.get("inventory")
                if generic_inventory.inventory_complete and (
                    not isinstance(existing_inventory, dict)
                    or existing_inventory.get("source") != "native_adapter"
                ):
                    api_trace["inventory"] = {
                        "source": "generic_html",
                        "scope": (
                            "filtered"
                            if search_source
                            in {
                                "declared_get_form",
                                "declared_search_route",
                                "interactive_job_search",
                            }
                            or title_filtered_fallback
                            else "full"
                        ),
                        "status": (
                            "verified"
                            if generic_inventory.inventory_complete
                            and generic_inventory.candidates
                            else (
                                "verified_empty"
                                if generic_inventory.inventory_complete
                                else "incomplete"
                            )
                        ),
                        "complete": generic_inventory.inventory_complete,
                        "candidate_count": len(generic_inventory.candidates),
                        "strongest_title_score": strongest_title_score,
                        "stop_reason": generic_inventory.stop_reason,
                    }
            candidates = _opening_candidates_from_links(
                inventory_links,
                page_url=page_url,
                target_title=target_title,
                target_location=target_location,
                provider=trace["provider"],
                excluded_urls=(job_list_url,),
            )
            _record_candidates(trace, candidates)
            if candidates:
                if interaction is not None:
                    trace["interactive_search"]["disposition"] = "matched"
                selected = self._select_with_verified_detail(
                    candidates,
                    job_list_url=job_list_url,
                    target_title=target_title,
                    target_location=target_location,
                    provider=trace["provider"],
                    trace=trace,
                )
                if selected is not None:
                    trace["selected"] = _selected_candidate_trace(selected)
                    return selected, trace

            if search_source in {
                "reused_landing_page",
                "declared_get_form",
                "interactive_job_search",
            }:
                js_inventory = discover_js_declared_inventory(
                    self.fetcher,
                    page,
                    target_title,
                )
                js_trace = {
                    "status": js_inventory.trace.status,
                    "retryable": js_inventory.trace.retryable,
                    "blocked": js_inventory.trace.blocked,
                    "assets_considered": list(js_inventory.trace.assets_considered),
                    "assets_fetched": list(js_inventory.trace.assets_fetched),
                    "endpoint_url": js_inventory.trace.endpoint_url,
                    "request_fields": list(js_inventory.trace.request_fields),
                    "candidate_count": js_inventory.trace.candidate_count,
                    "detail": js_inventory.trace.detail,
                    "inventory_scope": js_inventory.trace.inventory_scope,
                }
                trace.setdefault("js_declared_inventory", []).append(js_trace)
                if js_inventory.trace.status in {"verified", "candidate_cap_reached"}:
                    js_links = [
                        RawLink(
                            url=item.url,
                            text=item.title,
                            source_url=page_url,
                            origin="verified_declared_inventory",
                            location=item.location,
                        )
                        for item in js_inventory.candidates
                    ]
                    candidates = _opening_candidates_from_links(
                        js_links,
                        page_url=page_url,
                        target_title=target_title,
                        target_location=target_location,
                        provider=trace["provider"],
                        excluded_urls=(job_list_url,),
                    )
                    _record_candidates(trace, candidates)
                    api_trace["inventory"] = {
                        "source": "js_declared_inventory",
                        "scope": js_inventory.trace.inventory_scope,
                        "status": (
                            "verified_filtered_empty"
                            if js_inventory.inventory_complete
                            and not js_inventory.candidates
                            else js_inventory.trace.status
                        ),
                        "complete": js_inventory.inventory_complete,
                        "candidate_count": len(js_inventory.candidates),
                    }
                    api_trace["provider_detection"] = {
                        "method": "verified_declared_inventory",
                        "provider": "generic",
                        "url": job_list_url,
                        "endpoint_url": js_inventory.trace.endpoint_url,
                        "inventory_complete": js_inventory.inventory_complete,
                        "inventory_count": len(js_inventory.candidates),
                        "inventory_scope": js_inventory.trace.inventory_scope,
                    }
                    if candidates:
                        if interaction is not None:
                            trace["interactive_search"]["disposition"] = "matched"
                        selected = self._select_with_verified_detail(
                            candidates,
                            job_list_url=job_list_url,
                            target_title=target_title,
                            target_location=target_location,
                            provider=trace["provider"],
                            trace=trace,
                        )
                        if selected is not None:
                            trace["selected"] = _selected_candidate_trace(selected)
                            return selected, trace
                elif js_inventory.trace.blocked:
                    api_trace["inventory"] = {
                        "source": "js_declared_inventory",
                        "scope": "filtered",
                        "status": js_inventory.trace.status,
                        "complete": False,
                        "candidate_count": 0,
                        "reason_code": "BOT_PROTECTION",
                    }
            if interaction is not None:
                trace["interactive_search"]["disposition"] = "no_exact_match"
                trace["interactive_search"][
                    "reason_code"
                ] = "OPENING_DISCOVERY_INCOMPLETE"

        native_inventory_incomplete = bool(
            trace["provider"] == "cws"
            and
            isinstance(inventory, dict)
            and inventory.get("source") == "native_adapter"
            and inventory.get("complete") is not True
        )
        if trace["provider"] == "generic" or native_inventory_incomplete:
            site_match, site_trace = self._match_verified_site_search(
                job_list_url,
                target_title,
                target_location,
                provider=trace["provider"],
            )
        else:
            site_match = None
            site_trace = {
                "skipped": "native_provider_owns_opening_identity",
                "provider": trace["provider"],
            }
        trace["verified_site_search"] = site_trace
        if site_match is not None:
            trace["selected"] = _selected_candidate_trace(site_match)
            return site_match, trace

        fallback_url = build_search_result_url(job_list_url, target_title)
        if fallback_url:
            trace["fallback_search_url"] = fallback_url
        return None, trace

    def _select_with_verified_detail(
        self,
        candidates: list[OpeningMatch],
        *,
        job_list_url: str,
        target_title: str,
        target_location: str | None,
        provider: str,
        trace: dict,
    ) -> OpeningMatch | None:
        selected = candidates[0]
        if provider != "generic" or not target_location:
            return selected

        for candidate in candidates:
            if candidate.location and _strict_location_identity_matches(
                candidate.location,
                target_location,
            ):
                return candidate

        expected_domain = domain_of(job_list_url)
        expected_site = _registrable_site(expected_domain)
        attempts: list[dict] = []
        verified: list[OpeningMatch] = []
        for candidate in candidates[:3]:
            if not title_identity_matches(
                candidate.title,
                target_title,
            ):
                continue
            candidate_url = safe_normalize_url(candidate.url)
            if (
                not candidate_url
                or not expected_site
                or _registrable_site(domain_of(candidate_url)) != expected_site
            ):
                attempts.append({"url": candidate.url, "status": "cross_site_rejected"})
                continue
            try:
                page = self.fetcher.fetch(candidate_url)
            except FetchError as error:
                attempts.append(
                    {"url": candidate_url, "status": "fetch_failed", "error": str(error)}
                )
                continue
            page_url = safe_normalize_url(page.final_url or page.url)
            if (
                not page_url
                or _registrable_site(domain_of(page_url)) != expected_site
                or _page_indicates_closed_opening(page.html)
            ):
                attempts.append({"url": candidate_url, "status": "page_rejected"})
                continue
            page_identity = page_url.rstrip("/").casefold()
            matched = None
            detail_postings = _strict_json_ld_job_postings(page.html, page_url)
            detail_postings.extend(
                _strict_embedded_job_detail_postings(page.html, page_url)
            )
            detail_postings.extend(
                _strict_page_bound_detail_postings(page.html, page_url)
            )
            for posting in detail_postings:
                posting_url = validate_output_url(
                    posting["url"],
                    page_url,
                    title=posting["title"],
                )
                declared_posting_url = safe_normalize_url(
                    str(posting["url"]),
                    page_url,
                )
                if (
                    not posting_url
                    and declared_posting_url
                    and declared_posting_url.rstrip("/").casefold() == page_identity
                    and candidate_url.rstrip("/").casefold() == page_identity
                ):
                    posting_url = candidate_url
                if (
                    not posting_url
                    or posting_url.rstrip("/").casefold() != page_identity
                    or not title_identity_matches(posting["title"], target_title)
                    or not _strict_location_identity_matches(
                        posting["location"],
                        target_location,
                    )
                    or not _listing_detail_hiring_organization_matches(
                        posting,
                        expected_domain,
                    )
                ):
                    continue
                location_score, location_reasons = score_location_match(
                    posting["location"],
                    target_location,
                )
                matched = replace(
                    candidate,
                    url=posting_url,
                    title=posting["title"],
                    score=candidate.score + 100,
                    reasons=(
                        candidate.reasons
                        + ["verified same-site JobPosting detail"]
                        + location_reasons
                    ),
                    location_score=location_score,
                    location=posting["location"],
                    hiring_organization_name=posting["hiring_organization_name"],
                )
                break
            if matched is None:
                attempts.append(
                    {"url": candidate_url, "status": "jobposting_identity_not_verified"}
                )
                continue
            attempts.append({"url": candidate_url, "status": "verified"})
            verified.append(matched)

        if attempts:
            trace["detail_enrichment"] = {
                "attempts": attempts,
                "verified_count": len(verified),
            }
        if not verified:
            url_location_score, _url_location_reasons = (
                _score_url_location_tiebreaker(selected.url, target_location)
            )
            if url_location_score > 0:
                return selected
            trace["location_unverified_candidate_rejected"] = {
                "url": selected.url,
                "candidate_location": selected.location,
                "target_location": target_location,
                "reason": (
                    "generic candidate location was broader than the target"
                    if selected.location
                    else "generic candidate lacked verifiable location evidence"
                ),
            }
            return None
        verified.sort(
            key=lambda candidate: (candidate.score, candidate.location_score),
            reverse=True,
        )
        return verified[0]

    def _match_verified_site_search(
        self,
        job_list_url: str,
        target_title: str,
        target_location: str | None,
        *,
        provider: str,
    ) -> tuple[OpeningMatch | None, dict]:
        result = search_site_openings(
            self.fetcher,
            job_list_url,
            target_title,
            max_results=3,
            max_source_fetches=2,
        )
        trace = {
            "search": result.trace,
            "verified_pages": [],
            "rejected_pages": [],
        }
        expected_domain = domain_of(job_list_url)
        expected_site = _registrable_site(expected_domain)
        verified_candidates: list[OpeningMatch] = []
        for lead in result.candidates:
            try:
                page = self.fetcher.fetch(lead.url)
            except FetchError as error:
                trace["rejected_pages"].append(
                    {"url": lead.url, "reason": "fetch_failed", "error": str(error)}
                )
                continue
            page_url = page.final_url or page.url
            page_domain = domain_of(page_url)
            if not expected_site or _registrable_site(page_domain) != expected_site:
                trace["rejected_pages"].append(
                    {"url": lead.url, "reason": "cross_site_redirect"}
                )
                continue
            if _page_indicates_closed_opening(page.html):
                trace["rejected_pages"].append(
                    {"url": lead.url, "reason": "opening_closed_or_unavailable"}
                )
                continue
            page_identity = (safe_normalize_url(page_url) or "").rstrip("/").casefold()
            candidates: list[OpeningMatch] = []
            for posting in _strict_json_ld_job_postings(page.html, page_url):
                validated_url = validate_output_url(
                    posting["url"],
                    page_url,
                    title=posting["title"],
                )
                if (
                    not validated_url
                    or validated_url.rstrip("/").casefold() != page_identity
                ):
                    continue
                title_score, title_reasons = score_title_match(
                    posting["title"],
                    target_title,
                )
                if (
                    title_score < MIN_PROVIDER_TITLE_MATCH_SCORE
                    or not title_identity_matches(posting["title"], target_title)
                ):
                    continue
                if not _same_site_hiring_organization(
                    posting["hiring_organization_url"],
                    expected_domain,
                ):
                    trace["rejected_pages"].append(
                        {"url": lead.url, "reason": "hiring_organization_not_first_party"}
                    )
                    continue
                if target_location and not _strict_location_identity_matches(
                    posting["location"],
                    target_location,
                ):
                    trace["rejected_pages"].append(
                        {"url": lead.url, "reason": "location_identity_mismatch"}
                    )
                    continue
                location_score, location_reasons = score_location_match(
                    posting["location"],
                    target_location,
                )
                candidates.append(
                    OpeningMatch(
                        url=validated_url,
                        title=posting["title"],
                        score=title_score + 100,
                        provider=provider,
                        reasons=(
                            ["verified same-site JobPosting page"]
                            + title_reasons
                            + location_reasons
                        ),
                        job_list_page_url=job_list_url,
                        location_score=location_score,
                        location=posting["location"],
                        hiring_organization_name=posting["hiring_organization_name"],
                    )
                )
            if not candidates:
                trace["rejected_pages"].append(
                    {"url": lead.url, "reason": "jobposting_identity_not_verified"}
                )
                continue
            for candidate in candidates:
                trace["verified_pages"].append(
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "location": candidate.location,
                        "hiring_organization_name": candidate.hiring_organization_name,
                    }
                )
                candidate.reasons.append("verified same-site title-targeted search page")
                verified_candidates.append(candidate)
        verified_candidates.sort(
            key=lambda candidate: (candidate.score, candidate.location_score),
            reverse=True,
        )
        return (verified_candidates[0] if verified_candidates else None), trace

    def _match_provider_api(
        self,
        job_list_url: str,
        target_title: str,
        target_location: str | None = None,
        *,
        discovered_board: DiscoveredJobBoard | None = None,
    ) -> tuple[OpeningMatch | None, dict, Page | None]:
        provider = self.provider_registry.detect(job_list_url)
        adapter = self.provider_registry.adapter_for(job_list_url)
        board = adapter.identify_board(job_list_url) if adapter else None
        page_detection = None
        landing_page = None
        if (
            discovered_board is not None
            and discovered_board.board.url.rstrip("/") == job_list_url.rstrip("/")
        ):
            typed_adapter = self.provider_registry.adapter_named(
                discovered_board.board.provider
            )
            if typed_adapter is not None:
                adapter = typed_adapter
                board = discovered_board.board
                provider = typed_adapter.name
                page_detection = {
                    "method": "typed_stage_handoff",
                    "source_method": discovered_board.detection_method,
                    "provider": provider,
                    "url": board.url,
                    "evidence_url": discovered_board.evidence_url,
                }
        if adapter is None:
            try:
                force_verified_generic_board = bool(
                    discovered_board is not None
                    and discovered_board.board.provider == "generic"
                    and discovered_board.detection_method
                    == "verified_first_party_action"
                    and discovered_board.board.url.rstrip("/")
                    == job_list_url.rstrip("/")
                )
                landing_page = self.fetcher.fetch(
                    job_list_url,
                    headers=(
                        {FORCE_RENDER_HEADER: "force"}
                        if force_verified_generic_board
                        else None
                    ),
                )
            except FetchError as exc:
                page_detection = {"method": "page_evidence", "error": str(exc)}
            else:
                # A verified generic board may arrive without its runtime-only
                # DiscoveredJobBoard after a stage/checkpoint boundary. Rebuild
                # bounded public inventory evidence from the landing page so S6
                # does not silently discard an S5-declared inventory.
                landing_page, declared_probe = probe_first_party_provider_assets(
                    self.fetcher,
                    landing_page,
                    self._recognizes_listing_provider,
                    self._provider_board_identity,
                )
                if (
                    isinstance(declared_probe, dict)
                    and declared_probe.get("method")
                    == "first_party_declared_inventory"
                    and declared_probe.get("status") == "verified"
                    and declared_probe.get("inventory_complete") is True
                ):
                    page_detection = {
                        "method": "verified_declared_inventory",
                        "provider": "generic",
                        "url": job_list_url,
                        "endpoint_url": declared_probe.get("endpoint_url"),
                        "inventory_complete": True,
                        "inventory_count": declared_probe.get("inventory_count"),
                    }
                identified = self.provider_registry.board_for_page(landing_page, self.fetcher)
                if identified is not None:
                    adapter, board = identified
                    provider = adapter.name
                    page_detection = {
                        "method": "page_evidence",
                        "provider": provider,
                        "url": board.url,
                    }
        if adapter:
            if board:
                try:
                    adapter_result = adapter.list_jobs(
                        self.fetcher,
                        board,
                        JobQuery(title=target_title, location=target_location),
                    )
                except FetchError as exc:
                    if adapter.supports_listing:
                        failure_trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": [],
                            "candidates": [],
                            "errors": [{"url": job_list_url, "error": str(exc)}],
                        }
                        if page_detection is not None:
                            failure_trace["provider_detection"] = page_detection
                        return None, failure_trace, landing_page
                else:
                    if adapter_result.reason_code == "PROVIDER_VARIANT_UNSUPPORTED":
                        unsupported_trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": list(adapter_result.trace.get("api_urls", [])),
                            "candidates": [],
                            "adapter_trace": adapter_result.trace,
                            "inventory": {
                                "source": "native_adapter",
                                "status": "incomplete",
                                "scope": adapter_result.trace.get(
                                    "inventory_scope",
                                    adapter_result.inventory_scope,
                                ),
                                "complete": False,
                                "candidate_count": 0,
                                "strongest_title_score": 0,
                                "reason_code": adapter_result.reason_code,
                            },
                        }
                        if page_detection is not None:
                            unsupported_trace["provider_detection"] = page_detection
                        return None, unsupported_trace, landing_page
                    else:
                        inventory_scope = adapter_result.trace.get(
                            "inventory_scope",
                            adapter_result.inventory_scope,
                        )
                        adapter_errors = any(
                            isinstance(adapter_result.trace.get(key), list)
                            and bool(adapter_result.trace[key])
                            for key in ("errors", "page_errors")
                        )
                        inventory_complete = bool(
                            adapter_result.inventory_complete
                            and not adapter_result.retryable
                            and adapter_result.reason_code in {None, "EMPTY_PROVIDER_RESPONSE"}
                            and not adapter_errors
                        )
                        scored_titles = [
                            score_title_match(candidate.title, target_title)[0]
                            for candidate in adapter_result.candidates
                        ]
                        trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": list(adapter_result.trace.get("api_urls", [])),
                            "candidates": [],
                            "adapter_trace": adapter_result.trace,
                            "inventory": {
                                "source": "native_adapter",
                                "status": (
                                    "incomplete"
                                    if not inventory_complete
                                    else
                                    "verified"
                                    if adapter_result.candidates
                                    else "verified_filtered_empty"
                                    if (
                                        adapter_result.reason_code == "EMPTY_PROVIDER_RESPONSE"
                                        and inventory_scope == "title_filtered"
                                    )
                                    else "verified_empty"
                                    if adapter_result.reason_code == "EMPTY_PROVIDER_RESPONSE"
                                    else "incomplete"
                                ),
                                "scope": inventory_scope,
                                "complete": inventory_complete,
                                "candidate_count": len(adapter_result.candidates),
                                "strongest_title_score": max(scored_titles, default=0),
                                "reason_code": adapter_result.reason_code,
                            },
                        }
                        if page_detection is not None:
                            trace["provider_detection"] = page_detection
                        scored = []
                        acquired_brand_handoff = bool(
                            discovered_board is not None
                            and discovered_board.detection_method
                            == "acquired_brand_handoff"
                        )
                        if acquired_brand_handoff:
                            trace["title_policy"] = "exact_for_acquired_brand_handoff"
                        for candidate in adapter_result.candidates:
                            title_score, title_reasons = score_title_match(candidate.title, target_title)
                            if (
                                title_score < MIN_PROVIDER_TITLE_MATCH_SCORE
                                or not title_identity_matches(
                                    candidate.title,
                                    target_title,
                                )
                                or (
                                    acquired_brand_handoff
                                    and _title_token_sequence(candidate.title)
                                    != _title_token_sequence(target_title)
                                )
                            ):
                                continue
                            if _is_explicit_location_mismatch(
                                candidate.location,
                                target_location,
                            ):
                                trace.setdefault("rejected_candidates", []).append(
                                    {
                                        "url": candidate.url,
                                        "title": candidate.title,
                                        "location": candidate.location,
                                        "reason": "location_identity_mismatch",
                                    }
                                )
                                continue
                            location_score, location_reasons = score_location_match(
                                candidate.location,
                                target_location,
                            )
                            scored.append(
                                OpeningMatch(
                                    url=candidate.url,
                                    title=candidate.title,
                                    score=title_score + 100,
                                    provider=candidate.provider,
                                    reasons=(
                                        ["provider adapter result"]
                                        + title_reasons
                                        + location_reasons
                                    ),
                                    job_list_page_url=job_list_url,
                                    location_score=location_score,
                                    location=candidate.location,
                                    hiring_organization_name=(
                                        candidate.raw.get("hiring_organization_name")
                                        if isinstance(
                                            candidate.raw.get(
                                                "hiring_organization_name"
                                            ),
                                            str,
                                        )
                                        else None
                                    ),
                                )
                            )
                        scored.sort(
                            key=lambda candidate: (candidate.score, candidate.location_score),
                            reverse=True,
                        )
                        trace["candidates"] = [
                            {
                                "url": candidate.url,
                                "title": candidate.title,
                                "location": candidate.location,
                                "hiring_organization_name": (
                                    candidate.hiring_organization_name
                                ),
                                "score": candidate.score,
                                "reasons": candidate.reasons,
                            }
                            for candidate in scored[:8]
                        ]
                        return (scored[0] if scored else None), trace, landing_page

        api_requests = build_provider_api_requests(job_list_url, target_title)
        trace = {"provider": provider, "api_urls": [request.url for request in api_requests], "candidates": []}
        if page_detection is not None:
            trace["provider_detection"] = page_detection
        if page_detection and page_detection.get("error"):
            trace["errors"] = [
                {
                    "url": job_list_url,
                    "error": page_detection["error"],
                    "phase": "page_evidence",
                }
            ]
        successful_api_fetches = 0
        inventory_candidate_count = 0
        strongest_title_score = 0
        for api_request in api_requests:
            try:
                page = self.fetcher.fetch(api_request.url, data=api_request.data, headers=api_request.headers)
            except FetchError as exc:
                trace.setdefault("errors", []).append({"url": api_request.url, "error": str(exc)})
                continue
            successful_api_fetches += 1
            candidates = provider_api_candidates(provider, page.html, job_list_url)
            inventory_candidate_count += len(candidates)
            scored = []
            for title, url in candidates:
                title_score, title_reasons = score_title_match(title, target_title)
                strongest_title_score = max(strongest_title_score, title_score)
                if (
                    title_score < MIN_TITLE_MATCH_SCORE
                    or not title_identity_matches(title, target_title)
                ):
                    continue
                scored.append(
                    OpeningMatch(
                        url=url,
                        title=title,
                        score=title_score + 100,
                        provider=provider,
                        reasons=["provider API result"] + title_reasons,
                        job_list_page_url=job_list_url,
                    )
                )
            scored.sort(key=lambda candidate: candidate.score, reverse=True)
            trace["candidates"].extend(
                [
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                    for candidate in scored[:8]
                ]
            )
            if scored:
                trace["inventory"] = {
                    "source": "provider_api",
                    "status": "verified",
                    "candidate_count": inventory_candidate_count,
                    "strongest_title_score": strongest_title_score,
                }
                return scored[0], trace, landing_page
        if successful_api_fetches:
            trace["inventory"] = {
                "source": "provider_api",
                "status": "verified" if inventory_candidate_count else "verified_empty",
                "candidate_count": inventory_candidate_count,
                "strongest_title_score": strongest_title_score,
            }
        return None, trace, landing_page

    def _recognizes_listing_provider(self, url: str) -> bool:
        adapter = self.provider_registry.adapter_for(url)
        return bool(
            adapter is not None
            and adapter.supports_listing
            and adapter.identify_board(url) is not None
        )

    def _provider_board_identity(self, url: str) -> tuple[str, str] | None:
        adapter = self.provider_registry.adapter_for(url)
        board = adapter.identify_board(url) if adapter is not None else None
        if adapter is None or board is None or not adapter.supports_listing:
            return None
        return adapter.name, board.url


def build_search_form_urls(page: Page, target_title: str) -> list[str]:
    """Build bounded, same-host GET searches declared by a public listing page."""

    return [
        action.request_url(query.value)
        for action in discover_job_search_actions(page).actions
        for query in title_search_queries(target_title)
    ]


def _build_search_plan(
    job_list_url: str,
    target_title: str,
    landing_page: Page | None,
    *,
    declared_actions: tuple[JobSearchAction, ...] = (),
    interactive_actions: tuple[JobSearchInteraction, ...] = (),
    declared_search_routes: tuple[tuple[str, TitleSearchQuery], ...] = (),
    title_queries: tuple[TitleSearchQuery, ...] = (),
) -> list[
    tuple[
        str,
        Page | None,
        str,
        JobSearchInteraction | None,
        TitleSearchQuery | None,
        JobSearchAction | None,
    ]
]:
    plan: list[
        tuple[
            str,
            Page | None,
            str,
            JobSearchInteraction | None,
            TitleSearchQuery | None,
            JobSearchAction | None,
        ]
    ] = []
    seen: set[tuple[str, str]] = set()

    def append(
        url: str,
        page: Page | None,
        source: str,
        interaction: JobSearchInteraction | None = None,
        query: TitleSearchQuery | None = None,
        action: JobSearchAction | None = None,
    ) -> None:
        normalized = safe_normalize_url(url)
        transport_identity = (
            f"{action.method.upper()}:{query.value}"
            if action is not None and query is not None
            else interaction.fingerprint()
            if interaction is not None
            else "plain"
        )
        identity = (
            normalized or "",
            transport_identity,
        )
        if not normalized or identity in seen:
            return
        seen.add(identity)
        plan.append((url, page, source, interaction, query, action))

    if landing_page is not None:
        append(job_list_url, landing_page, "reused_landing_page")
        final_url = landing_page.final_url or landing_page.url
        final_normalized = safe_normalize_url(final_url)
        if final_normalized:
            seen.add((final_normalized, "plain"))
        actions = declared_actions or discover_job_search_actions(landing_page).actions
        for action in actions:
            for query in title_queries or title_search_queries(target_title):
                append(
                    action.request_url(query.value),
                    None,
                    action.source,
                    query=query,
                    action=action,
                )
        for interaction in interactive_actions[:1]:
            for query in title_queries or title_search_queries(target_title):
                append(job_list_url, None, "interactive_job_search",
                       replace(interaction, target_title=query.value), query)
        for route_url, query in declared_search_routes:
            append(route_url, None, "declared_search_route", query=query)

    for search_url in build_provider_search_urls(job_list_url, target_title):
        append(search_url, None, "provider_fallback")
    return plan


def _interaction_trace(interaction: JobSearchInteraction) -> dict[str, object]:
    return {
        "form_ordinal": interaction.form_ordinal,
        "query_name": interaction.query_name,
        "query_id": interaction.query_id,
        "query_placeholder": interaction.query_placeholder,
        "target_title": interaction.target_title,
        "submit_text": interaction.submit_text,
        "submit_tag": interaction.submit_tag,
        "fingerprint": interaction.fingerprint(),
    }


def _record_interactive_failure(
    trace: dict,
    url: str,
    disposition: str,
    error: str,
    *,
    detail_reason_code: str | None = None,
    retryable: bool | None = None,
) -> None:
    failure = {
        "disposition": disposition,
        "url": url,
        "reason_code": "OPENING_DISCOVERY_INCOMPLETE",
        "error": error,
    }
    if detail_reason_code:
        failure["detail_reason_code"] = detail_reason_code
    if retryable is not None:
        failure["retryable"] = retryable
    interaction_trace = trace.setdefault("interactive_search", {})
    interaction_trace.update(failure)
    trace.setdefault("errors", []).append(
        {
            "url": url,
            "error": error,
            "phase": "interactive_job_search",
            "reason_code": "OPENING_DISCOVERY_INCOMPLETE",
            **(
                {"detail_reason_code": detail_reason_code}
                if detail_reason_code
                else {}
            ),
        }
    )


def detect_provider(url: str) -> str:
    return DEFAULT_PROVIDER_REGISTRY.detect(url)


def score_location_match(
    candidate_location: str | None,
    target_location: str | None,
) -> tuple[int, list[str]]:
    """Use location only to break title ties; missing location never rejects a job."""

    if not candidate_location or not target_location:
        return 0, []
    candidate_normalized = " ".join(re.findall(r"[a-z0-9]+", candidate_location.casefold()))
    target_normalized = " ".join(re.findall(r"[a-z0-9]+", target_location.casefold()))
    if not candidate_normalized or not target_normalized:
        return 0, []

    score = 0
    reasons: list[str] = []
    if candidate_normalized == target_normalized:
        score += 20
        reasons.append("exact location match")
    else:
        overlap = set(candidate_normalized.split()) & set(target_normalized.split())
        if overlap:
            score += min(12, 4 * len(overlap))
            reasons.append("location token overlap")

    candidate_region = location_region(candidate_location)
    target_region = location_region(target_location)
    if candidate_region and candidate_region == target_region:
        score += 8
        reasons.append(f"location region match '{target_region}'")
    return score, reasons


def build_provider_search_urls(job_list_url: str, target_title: str) -> list[str]:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    if provider == "google_careers":
        return [
            f"https://www.google.com/about/careers/applications/jobs/results/?q={query}",
            job_list_url,
        ]
    if provider == "meta_careers":
        return [
            f"https://www.metacareers.com/jobs/?q={query}",
            job_list_url,
        ]
    if provider in {"greenhouse", "lever", "ashby"}:
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "workable":
        return [job_list_url, add_query_params(job_list_url, {"query": target_title})]
    if provider == "smartrecruiters":
        return [job_list_url, add_query_params(job_list_url, {"search": target_title})]
    if provider == "icims":
        return [
            job_list_url,
            add_query_params(job_list_url, {"ss": "1", "searchKeyword": target_title}),
        ]
    if provider == "workday":
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "successfactors":
        return [
            job_list_url,
            add_query_params(job_list_url, {"q": target_title}),
            add_query_params(job_list_url, {"keyword": target_title}),
        ]
    if provider == "rippling":
        return [job_list_url]
    if provider == "bamboohr":
        return [job_list_url]
    return [
        job_list_url,
        add_query_params(job_list_url, {"q": target_title}),
        add_query_params(job_list_url, {"search": target_title}),
        add_query_params(job_list_url, {"query": target_title}),
    ]


def _is_title_filtered_fallback(url: str, target_title: str) -> bool:
    try:
        values = [
            value
            for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
            if key.casefold() in {"q", "query", "search"}
        ]
    except (TypeError, ValueError):
        return False
    normalized_target = " ".join(target_title.casefold().split())
    return bool(
        normalized_target
        and len(values) == 1
        and " ".join(values[0].casefold().split()) == normalized_target
    )


def build_provider_api_urls(job_list_url: str) -> list[str]:
    return [request.url for request in build_provider_api_requests(job_list_url)]


def build_provider_api_requests(job_list_url: str, target_title: str | None = None) -> list[ProviderApiRequest]:
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if provider == "greenhouse" and parts:
        board = parts[0]
        return [ProviderApiRequest(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")]
    if provider == "lever" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.lever.co/v0/postings/{company}?mode=json")]
    if provider == "smartrecruiters" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")]
    if provider == "ashby":
        board = _ashby_board_name(job_list_url)
        if board:
            return [ProviderApiRequest(f"https://api.ashbyhq.com/posting-api/job-board/{board}")]
    if provider == "workday":
        workday_api_url = build_workday_api_url(job_list_url)
        if workday_api_url:
            payload = {
                "appliedFacets": {},
                "limit": 50,
                "offset": 0,
                "searchText": target_title or "",
            }
            return [ProviderApiRequest(workday_api_url, data=json.dumps(payload).encode("utf-8"))]
    if provider == "bamboohr":
        return [ProviderApiRequest(_bamboohr_jobs_api_url(job_list_url))]
    return []


def provider_api_candidates(provider: str, body: str, job_list_url: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if provider == "greenhouse":
        return [
            (str(job.get("title") or ""), str(job.get("absolute_url") or ""))
            for job in data.get("jobs", [])
            if job.get("title") and job.get("absolute_url")
        ]
    if provider == "lever" and isinstance(data, list):
        return [
            (str(job.get("text") or ""), str(job.get("hostedUrl") or job.get("applyUrl") or ""))
            for job in data
            if job.get("text") and (job.get("hostedUrl") or job.get("applyUrl"))
        ]
    if provider == "smartrecruiters":
        candidates = []
        for job in data.get("content", []):
            title = str(job.get("name") or "")
            url = _smartrecruiters_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "workday":
        candidates = []
        for job in data.get("jobPostings", []):
            title = str(job.get("title") or "")
            url = _workday_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "ashby":
        candidates = []
        for job in data.get("jobs", []):
            title = str(job.get("title") or "")
            url = _ashby_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "bamboohr":
        candidates = []
        for job in data.get("result", []):
            title = str(job.get("jobOpeningName") or "")
            url = _bamboohr_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    return []


def _opening_candidates_from_links(
    links: list[RawLink],
    *,
    page_url: str,
    target_title: str,
    target_location: str | None,
    provider: str,
    excluded_urls: tuple[str, ...] = (),
) -> list[OpeningMatch]:
    candidates: list[OpeningMatch] = []
    excluded_identities = {
        identity
        for identity in (
            safe_normalize_url(url) for url in (page_url, *excluded_urls)
        )
        if identity
    }
    for link in dedupe_raw_links(links):
        evidence_page_url = link.source_url or page_url
        validated_url = validate_output_url(
            link.url,
            evidence_page_url,
            title=link.text,
            origin=link.origin,
        )
        if not validated_url:
            continue
        if safe_normalize_url(validated_url) in excluded_identities:
            continue
        link = RawLink(
            validated_url,
            link.text,
            link.source_url,
            link.origin,
            link.location,
        )
        scored = score_job_link(link, evidence_page_url)
        verified_detail_shape = is_likely_job_detail(scored)
        title_score, title_reasons = score_title_match(link.text, target_title)
        if (
            title_score < MIN_TITLE_MATCH_SCORE
            or not title_identity_matches(
                link.text,
                target_title,
                target_location=target_location,
            )
        ):
            continue
        if _is_explicit_location_mismatch(link.location, target_location):
            continue
        if (
            link.origin == "verified_declared_inventory"
            and link.location
            and target_location
            and not _strict_location_identity_matches(
                link.location,
                target_location,
            )
            and not _title_location_identity_matches(
                link.text,
                target_location,
            )
        ):
            continue
        total_score = scored.score + title_score
        location_score, location_reasons = score_location_match(
            link.location,
            target_location,
        )
        url_location_score, url_location_reasons = _score_url_location_tiebreaker(
            link.url,
            target_location,
        )
        location_score += url_location_score
        reasons = (
            scored.reasons
            + title_reasons
            + location_reasons
            + url_location_reasons
            + [f"listing origin: {link.origin}"]
        )
        if not verified_detail_shape and title_score < 60:
            continue
        if total_score < 70 and not (
            verified_detail_shape and title_score >= MIN_PROVIDER_TITLE_MATCH_SCORE
        ):
            continue
        candidates.append(
            OpeningMatch(
                url=link.url,
                title=link.text,
                score=total_score,
                provider=provider,
                reasons=reasons,
                job_list_page_url=page_url,
                location_score=location_score,
                location=link.location,
            )
        )
    candidates.sort(
        key=lambda candidate: (candidate.score, candidate.location_score),
        reverse=True,
    )
    return candidates


def _score_url_location_tiebreaker(
    opening_url: str,
    target_location: str | None,
) -> tuple[int, list[str]]:
    if not target_location:
        return 0, []
    path_tokens = set(re.findall(r"[a-z0-9]+", urlparse(opening_url).path.casefold()))
    target_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", target_location.casefold())
        if token not in {"area", "greater", "metropolitan", "metro", "united", "states"}
    }
    overlap = path_tokens & target_tokens
    if not overlap:
        return 0, []
    return min(12, 4 * len(overlap)), ["opening URL location token overlap"]


def _record_candidates(trace: dict, candidates: list[OpeningMatch]) -> None:
    trace["candidates"].extend(
        {
            "url": candidate.url,
            "title": candidate.title,
            "location": candidate.location,
            "score": candidate.score,
            "reasons": candidate.reasons,
            "hiring_organization_name": candidate.hiring_organization_name,
        }
        for candidate in candidates[:8]
    )


def _selected_candidate_trace(candidate: OpeningMatch) -> dict:
    return {
        "url": candidate.url,
        "title": candidate.title,
        "location": candidate.location,
        "score": candidate.score,
        "reasons": candidate.reasons,
        "hiring_organization_name": candidate.hiring_organization_name,
    }


def structured_job_links(
    html: str,
    source_url: str,
    *,
    trusted_declared_inventory: bool = False,
) -> list[RawLink]:
    links: list[RawLink] = []
    for script_attrs, script_body in _script_blocks(html):
        if "application/ld+json" not in script_attrs.lower():
            continue
        try:
            data = json.loads(unescape(script_body.strip()))
        except json.JSONDecodeError:
            continue
        for job in _walk_json_ld_jobs(data):
            title = str(job.get("title") or job.get("name") or "").strip()
            url = _json_ld_url(job)
            location = _json_ld_location(job)
            normalized = safe_normalize_url(url, source_url) if url else None
            if title and normalized:
                links.append(
                    RawLink(
                        url=normalized,
                        text=title,
                        source_url=source_url,
                        location=location,
                    )
                )
    for script_attrs, script_body in _script_blocks(html):
        if not _looks_like_json_script(script_attrs, script_body):
            continue
        data = _parse_script_json(script_body)
        if data is None:
            continue
        if trusted_declared_inventory:
            declared = (
                data.get("first_party_declared_inventory")
                if isinstance(data, dict)
                else None
            )
            if isinstance(declared, dict) and isinstance(declared.get("jobs"), list):
                for title, url, location in _walk_structured_job_records(
                    declared["jobs"],
                    source_url,
                ):
                    links.append(
                        RawLink(
                            url=url,
                            text=title,
                            source_url=source_url,
                            origin="verified_declared_inventory",
                            location=location,
                        )
                    )
                continue
        for title, url, location in _walk_structured_job_records(data, source_url):
            links.append(
                RawLink(
                    url=url,
                    text=title,
                    source_url=source_url,
                    location=location,
                )
            )
    links.extend(candidate.as_raw_link() for candidate in extract_listing_candidates(html, source_url))
    return dedupe_raw_links(links)


def _script_blocks(html: str) -> list[tuple[str, str]]:
    return [
        (attrs, unescape(body.strip()))
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.I | re.S)
        if body.strip()
    ]


def _looks_like_json_script(attrs: str, body: str) -> bool:
    attrs_lower = attrs.lower()
    body_stripped = body.strip()
    return (
        "application/json" in attrs_lower
        or "application/ld+json" in attrs_lower
        or body_stripped.startswith("{")
        or body_stripped.startswith("[")
    )


def _parse_script_json(body: str):
    text = body.strip()
    if text.startswith("<![CDATA["):
        text = text.removeprefix("<![CDATA[").removesuffix("]]>").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_json_ld_jobs(value):
    if isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(kind).lower() == "jobposting" for kind in types):
            yield value
        for child in value.values():
            yield from _walk_json_ld_jobs(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_ld_jobs(item)


def _json_ld_url(job: dict) -> str:
    raw_url = job.get("url") or job.get("sameAs")
    if isinstance(raw_url, list):
        raw_url = raw_url[0] if raw_url else ""
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url") or ""
    return str(raw_url or "")


def _json_ld_location(job: dict) -> str | None:
    locations = job.get("jobLocation")
    if not isinstance(locations, list):
        locations = [locations]
    parts: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        locality = address.get("addressLocality")
        region = address.get("addressRegion")
        value = ", ".join(
            item.strip()
            for item in (locality, region)
            if isinstance(item, str) and item.strip()
        )
        if value:
            parts.append(value)
    if parts:
        return "; ".join(dict.fromkeys(parts))
    if str(job.get("jobLocationType") or "").casefold() != "telecommute":
        return None
    requirements = job.get("applicantLocationRequirements")
    if not isinstance(requirements, list):
        requirements = [requirements]
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        name = requirement.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        address = requirement.get("address")
        if isinstance(address, dict):
            country = address.get("addressCountry")
            if isinstance(country, str) and country.strip():
                return country.strip()
    description = job.get("description")
    if not isinstance(description, str) or len(description) > 500_000:
        return None
    visible = " ".join(unescape(re.sub(r"<[^>]+>", " ", description)).split())
    match = re.search(
        r"\bapplicants? must be (?:based|located) in (?:the )?"
        r"([a-z][a-z .'-]{1,60}?)(?=[.;]|$)",
        visible,
        re.I,
    )
    return match.group(1).strip() if match else None


def _strict_json_ld_job_postings(html: str, source_url: str) -> list[dict[str, str | None]]:
    postings: list[dict[str, str | None]] = []
    for script_attrs, script_body in _script_blocks(html):
        if "application/ld+json" not in script_attrs.casefold():
            continue
        try:
            payload = json.loads(unescape(script_body.strip()))
        except (json.JSONDecodeError, TypeError):
            continue
        for job in _walk_json_ld_jobs(payload):
            title = job.get("title") or job.get("name")
            # Some first-party detail pages omit JobPosting.url. The current
            # page remains a safe candidate because the caller later requires
            # the normalized posting URL to equal this fetched page exactly.
            url = safe_normalize_url(_json_ld_url(job) or source_url, source_url)
            organization = job.get("hiringOrganization")
            if not isinstance(title, str) or not title.strip() or not url:
                continue
            if not isinstance(organization, dict):
                organization = {}
            organization_name = organization.get("name")
            organization_url = organization.get("url") or organization.get("sameAs")
            if isinstance(organization_url, list):
                organization_url = organization_url[0] if organization_url else None
            if isinstance(organization_url, dict):
                organization_url = organization_url.get("url") or organization_url.get("@id")
            postings.append(
                {
                    "url": url,
                    "title": title.strip(),
                    "location": _json_ld_location(job),
                    "hiring_organization_name": (
                        organization_name.strip()
                        if isinstance(organization_name, str) and organization_name.strip()
                        else None
                    ),
                    "hiring_organization_url": (
                        safe_normalize_url(organization_url, source_url)
                        if isinstance(organization_url, str)
                        else None
                    ),
                    "hiring_organization_url_present": bool(organization_url),
                }
            )
    return postings


def _strict_embedded_job_detail_postings(
    html: str,
    source_url: str,
) -> list[dict[str, str | None]]:
    """Read a page-bound public job record from a bounded controller cache."""

    if not isinstance(html, str) or len(html) > 2_000_000:
        return []
    parsed = urlparse(source_url)
    page_ids = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.casefold() in {"id", "jobid", "job_id", "requisitionid"}
        and value
    ]
    if len(set(page_ids)) != 1:
        return []
    page_id = page_ids[0]
    marker = re.search(
        r"\bwindow\.ASYNC_DATA_CONTROLLER_CACHE\s*=\s*",
        html,
        re.IGNORECASE,
    )
    if marker is None:
        return []
    try:
        payload, _end = json.JSONDecoder().raw_decode(html, marker.end())
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(payload, dict) or len(payload) > 100:
        return []

    records: list[dict] = []
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        body = data.get("body") if isinstance(data, dict) else None
        if isinstance(body, dict) and str(body.get("id") or "") == page_id:
            records.append(body)
    if len(records) != 1:
        return []
    record = records[0]
    title = record.get("title")
    organization_name = record.get("jobPostingSite")
    offices = record.get("offices")
    locations = []
    if isinstance(offices, list) and len(offices) <= 25:
        for office in offices:
            location = office.get("location") if isinstance(office, dict) else None
            if isinstance(location, str) and location.strip():
                locations.append(location.strip())
    if not locations:
        primary = record.get("primary_location")
        if isinstance(primary, str) and primary.strip():
            locations.append(primary.strip())
    canonical_page = safe_normalize_url(source_url)
    if (
        not isinstance(title, str)
        or not title.strip()
        or not locations
        or not canonical_page
    ):
        return []
    return [
        {
            "url": canonical_page,
            "title": title.strip(),
            "location": "; ".join(dict.fromkeys(locations)),
            "hiring_organization_name": (
                organization_name.strip()
                if isinstance(organization_name, str) and organization_name.strip()
                else None
            ),
            "hiring_organization_url": canonical_page,
            "hiring_organization_url_present": True,
        }
    ]


def _strict_page_bound_detail_postings(
    html: str,
    source_url: str,
) -> list[dict[str, object]]:
    return [
        {
            "url": candidate.url,
            "title": candidate.title,
            "location": candidate.location,
            "hiring_organization_name": None,
            "hiring_organization_url": None,
            "hiring_organization_url_present": False,
        }
        for candidate in extract_detail_page_candidates(html, source_url)
    ]


def _listing_detail_hiring_organization_matches(
    posting: dict[str, object],
    expected_domain: str,
) -> bool:
    organization_url = posting.get("hiring_organization_url")
    if organization_url:
        return _same_site_hiring_organization(str(organization_url), expected_domain)
    return not bool(posting.get("hiring_organization_url_present"))


def _same_site_hiring_organization(
    organization_url: str | None,
    expected_domain: str,
) -> bool:
    organization_domain = domain_of(organization_url or "")
    return bool(
        expected_domain
        and organization_domain
        and _registrable_site(organization_domain)
        == _registrable_site(expected_domain)
    )


def _registrable_site(host: str) -> str:
    labels = host.casefold().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    two_level_suffixes = {"co.jp", "co.nz", "co.uk", "com.au", "com.br", "com.sg"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in two_level_suffixes else suffix


def _page_indicates_closed_opening(html: str) -> bool:
    visible = re.sub(r"<[^>]+>", " ", html).casefold()
    visible = " ".join(unescape(visible).split())
    return any(
        marker in visible
        for marker in (
            "job you are trying to apply for does not exist",
            "job is no longer available",
            "job is no longer accepting applications",
            "position is no longer available",
            "position has been filled",
            "this job has expired",
        )
    )


def _strict_location_identity_matches(
    candidate_location: str | None,
    target_location: str,
) -> bool:
    if not candidate_location:
        return False
    candidate_normalized = " ".join(
        re.findall(r"[a-z0-9]+", candidate_location.casefold())
    )
    target_normalized = " ".join(re.findall(r"[a-z0-9]+", target_location.casefold()))
    if candidate_normalized and candidate_normalized == target_normalized:
        return True
    target_parts = [
        set(re.findall(r"[a-z0-9]+", part.casefold()))
        for part in target_location.split(",")
    ]
    target_parts = [part for part in target_parts if part]
    if not target_parts:
        return False
    for option in _location_options(candidate_location):
        candidate_parts = [
            set(re.findall(r"[a-z0-9]+", part.casefold()))
            for part in option.split(",")
        ]
        candidate_parts = [part for part in candidate_parts if part]
        if not candidate_parts:
            continue
        if len(target_parts) >= 2 and len(candidate_parts) >= 2:
            if candidate_parts[0].intersection(target_parts[0]):
                return True
            continue
        ignored = {"united", "states", "usa", "us"}
        candidate = set().union(*candidate_parts) - ignored
        target = set().union(*target_parts) - ignored
        if candidate and target and candidate.intersection(target):
            return True
    return False


def _title_location_identity_matches(
    candidate_title: str,
    target_location: str,
) -> bool:
    from .opening_selection_validation import _target_state_code

    qualifiers = re.findall(
        r"(?:,|\s+-\s+|\()\s*([^,()]{1,80})\)?(?:\s*$|,)",
        candidate_title,
    )
    if not qualifiers:
        return False
    target_state = _target_state_code(target_location)
    target_city = _location_city_tokens(target_location, target_state)
    for qualifier in qualifiers:
        normalized = " ".join(re.findall(r"[a-z0-9]+", qualifier.casefold()))
        aliases = {
            "nyc": {"new", "york"},
            "new york city": {"new", "york"},
            "dc": {"washington"},
            "d c": {"washington"},
        }
        tokens = aliases.get(normalized, set(normalized.split()))
        if target_city and target_city.issubset(tokens):
            return True
        if target_state and target_state.casefold() in tokens:
            return True
    return False


def _is_explicit_location_mismatch(
    candidate_location: str | None,
    target_location: str | None,
) -> bool:
    """Reject explicit conflicts while keeping missing, remote, and multi-site evidence."""

    if not candidate_location or not target_location:
        return False
    normalized = " ".join(candidate_location.casefold().split())
    if any(
        marker in normalized
        for marker in (
            "multiple location",
            "multiple locations",
            "various location",
            "various locations",
            "remote",
            "nationwide",
        )
    ):
        return False
    options = _location_options(candidate_location)
    if len(options) > 1:
        return all(
            _is_explicit_location_mismatch(option, target_location)
            for option in options
        )
    # Import lazily because the final identity gate already depends on this module.
    from .opening_selection_validation import _target_state_code

    candidate_state = _target_state_code(candidate_location)
    target_state = _target_state_code(target_location)
    if candidate_state and target_state and candidate_state != target_state:
        return True

    candidate_city = _location_city_tokens(candidate_location, candidate_state)
    target_city = _location_city_tokens(target_location, target_state)
    ignored = {
        "area",
        "greater",
        "metro",
        "metropolitan",
        "state",
        "united",
        "states",
        "usa",
        "us",
    }
    candidate_city -= ignored
    target_city -= ignored
    if not candidate_city or not target_city or candidate_city & target_city:
        return False
    if (
        candidate_state
        and candidate_state == target_state
        and _looks_like_opaque_facility_label(candidate_location)
    ):
        return False
    return bool(
        (candidate_state and target_state)
        or ("," in candidate_location and "," in target_location)
    )


def _location_options(location: str) -> list[str]:
    return [
        option.strip()
        for option in re.split(r"(?:\s*[;|\n]\s*|\s+\+\s+)", location)
        if option.strip()
    ]


def _location_city_tokens(location: str, state_code: str | None) -> set[str]:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) >= 2:
        country_tokens = {"us", "usa", "united states", "united states of america"}
        if parts[-1].casefold() in country_tokens:
            parts.pop()
        city_part = parts[-2] if len(parts) >= 2 else parts[0]
    else:
        city_part = parts[0] if parts else location
    tokens = set(re.findall(r"[a-z0-9]+", city_part.casefold()))
    if state_code:
        tokens.discard(state_code.casefold())
    return tokens


def _looks_like_opaque_facility_label(location: str) -> bool:
    return "," not in location and bool(re.match(r"^\s*[a-z]\s+", location, re.I))


STRUCTURED_TITLE_FIELDS = ("title", "name", "jobTitle", "job_title", "text")
STRUCTURED_LOCATION_FIELDS = (
    "location",
    "locationName",
    "jobLocation",
    "job_location",
)
STRUCTURED_URL_FIELDS = (
    "url",
    "absolute_url",
    "absoluteUrl",
    "hostedUrl",
    "applyUrl",
    "jobUrl",
    "job_url",
    "externalPath",
    "detailUrl",
    "link",
)


def _walk_structured_job_records(value, source_url: str):
    if isinstance(value, dict):
        title = _first_text_field(value, STRUCTURED_TITLE_FIELDS)
        url = _structured_record_url(value, source_url, title)
        if title and url and _looks_like_structured_job_record(value, url, source_url, title):
            yield title, url, _first_text_field(value, STRUCTURED_LOCATION_FIELDS) or None
        for child in value.values():
            yield from _walk_structured_job_records(child, source_url)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_structured_job_records(item, source_url)


def _first_text_field(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _structured_record_url(record: dict, source_url: str, title: str) -> str | None:
    raw_url = _first_text_field(record, STRUCTURED_URL_FIELDS)
    provider = detect_provider(source_url)
    if raw_url:
        normalized = safe_normalize_url(raw_url, source_url)
        if normalized:
            return normalized

    if provider == "successfactors":
        job_req_id = _first_text_field(record, ("career_job_req_id", "jobReqId", "job_req_id", "id"))
        if job_req_id:
            return add_query_params(source_url, {"career_ns": "job_listing", "career_job_req_id": job_req_id})

    if provider == "icims":
        job_id = _first_text_field(record, ("id", "jobId", "job_id", "jobNumber"))
        if job_id and title:
            return safe_normalize_url(f"/jobs/{job_id}/{_slugify_title(title)}/job", source_url)

    if provider == "ashby":
        job_id = _first_text_field(record, ("id", "jobId", "job_id"))
        board = _ashby_board_name(source_url)
        if job_id and board:
            return f"https://jobs.ashbyhq.com/{board}/{job_id}"

    if provider == "workable":
        short_code = _first_text_field(record, ("shortcode", "shortCode", "code", "id"))
        account = _workable_account_name(source_url)
        if short_code and account:
            return f"https://apply.workable.com/{account}/j/{short_code}/"

    return None


def _looks_like_structured_job_record(
    record: dict,
    url: str,
    source_url: str,
    title: str,
) -> bool:
    keys = " ".join(str(key).lower() for key in record)
    query = urlparse(url).query.lower()
    candidate = score_job_link(RawLink(url=url, text=title, source_url=source_url), source_url)
    reason_text = " ".join(candidate.reasons)
    return (
        is_likely_job_detail(candidate)
        or "ATS job detail pattern" in reason_text
        or "career_job_req_id" in query
        or "jobreqid" in query
        or ("job" in keys and detect_provider(url) != "generic" and candidate.score >= 90)
    )


def _slugify_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def dedupe_raw_links(links: list[RawLink]) -> list[RawLink]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RawLink] = []
    for link in links:
        key = (link.url.rstrip("/"), link.text.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(link)
    return deduped


def _smartrecruiters_job_url(job: dict, job_list_url: str) -> str:
    actions = job.get("actions") or {}
    if actions.get("details"):
        return str(actions["details"])
    ref = job.get("ref")
    if ref:
        return str(ref)
    job_id = job.get("id")
    if not job_id:
        return ""
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    return f"https://jobs.smartrecruiters.com/{parts[0]}/{job_id}"


def _ashby_board_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()
    if host == "jobs.ashbyhq.com" and parts:
        return parts[0]
    if host.endswith(".ashbyhq.com") and host not in {"api.ashbyhq.com", "jobs.ashbyhq.com"}:
        return host.split(".", 1)[0]
    return ""


def _ashby_job_url(job: dict, job_list_url: str) -> str:
    raw_url = str(job.get("jobUrl") or job.get("hostedUrl") or job.get("url") or "")
    if raw_url:
        normalized = safe_normalize_url(raw_url, job_list_url)
        if normalized:
            return normalized
    job_id = str(job.get("id") or "")
    board = _ashby_board_name(job_list_url)
    if job_id and board:
        return f"https://jobs.ashbyhq.com/{board}/{job_id}"
    return ""


def _workable_account_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() == "apply.workable.com" and parts:
        return parts[0]
    return ""


def build_workday_api_url(job_list_url: str) -> str | None:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    site = parts[-1]
    tenant = parsed.netloc.split(".", 1)[0]
    if not tenant or not site:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}/jobs"


def _workday_job_url(job: dict, job_list_url: str) -> str:
    external_path = str(job.get("externalPath") or "")
    if not external_path:
        return ""
    if external_path.startswith("http"):
        return external_path
    parsed = urlparse(job_list_url)
    board_path = parsed.path.rstrip("/")
    if not external_path.startswith("/"):
        external_path = "/" + external_path
    return f"{parsed.scheme}://{parsed.netloc}{board_path}{external_path}"


def _bamboohr_jobs_api_url(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/list"


def _bamboohr_job_url(job: dict, job_list_url: str) -> str:
    job_id = str(job.get("id") or "")
    if not job_id:
        return ""
    parsed = urlparse(job_list_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/{job_id}"


def build_search_result_url(job_list_url: str, target_title: str) -> str | None:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    if provider == "google_careers":
        return f"https://www.google.com/about/careers/applications/jobs/results/?q={query}"
    if provider == "meta_careers":
        return f"https://www.metacareers.com/jobs/?q={query}"
    if provider in {
        "lever",
        "greenhouse",
        "ashby",
    }:
        return job_list_url
    if provider in {"workable", "smartrecruiters", "icims", "workday", "successfactors", "rippling", "bamboohr"}:
        return build_provider_search_urls(job_list_url, target_title)[-1]
    return None


def add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def score_title_match(candidate_title: str, target_title: str) -> tuple[int, list[str]]:
    candidate_tokens = _tokens(candidate_title)
    target_tokens = _tokens(target_title)
    if not candidate_tokens or not target_tokens:
        return 0, []

    overlap = candidate_tokens & target_tokens
    recall = len(overlap) / len(target_tokens)
    precision = len(overlap) / len(candidate_tokens)
    score = int((recall * 70) + (precision * 30))
    reasons = []
    if overlap:
        reasons.append(f"title token overlap: {', '.join(sorted(overlap))}")
    if candidate_title.strip().lower() == target_title.strip().lower():
        score += 50
        reasons.append("exact title match")
    return score, reasons


def title_identity_matches(
    candidate_title: str,
    target_title: str,
    *,
    target_location: str | None = None,
) -> bool:
    """Require a title-shaped candidate while preserving level tokens."""

    candidate = _title_token_sequence(candidate_title)
    target = _title_token_sequence(target_title)
    if not candidate or not target:
        return False
    if len(candidate) == len(target) and sorted(candidate) == sorted(target):
        return True
    if len(target) == 1:
        return False
    target_index = 0
    for token in candidate:
        if token == target[target_index]:
            target_index += 1
            if target_index == len(target):
                return True
    return False


def publication_title_identity_matches(
    candidate_title: str,
    target_title: str,
    *,
    target_location: str | None = None,
) -> bool:
    """Require the same normalized role before an opening can be published."""

    candidate = _publication_title_token_sequence(candidate_title)
    target = _publication_title_token_sequence(target_title)
    if not candidate or not target:
        return False
    if sorted(candidate) == sorted(target):
        return True
    if len(candidate) > len(target) and candidate[: len(target)] == target:
        suffix = candidate[len(target) :]
        listing_metadata = {
            "commercial",
            "contract",
            "full",
            "hybrid",
            "location",
            "locations",
            "multiple",
            "part",
            "remote",
            "time",
        }
        if suffix and all(token in listing_metadata for token in suffix):
            return True
    return bool(
        target_location
        and candidate[: len(target)] == target
        and _title_location_identity_matches(candidate_title, target_location)
    )


def _title_token_sequence(text: str) -> list[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    aliases = {"sr": "senior", "jr": "junior"}
    return [
        aliases.get(token, token)
        for token in normalized.split()
        if len(token) >= 2 and token not in STOPWORDS
    ]


def _publication_title_token_sequence(text: str) -> list[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    aliases = {
        "sr": "senior",
        "jr": "junior",
        "i": "level1",
        "1": "level1",
        "ii": "level2",
        "2": "level2",
        "iii": "level3",
        "3": "level3",
        "iv": "level4",
        "4": "level4",
    }
    return [
        aliases.get(token, token)
        for token in normalized.split()
        if token in aliases or (len(token) >= 2 and token not in STOPWORDS)
    ]


def _tokens(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {token for token in normalized.split() if len(token) >= 2 and token not in STOPWORDS}
