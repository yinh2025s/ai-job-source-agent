from __future__ import annotations

import json
import re
from base64 import urlsafe_b64decode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from xml.etree import ElementTree as ET

from .contracts import FetchClient
from .fetch_failure import project_fetch_error
from .homepage_navigation import HomepageNavigationEvidence, evidence_from_verified_homepage
from .identity_evidence import LinkedInWebsiteEvidenceStore
from .web import FetchError, Page, domain_of, normalize_url


SEARCH_ENDPOINT = "https://www.bing.com/search"
DUCKDUCKGO_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"

BLOCKED_DOMAINS = {
    "linkedin.com",
    "licdn.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "wikipedia.org",
    "crunchbase.com",
    "glassdoor.com",
    "indeed.com",
    "wellfound.com",
    "pitchbook.com",
    "zoominfo.com",
    "rocketreach.co",
    "github.com",
    "bing.com",
    "microsoft.com",
    "static.licdn.com",
    "media.licdn.com",
    "dms.licdn.com",
    "w3.org",
    "schema.org",
    "schemas.live.com",
    "storage.live.com",
    "challenges.cloudflare.com",
    "bit.ly",
    "l.ink",
    "my.site.com",
}

BLOCKED_DOMAIN_PARTS = (
    "linkedin.",
    "greenhouse.io",
    "lever.co",
    "workdayjobs.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
)

PARKED_DOMAIN_HOSTS = {
    "afternic.com",
    "atom.com",
    "dan.com",
    "godaddy.com",
    "hugedomains.com",
    "sedo.com",
}

HOSTED_NON_COMPANY_DOMAINS = {
    "bit.ly",
    "l.ink",
    "my.site.com",
}

PARKED_DOMAIN_TEXT_MARKERS = (
    "buy this domain",
    "domain is for sale",
    "domain marketplace",
    " for sale | spaceship.com",
    " is for sale on spaceship",
    "make an offer on this domain",
    "purchase this domain",
)

PARKED_DOMAIN_INFRASTRUCTURE_MARKERS = (
    "data-adblockkey=",
    "sedoparking.com",
    "iseaskies.com",
    "assets.squarespace.com/universal/scripts-compressed/parking-page-",
    "assets.squarespace.com/universal/styles-compressed/parking-page-",
)


@dataclass
class WebsiteCandidate:
    url: str
    score: int
    reasons: list[str] = field(default_factory=list)
    verified_page: Page | None = None


@dataclass(frozen=True)
class SearchEvidence:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class _LinkedInCompanyCandidates:
    official_urls: tuple[str, ...] = ()
    outbound_urls: tuple[str, ...] = ()
    official_source: str | None = None


class CompanyWebsiteResolver:
    def __init__(
        self,
        fetcher: FetchClient,
        overrides_path: str | Path | None = None,
        verify_limit: int = 3,
        linkedin_evidence_store: LinkedInWebsiteEvidenceStore | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.overrides = self._load_overrides(overrides_path)
        self.verify_limit = verify_limit
        self.linkedin_evidence_store = linkedin_evidence_store

    def resolve(
        self,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
        preferred_url: str | None = None,
    ) -> tuple[str | None, dict]:
        website_url, trace, _navigation_evidence = self.resolve_with_navigation_evidence(
            company_name,
            linkedin_company_url,
            job_location,
            preferred_url,
        )
        return website_url, trace

    def resolve_with_navigation_evidence(
        self,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
        preferred_url: str | None = None,
    ) -> tuple[str | None, dict, HomepageNavigationEvidence | None]:
        normalized_name = normalize_company_key(company_name)
        trace = {
            "company_name": company_name,
            "linkedin_company_url": linkedin_company_url,
            "job_location": job_location,
            "preferred_url": preferred_url,
            "target_region": location_region(job_location),
            "candidates": [],
            "fetch_errors": [],
        }
        fetch_errors = trace["fetch_errors"]

        if normalized_name in self.overrides:
            url = normalize_url(self.overrides[normalized_name])
            trace["selected"] = {"url": url, "reason": "override"}
            return url, trace, None

        guessed_candidates = self._guess_domain_candidates(company_name)

        preferred_candidates = [preferred_url] if preferred_url else []
        linkedin_official_candidates: list[str] = []
        linkedin_candidates: list[str] = []
        linkedin_official_source: str | None = None
        linkedin_evidence_loaded = False
        if (
            preferred_url
            or _linkedin_slug_uses_marketing_prefix(linkedin_company_url)
            or _company_name_loses_identity_separator(company_name)
        ) and linkedin_company_url:
            linkedin_evidence = self._linkedin_company_candidates(
                linkedin_company_url,
                company_name,
                fetch_errors=fetch_errors,
            )
            linkedin_official_candidates = list(linkedin_evidence.official_urls)
            linkedin_candidates = list(linkedin_evidence.outbound_urls)
            linkedin_official_source = linkedin_evidence.official_source
            if linkedin_official_source:
                trace["linkedin_official_evidence_source"] = linkedin_official_source
            linkedin_evidence_loaded = True
        fast_candidates = dedupe_urls(
            preferred_candidates
            + linkedin_official_candidates
            + self._linkedin_slug_domain_candidates(linkedin_company_url)
            + guessed_candidates[:6]
        )
        fast_sources = _candidate_source_map(
            ("preferred_input", preferred_candidates),
            ("linkedin_official_website", linkedin_official_candidates),
            (
                "linkedin_cached_official_website",
                linkedin_official_candidates if linkedin_official_source == "cache" else [],
            ),
            ("linkedin_slug", self._linkedin_slug_domain_candidates(linkedin_company_url)),
            ("speculative_guess", guessed_candidates[:6]),
        )
        fast_scored = self._rank_and_verify_candidates(
            fast_candidates,
            company_name,
            linkedin_company_url,
            job_location=job_location,
            candidate_sources=fast_sources,
            fetch_errors=fetch_errors,
        )
        fast_selected = self._select_verified_candidate(
            fast_scored,
            require_fast_confidence=True,
        )
        selectable_fast_domains = {
            domain_of(candidate.url)
            for candidate in fast_scored
            if "homepage verified" in candidate.reasons
            and self._select_verified_candidate([candidate])
        }
        fast_selection_defer_reason: str | None = None
        if fast_selected and linkedin_company_url and not linkedin_evidence_loaded:
            if len(selectable_fast_domains) >= 2:
                fast_selection_defer_reason = "multiple verified same-brand domains"
            elif _has_non_www_subdomain(fast_selected.url) and not _has_direct_identity_source(
                fast_selected
            ):
                fast_selection_defer_reason = "verified non-apex domain"
        fast_selection_deferred = fast_selection_defer_reason is not None
        if fast_selection_deferred:
            deferred_domains = set(selectable_fast_domains)
            if fast_selected:
                deferred_domains.add(domain_of(fast_selected.url))
            for candidate in fast_scored:
                if domain_of(candidate.url) in deferred_domains:
                    candidate.reasons.append(
                        "fast selection deferred for LinkedIn official evidence: "
                        f"{fast_selection_defer_reason}"
                    )
        trace["candidates"].extend(
            {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
            for candidate in fast_scored[:10]
        )
        if fast_selected and not fast_selection_deferred:
            trace["selected"] = {
                "url": fast_selected.url,
                "score": fast_selected.score,
                "reasons": fast_selected.reasons + ["fast verified domain"],
            }
            return (
                fast_selected.url,
                trace,
                self._navigation_evidence_for_selected(fast_selected),
            )

        regional_candidates = (
            []
            if fast_selection_deferred
            else _regional_root_candidates(fast_scored, job_location)
        )
        if regional_candidates:
            regional_sources = _candidate_source_map(("regional_recovery", regional_candidates))
            regional_scored = self._rank_and_verify_candidates(
                regional_candidates,
                company_name,
                linkedin_company_url,
                job_location=job_location,
                candidate_sources=regional_sources,
                fetch_errors=fetch_errors,
            )
            trace["candidates"].extend(
                {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
                for candidate in regional_scored[:5]
            )
            regional_selected = self._select_verified_candidate(regional_scored)
            if regional_selected:
                trace["selected"] = {
                    "url": regional_selected.url,
                    "score": regional_selected.score,
                    "reasons": regional_selected.reasons + ["verified regional root recovery"],
                }
                return (
                    regional_selected.url,
                    trace,
                    self._navigation_evidence_for_selected(regional_selected),
                )

        loaded_linkedin_evidence_after_fast_path = False
        if not linkedin_evidence_loaded:
            linkedin_evidence = self._linkedin_company_candidates(
                linkedin_company_url,
                company_name,
                fetch_errors=fetch_errors,
            )
            linkedin_official_candidates = list(linkedin_evidence.official_urls)
            linkedin_candidates = list(linkedin_evidence.outbound_urls)
            linkedin_official_source = linkedin_evidence.official_source
            if linkedin_official_source:
                trace["linkedin_official_evidence_source"] = linkedin_official_source
            loaded_linkedin_evidence_after_fast_path = True
        if loaded_linkedin_evidence_after_fast_path and linkedin_official_candidates:
            official_sources = _candidate_source_map(
                ("linkedin_official_website", linkedin_official_candidates),
                (
                    "linkedin_cached_official_website",
                    linkedin_official_candidates if linkedin_official_source == "cache" else [],
                ),
            )
            recovered: list[WebsiteCandidate] = []
            missing_verification: list[str] = []
            for official_url in dedupe_urls(linkedin_official_candidates):
                official_domain = domain_of(official_url)
                prior = next(
                    (
                        candidate
                        for candidate in fast_scored
                        if domain_of(candidate.url) == official_domain
                        and "domain-only score" not in candidate.reasons
                    ),
                    None,
                )
                if prior is None:
                    missing_verification.append(official_url)
                    continue
                recovered_url = (
                    prior.url if "homepage verified" in prior.reasons else normalize_url(official_url)
                )
                recovered.append(
                    _append_candidate_sources(
                        WebsiteCandidate(
                            recovered_url,
                            prior.score,
                            list(prior.reasons),
                            prior.verified_page,
                        ),
                        official_sources.get(official_domain, set()),
                    )
                )
            if missing_verification:
                recovered.extend(
                    self._rank_and_verify_candidates(
                        missing_verification,
                        company_name,
                        linkedin_company_url,
                        job_location=job_location,
                        candidate_sources=official_sources,
                        fetch_errors=fetch_errors,
                    )
                )
            official_selected = self._select_verified_candidate(
                sorted(recovered, key=lambda candidate: candidate.score, reverse=True)
            )
            if official_selected:
                trace["selected"] = {
                    "url": official_selected.url,
                    "score": official_selected.score,
                    "reasons": official_selected.reasons,
                }
                return (
                    official_selected.url,
                    trace,
                    self._navigation_evidence_for_selected(official_selected),
                )
        if fast_selection_deferred and fast_selected:
            trace["selected"] = {
                "url": fast_selected.url,
                "score": fast_selected.score,
                "reasons": fast_selected.reasons
                + ["fast verified domain", "LinkedIn official evidence unavailable"],
            }
            return (
                fast_selected.url,
                trace,
                self._navigation_evidence_for_selected(fast_selected),
            )
        search_evidence = self._search_candidates_with_evidence(
            company_name,
            job_location,
            fetch_errors=fetch_errors,
        )
        search_candidates = [result.url for result in search_evidence]
        evidence_by_domain = {domain_of(result.url): result for result in search_evidence}
        all_candidates = dedupe_urls(
            preferred_candidates
            + linkedin_official_candidates
            + linkedin_candidates[:5]
            + search_candidates[:5]
            + guessed_candidates[:6]
        )
        candidate_sources = _candidate_source_map(
            ("preferred_input", preferred_candidates),
            ("linkedin_official_website", linkedin_official_candidates),
            (
                "linkedin_cached_official_website",
                linkedin_official_candidates if linkedin_official_source == "cache" else [],
            ),
            ("linkedin_evidence", linkedin_candidates[:5]),
            ("search_evidence", search_candidates[:5]),
            ("speculative_guess", guessed_candidates[:6]),
        )
        scored = self._rank_and_verify_candidates(
            all_candidates,
            company_name,
            linkedin_company_url,
            job_location=job_location,
            search_evidence=evidence_by_domain,
            candidate_sources=candidate_sources,
            fetch_errors=fetch_errors,
        )
        seen_domains = {domain_of(str(item.get("url") or "")) for item in trace["candidates"]}
        trace["candidates"].extend(
            {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
            for candidate in scored[:10]
            if domain_of(candidate.url) not in seen_domains
        )

        selected = self._select_verified_candidate(scored)
        if selected:
            trace["selected"] = {
                "url": selected.url,
                "score": selected.score,
                "reasons": selected.reasons,
            }
            return selected.url, trace, self._navigation_evidence_for_selected(selected)

        retryable_failure = _strongest_retryable_fetch_failure(fetch_errors)
        if retryable_failure is not None:
            trace["resolution_failure"] = {
                "kind": "verification_blocked",
                **retryable_failure,
            }
        return None, trace, None

    @staticmethod
    def _navigation_evidence_for_selected(
        selected: WebsiteCandidate,
    ) -> HomepageNavigationEvidence | None:
        if "homepage verified" not in selected.reasons or selected.verified_page is None:
            return None
        return evidence_from_verified_homepage(
            selected.verified_page,
            homepage_url=selected.url,
        )

    def _rank_and_verify_candidates(
        self,
        candidates: list[str],
        company_name: str,
        linkedin_company_url: str | None,
        job_location: str | None = None,
        search_evidence: dict[str, SearchEvidence] | None = None,
        candidate_sources: dict[str, set[str]] | None = None,
        fetch_errors: list[dict] | None = None,
    ) -> list[WebsiteCandidate]:
        search_evidence = search_evidence or {}
        candidate_sources = candidate_sources or {}
        base_scored = [
            self._score_candidate(
                candidate,
                company_name,
                linkedin_company_url=linkedin_company_url,
                job_location=job_location,
                verify=False,
                search_evidence=search_evidence.get(domain_of(candidate)),
            )
            for candidate in candidates
        ]
        for candidate in base_scored:
            candidate.reasons.extend(
                f"candidate source: {source}"
                for source in sorted(candidate_sources.get(domain_of(candidate.url), set()))
            )
            if "linkedin_official_website" in candidate_sources.get(
                domain_of(candidate.url), set()
            ):
                candidate.score += 100
                candidate.reasons.append("LinkedIn company page identifies official website")
        base_scored.sort(key=lambda candidate: candidate.score, reverse=True)

        verify_count = min(self.verify_limit, len(base_scored))
        to_verify = _allocate_verification_slots(
            base_scored,
            verify_count,
            candidate_sources,
        )
        direct_to_verify = [
            candidate
            for candidate in to_verify
            if _has_direct_identity_source(candidate)
        ]
        remaining_to_verify = [
            candidate for candidate in to_verify if candidate not in direct_to_verify
        ]

        def verify_wave(wave: list[WebsiteCandidate]) -> list[WebsiteCandidate]:
            if not wave:
                return []

            def verify_candidate(
                candidate: WebsiteCandidate,
            ) -> tuple[WebsiteCandidate, list[dict]]:
                candidate_fetch_errors: list[dict] = []
                verified_candidate = _append_candidate_sources(
                    self._score_candidate(
                        candidate.url,
                        company_name,
                        linkedin_company_url=linkedin_company_url,
                        job_location=job_location,
                        verify=True,
                        search_evidence=search_evidence.get(domain_of(candidate.url)),
                        fetch_errors=candidate_fetch_errors,
                        evidence_tier=_candidate_evidence_tier(
                            candidate_sources.get(domain_of(candidate.url), set())
                        ),
                    ),
                    candidate_sources.get(domain_of(candidate.url), set()),
                )
                return verified_candidate, candidate_fetch_errors

            with ThreadPoolExecutor(
                max_workers=len(wave),
                thread_name_prefix="website-verify",
            ) as executor:
                outcomes = list(executor.map(verify_candidate, wave))
            if fetch_errors is not None:
                for _candidate, candidate_fetch_errors in outcomes:
                    fetch_errors.extend(candidate_fetch_errors)
            return [candidate for candidate, _errors in outcomes]

        verified = verify_wave(direct_to_verify)
        directly_selectable_domains = {
            domain_of(candidate.url)
            for candidate in verified
            if self._select_verified_candidate([candidate]) is not None
        }
        if len(directly_selectable_domains) != 1:
            verified.extend(verify_wave(remaining_to_verify))
        verified_domains = {domain_of(candidate.url) for candidate in verified}
        refined = verified + [
            candidate for candidate in base_scored if domain_of(candidate.url) not in verified_domains
        ]
        return sorted(refined, key=lambda candidate: candidate.score, reverse=True)

    def _search_candidates(self, company_name: str, job_location: str | None = None) -> list[str]:
        return [result.url for result in self._search_candidates_with_evidence(company_name, job_location)]

    def _search_candidates_with_evidence(
        self,
        company_name: str,
        job_location: str | None = None,
        fetch_errors: list[dict] | None = None,
    ) -> list[SearchEvidence]:
        region = location_region(job_location)
        region_query = " United States" if region == "us" else ""
        query_text = f"{company_name}{region_query} official website"
        query = urlencode({"q": query_text, "setlang": "en-us", "cc": "us"})
        rss_query = urlencode(
            {"q": query_text, "format": "rss", "setlang": "en-us", "cc": "us"}
        )
        results: list[SearchEvidence] = []
        seen: set[str] = set()
        searches = (
            (f"{SEARCH_ENDPOINT}?{rss_query}", _bing_rss_results),
            (f"{SEARCH_ENDPOINT}?{query}", _bing_html_results),
            (f"{DUCKDUCKGO_SEARCH_ENDPOINT}?{query}", _duckduckgo_html_results),
        )
        for search_url, extract_urls in searches:
            try:
                page = self.fetcher.fetch(search_url)
            except FetchError as exc:
                _retain_fetch_error(
                    fetch_errors,
                    exc,
                    phase="search",
                    url=search_url,
                    evidence_tier=2,
                )
                continue
            raw_results = extract_urls(page.html)
            for result in raw_results:
                cleaned = clean_search_url(result.url, preserve_region=region)
                if not cleaned or is_blocked_domain(cleaned):
                    continue
                domain = domain_of(cleaned)
                if domain in seen:
                    continue
                seen.add(domain)
                results.append(SearchEvidence(cleaned, result.title, result.snippet))
            if results:
                break
        return results

    def _linkedin_company_candidates(
        self,
        linkedin_company_url: str | None,
        company_name: str,
        fetch_errors: list[dict] | None = None,
    ) -> _LinkedInCompanyCandidates:
        if not linkedin_company_url:
            return _LinkedInCompanyCandidates()
        base_url = linkedin_company_url.rstrip("/")
        attempt_urls = list(
            dict.fromkeys(
                (
                    linkedin_company_url,
                    base_url if linkedin_company_url.endswith("/") else f"{base_url}/",
                )
            )
        )
        pages = []
        for attempt_url in attempt_urls:
            try:
                candidate_page = self.fetcher.fetch(attempt_url)
            except FetchError as exc:
                _retain_fetch_error(
                    fetch_errors,
                    exc,
                    phase="linkedin_company",
                    url=attempt_url,
                    evidence_tier=1,
                )
                continue
            pages.append(candidate_page)
            if not _linkedin_company_page_incomplete(candidate_page.html):
                break
        page = max(pages, key=lambda item: _linkedin_company_page_quality(item.html)) if pages else None
        official = _linkedin_json_ld_websites(page.html, company_name) if page else []
        official_source = "live" if official else None
        if official and self.linkedin_evidence_store is not None:
            try:
                self.linkedin_evidence_store.save(
                    company_name,
                    linkedin_company_url,
                    tuple(official),
                )
            except (OSError, TypeError, ValueError):
                pass
        elif self.linkedin_evidence_store is not None:
            try:
                official = list(
                    self.linkedin_evidence_store.load(company_name, linkedin_company_url)
                )
            except (OSError, TypeError, ValueError):
                official = []
            if official:
                official_source = "cache"
        urls: list[str] = []
        for url in re.findall(r"https?://[^\"'<>\s)\\]+", page.html if page else ""):
            cleaned = clean_search_url(url)
            if not cleaned or is_blocked_domain(cleaned):
                continue
            urls.append(cleaned)
        return _LinkedInCompanyCandidates(
            official_urls=tuple(dedupe_urls(official)),
            outbound_urls=tuple(dedupe_urls(urls)),
            official_source=official_source,
        )

    def _guess_domain_candidates(self, company_name: str) -> list[str]:
        tokens = tokenize_company_name(company_name)
        if not tokens:
            return []
        compact = "".join(tokens)
        dashed = "-".join(tokens)
        prefixes = ["", "www.", "get", "go", "try", "join"]
        tlds = [".com", ".ai", ".io", ".co", ".org", ".tech"]
        bases = [compact]
        if dashed != compact:
            bases.append(dashed)
        abbreviation = _company_abbreviation(tokens)
        if abbreviation:
            bases.append(abbreviation)
        urls: list[str] = []
        brand_as_tld = _brand_as_tld_candidate(tokens)
        if brand_as_tld:
            urls.append(brand_as_tld)
        institutional_acronym = _institutional_acronym(tokens)
        if institutional_acronym:
            urls.append(f"https://{institutional_acronym}.edu")
        for base in bases:
            for tld in tlds[:4]:
                urls.append(f"https://{base}{tld}")
            for prefix in prefixes[2:4]:
                urls.append(f"https://{prefix}{base}.com")
        return dedupe_urls(urls)

    def _linkedin_slug_domain_candidates(self, linkedin_company_url: str | None) -> list[str]:
        if not linkedin_company_url:
            return []
        path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != "company":
            return []
        slug = re.sub(r"[^a-z0-9-]", "", path_parts[1].lower())
        if not slug:
            return []
        base = re.sub(r"-(inc|llc|ltd|corp|corporation|company|co)$", "", slug)
        base = re.sub(r"(inc|llc|ltd|corp|corporation|company|co|hq)$", "", base)
        compact = base.replace("-", "")
        product_suffix_base = re.sub(r"-(ai|app|tech)$", "", base)
        prefix_stripped_base = re.sub(r"^(get|go|join|try|use)-?", "", base)
        candidates = [base, compact, product_suffix_base, prefix_stripped_base]
        return [
            f"https://{candidate}.{tld}"
            for candidate in dict.fromkeys(candidates)
            if candidate
            for tld in ("com", "ai", "io", "co", "app")
        ]

    def _select_verified_candidate(
        self,
        scored: list[WebsiteCandidate],
        require_fast_confidence: bool = False,
    ) -> WebsiteCandidate | None:
        authoritative_identity_available = any(
            "LinkedIn company page identifies official website" in candidate.reasons
            or (
                "homepage verified" in candidate.reasons
                and _has_positive_page_identity(candidate)
            )
            for candidate in scored
        )
        for candidate in scored:
            if candidate.score < 25:
                continue
            linkedin_official = (
                "LinkedIn company page identifies official website" in candidate.reasons
            )
            access_controlled_institution = (
                "access-controlled institutional acronym" in candidate.reasons
            )
            if any(
                reason in candidate.reasons
                for reason in (
                    "hosted non-company destination rejected",
                    "parked domain rejected",
                )
            ):
                continue
            if (
                "homepage verified" not in candidate.reasons
                and not linkedin_official
                and not access_controlled_institution
            ):
                continue
            if access_controlled_institution and authoritative_identity_available:
                continue
            if "homepage verified" not in candidate.reasons and linkedin_official:
                acceptance_reason = (
                    "LinkedIn official website accepted without homepage response"
                )
                if acceptance_reason not in candidate.reasons:
                    candidate.reasons.append(acceptance_reason)
            preferred_core_identity = (
                "candidate source: preferred_input" in candidate.reasons
                and _has_positive_core_page_identity(candidate)
            )
            if (
                not linkedin_official
                and not access_controlled_institution
                and not _has_positive_page_identity(candidate)
                and not preferred_core_identity
            ):
                continue
            if (
                not linkedin_official
                and "incomplete company identity" in candidate.reasons
                and not _has_strong_identity_evidence(candidate)
            ):
                continue
            if "single-token brand extension domain" in candidate.reasons and not any(
                reason in candidate.reasons
                for reason in (
                    "LinkedIn slug confirms domain",
                    "LinkedIn slug exactly matches domain",
                    "homepage canonical confirms company identity",
                    "LinkedIn company page identifies official website",
                )
            ):
                continue
            if "ambiguous company name" in candidate.reasons:
                content_confirms_identity = any(
                    reason in candidate.reasons
                    for reason in (
                        "search result confirms company identity",
                        "homepage title confirms company identity",
                        "homepage canonical confirms company identity",
                        "LinkedIn company page identifies official website",
                    )
                )
                slug_has_support = any(
                    reason in candidate.reasons
                    for reason in (
                        "LinkedIn slug confirms domain",
                        "LinkedIn slug exactly matches domain",
                    )
                ) and (
                    "company token missing from homepage" not in candidate.reasons
                    or "preferred .com TLD" in candidate.reasons
                )
                if not content_confirms_identity and not slug_has_support:
                    continue
            if require_fast_confidence and not (
                "preferred .com TLD" in candidate.reasons
                or "LinkedIn company slug matches domain TLD" in candidate.reasons
                or "homepage canonical URL" in candidate.reasons
                or "LinkedIn company page identifies official website" in candidate.reasons
                or access_controlled_institution
            ):
                continue
            if (
                require_fast_confidence
                and "LinkedIn marketing-prefix slug is TLD-ambiguous" in candidate.reasons
                and not linkedin_official
            ):
                continue
            return candidate
        return None

    def _score_candidate(
        self,
        url: str,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
        verify: bool = True,
        search_evidence: SearchEvidence | None = None,
        fetch_errors: list[dict] | None = None,
        evidence_tier: int = 3,
    ) -> WebsiteCandidate:
        score = 0
        reasons: list[str] = []
        domain = domain_of(url)
        company_tokens = tokenize_company_name(company_name)
        core_company_tokens = _core_company_tokens(company_tokens)
        ambiguous_name = _is_ambiguous_company_name(company_tokens)
        if ambiguous_name:
            reasons.append("ambiguous company name")
        if _is_single_token_brand_extension_domain(domain, company_tokens):
            score -= 25
            reasons.append("single-token brand extension domain")

        for token in company_tokens:
            if token and token in domain:
                score += 35
                reasons.append(f"company token '{token}' in domain")

        if _domain_matches_company_abbreviation(domain, company_tokens):
            abbreviation_score = (
                35 * len(company_tokens)
                if _domain_matches_institutional_acronym(domain, company_tokens)
                else 45
            )
            score += abbreviation_score
            reasons.append("company abbreviation in domain")

        if _domain_uses_brand_token_as_tld(domain, company_tokens):
            score += 45
            reasons.append("terminal brand token used as TLD")

        if domain.endswith((".com", ".ai", ".io", ".co", ".org", ".edu")):
            score += 10
            reasons.append("credible company TLD")
        if domain.endswith(".com"):
            score += 8
            reasons.append("preferred .com TLD")

        slug_tld_score = self._score_linkedin_slug_tld_hint(domain, company_tokens, linkedin_company_url)
        if slug_tld_score:
            score += slug_tld_score
            reasons.append("LinkedIn company slug matches domain TLD")

        if _linkedin_slug_confirms_domain(domain, company_tokens, linkedin_company_url):
            score += 30
            reasons.append("LinkedIn slug confirms domain")
            if _linkedin_slug_uses_marketing_prefix(linkedin_company_url):
                reasons.append("LinkedIn marketing-prefix slug is TLD-ambiguous")

        if _linkedin_slug_exactly_matches_domain(domain, company_tokens, linkedin_company_url):
            score += 75
            reasons.append("LinkedIn slug exactly matches domain")

        if search_evidence and _text_confirms_company_identity(
            f"{search_evidence.title} {search_evidence.snippet}", company_tokens
        ):
            score += 25
            reasons.append("search result confirms company identity")

        if not verify:
            reasons.append("domain-only score")
            return WebsiteCandidate(url, score, reasons)

        try:
            page = self.fetcher.fetch(url)
        except FetchError as exc:
            _retain_fetch_error(
                fetch_errors,
                exc,
                phase="homepage_verification",
                url=url,
                evidence_tier=evidence_tier,
            )
            if _is_access_controlled_institutional_acronym(
                domain,
                company_tokens,
                exc,
            ):
                reasons.append("access-controlled institutional acronym")
                reasons.append(
                    f"homepage access denied: {exc.reason_code or 'HTTP_ACCESS_DENIED'} "
                    f"({exc.status})"
                )
                return WebsiteCandidate(url, score, reasons)
            if domain.endswith(".com"):
                score += 10
                reasons.append("preferred .com domain despite fetch failure")
            score -= 20
            reasons.append("homepage fetch failed")
            return WebsiteCandidate(url, score, reasons)

        resolved_url = page.final_url or page.url
        client_redirect_url = _redirect_only_shell_target(page.html, resolved_url)
        if client_redirect_url:
            if domain_of(client_redirect_url) != domain_of(resolved_url):
                score -= 100
                reasons.append("cross-origin client redirect is migration hint only")
                reasons.append("redirect-only shell rejected")
                return WebsiteCandidate(resolved_url, score, reasons)
            try:
                page = self.fetcher.fetch(client_redirect_url)
            except FetchError as exc:
                _retain_fetch_error(
                    fetch_errors,
                    exc,
                    phase="client_redirect_verification",
                    url=client_redirect_url,
                    evidence_tier=evidence_tier,
                )
                score -= 100
                reasons.append("same-origin client redirect target fetch failed")
                reasons.append("redirect-only shell rejected")
                return WebsiteCandidate(resolved_url, score, reasons)
            resolved_url = page.final_url or page.url
            reasons.append("same-origin client redirect followed")
            if _redirect_only_shell_target(page.html, resolved_url):
                score -= 100
                reasons.append("redirect-only shell hop limit reached")
                return WebsiteCandidate(resolved_url, score, reasons)
        if _is_hosted_non_company_destination(resolved_url):
            score -= 200
            reasons.append("hosted non-company destination rejected")
            return WebsiteCandidate(resolved_url, score, reasons)
        if _is_parked_domain_page(page.html, resolved_url):
            score -= 200
            reasons.append("parked domain rejected")
            return WebsiteCandidate(resolved_url, score, reasons)
        reasons.append("homepage verified")
        canonical_url = _canonical_company_url(page.html, resolved_url, company_tokens)
        if canonical_url:
            resolved_url = canonical_url
            reasons.append("homepage canonical URL")
            if _domain_confirms_company_identity(domain_of(canonical_url), company_tokens):
                score += 20
                reasons.append("homepage canonical confirms company identity")

        target_region = location_region(job_location)
        resolved_region = url_region(resolved_url)
        if target_region and resolved_region and target_region != resolved_region:
            score -= 120
            reasons.append(
                f"regional website conflicts with job location: {resolved_region} vs {target_region}"
            )
        elif target_region and resolved_region == target_region:
            score += 25
            reasons.append(f"regional website matches job location: {target_region}")

        html_prefix = page.html[:5000]
        homepage_title = _html_title(_bounded_html_head(page.html))
        structured_identity = _structured_organization_confirms_identity(
            page.html, company_tokens
        )
        title_identity = _text_confirms_company_identity(homepage_title, company_tokens)
        body_identity = _body_confirms_company_identity(page.html, company_tokens)
        structured_core_identity = (
            core_company_tokens != company_tokens
            and _structured_organization_confirms_identity(page.html, core_company_tokens)
        )
        title_core_identity = (
            core_company_tokens != company_tokens
            and _text_confirms_company_identity(homepage_title, core_company_tokens)
        )
        body_core_identity = (
            core_company_tokens != company_tokens
            and _body_confirms_company_identity(page.html, core_company_tokens)
        )
        if structured_identity:
            score += 35
            reasons.append("homepage organization data confirms company identity")
        if title_identity:
            score += 25
            reasons.append("homepage title confirms company identity")
        matching_abbreviation = _matching_company_abbreviation(
            domain_of(resolved_url), company_tokens
        )
        abbreviation_confirms_identity = (
            matching_abbreviation is not None
            and _contains_identity_token(homepage_title, matching_abbreviation)
        )
        if abbreviation_confirms_identity:
            score += 25
            reasons.append("homepage title confirms company abbreviation")
        if body_identity:
            if not structured_identity and not title_identity and not abbreviation_confirms_identity:
                score += 25
            reasons.append("homepage body confirms company identity")
        if structured_core_identity and not structured_identity:
            score += 35
            reasons.append("homepage organization data confirms core company identity")
        if title_core_identity and not title_identity:
            score += 25
            reasons.append("homepage title confirms core company identity")
        if body_core_identity and not body_identity:
            if not structured_core_identity and not title_core_identity:
                score += 25
            reasons.append("homepage body confirms core company identity")
        token_in_homepage = abbreviation_confirms_identity
        evidenced_tokens: set[str] = set(company_tokens) if abbreviation_confirms_identity else set()
        for token in company_tokens:
            if token in domain:
                evidenced_tokens.add(token)
            if _contains_identity_token(html_prefix, token):
                score += 15
                token_in_homepage = True
                evidenced_tokens.add(token)
                reasons.append(f"company token '{token}' in homepage")
        if not token_in_homepage and company_tokens:
            score -= 35
            reasons.append("company token missing from homepage")
        if (
            len(core_company_tokens) > 1
            and not _domain_confirms_company_identity(domain, core_company_tokens)
            and not set(core_company_tokens).issubset(evidenced_tokens)
        ):
            score -= 45
            reasons.append("incomplete company identity")

        return WebsiteCandidate(resolved_url, score, reasons, page)

    def _score_linkedin_slug_tld_hint(
        self,
        domain: str,
        company_tokens: list[str],
        linkedin_company_url: str | None,
    ) -> int:
        if not linkedin_company_url or not company_tokens:
            return 0
        path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != "company":
            return 0
        slug = re.sub(r"[^a-z0-9]", "", path_parts[1].lower())
        compact_name = "".join(company_tokens)
        domain_parts = domain.split(".")
        if len(domain_parts) < 2:
            return 0
        domain_label, tld = domain_parts[-2], domain_parts[-1]
        if domain_label == compact_name and slug == f"{compact_name}{tld}":
            return 18
        return 0

    def _load_overrides(self, path: str | Path | None) -> dict[str, str]:
        if not path:
            return {}
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {normalize_company_key(key): value for key, value in data.items()}


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.results: list[SearchEvidence] = []
        self._in_h2 = False
        self._in_caption = False
        self._in_snippet = False
        self._current_url = ""
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "h2":
            self._in_h2 = True
        elif tag in {"div", "section"} and "b_caption" in attrs_dict.get("class", ""):
            self._in_caption = True
        elif tag == "p" and self._in_caption:
            self._in_snippet = True
        elif tag == "a" and self._in_h2 and attrs_dict.get("href"):
            self._current_url = attrs_dict["href"]

    def handle_data(self, data: str) -> None:
        if self._in_h2 and self._current_url:
            self._current_title.append(data)
        elif self._in_snippet:
            self._current_snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            if self._current_url:
                title = " ".join("".join(self._current_title).split())
                self.urls.append(self._current_url)
                self.results.append(SearchEvidence(self._current_url, title=title))
            self._in_h2 = False
            self._current_url = ""
            self._current_title = []
        elif tag == "p" and self._in_snippet:
            snippet = " ".join("".join(self._current_snippet).split())
            if snippet and self.results:
                previous = self.results[-1]
                self.results[-1] = SearchEvidence(previous.url, previous.title, snippet)
            self._in_snippet = False
            self._current_snippet = []
        elif tag in {"div", "section"} and self._in_caption:
            self._in_caption = False


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.results: list[SearchEvidence] = []
        self._current_url = ""
        self._current_title: list[str] = []
        self._in_snippet = False
        self._current_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "a" and "result__a" in attrs_dict.get("class", "") and attrs_dict.get("href"):
            self._current_url = attrs_dict["href"]
        elif "result__snippet" in attrs_dict.get("class", ""):
            self._in_snippet = True

    def handle_data(self, data: str) -> None:
        if self._current_url:
            self._current_title.append(data)
        elif self._in_snippet:
            self._current_snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_url:
            title = " ".join("".join(self._current_title).split())
            self.urls.append(self._current_url)
            self.results.append(SearchEvidence(self._current_url, title=title))
            self._current_url = ""
            self._current_title = []
        elif self._in_snippet and tag in {"a", "div", "span"}:
            snippet = " ".join("".join(self._current_snippet).split())
            if snippet and self.results:
                previous = self.results[-1]
                self.results[-1] = SearchEvidence(previous.url, previous.title, snippet)
            self._in_snippet = False
            self._current_snippet = []


def _bing_rss_results(body: str) -> list[SearchEvidence]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    results: list[SearchEvidence] = []
    for item in root.findall(".//item"):
        url = (item.findtext("link") or "").strip()
        if not url:
            continue
        results.append(
            SearchEvidence(
                url=url,
                title=(item.findtext("title") or "").strip(),
                snippet=(item.findtext("description") or "").strip(),
            )
        )
    return results


def _bing_rss_urls(body: str) -> list[str]:
    return [result.url for result in _bing_rss_results(body)]


def _bing_html_results(body: str) -> list[SearchEvidence]:
    parser = _SearchResultParser()
    parser.feed(body)
    seen = {result.url for result in parser.results}
    return parser.results + [
        SearchEvidence(url)
        for url in re.findall(r"https?://[^\"'<>\s)]+", body)
        if url not in seen
    ]


def _bing_html_urls(body: str) -> list[str]:
    return [result.url for result in _bing_html_results(body)]


def _duckduckgo_html_results(body: str) -> list[SearchEvidence]:
    parser = _DuckDuckGoResultParser()
    parser.feed(body)
    seen = {result.url for result in parser.results}
    return parser.results + [
        SearchEvidence(url)
        for url in re.findall(r"https?://[^\"'<>\s)]+", body)
        if url not in seen
    ]


def _duckduckgo_html_urls(body: str) -> list[str]:
    return [result.url for result in _duckduckgo_html_results(body)]


class _CanonicalLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link" or self.href:
            return
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        rel_values = {value.lower() for value in attrs_dict.get("rel", "").split()}
        if "canonical" in rel_values and attrs_dict.get("href"):
            self.href = attrs_dict["href"]


def _is_ambiguous_company_name(company_tokens: list[str]) -> bool:
    return len(company_tokens) == 1 and len(company_tokens[0]) <= 5


def _company_name_loses_identity_separator(company_name: str) -> bool:
    return re.search(r"[a-z0-9]\s*[|+/]\s*[a-z0-9]", company_name, flags=re.I) is not None


def _company_abbreviation(company_tokens: list[str]) -> str | None:
    if len(company_tokens) < 3 or not all(company_tokens):
        return None
    abbreviation = "".join(token[0] for token in company_tokens[:-1]) + company_tokens[-1]
    return abbreviation if len(abbreviation) >= 4 else None


def _institutional_acronym(company_tokens: list[str]) -> str | None:
    if not 3 <= len(company_tokens) <= 8:
        return None
    if company_tokens[-1] not in {"college", "institute", "university"}:
        return None
    if not all(token.isalpha() for token in company_tokens):
        return None
    return "".join(token[0] for token in company_tokens)


def _brand_as_tld_candidate(company_tokens: list[str]) -> str | None:
    if len(company_tokens) < 2 or company_tokens[-1] not in {"ai", "io", "tech"}:
        return None
    label = "".join(company_tokens[:-1])
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
        return None
    return f"https://{label}.{company_tokens[-1]}"


def _domain_matches_company_abbreviation(domain: str, company_tokens: list[str]) -> bool:
    return _matching_company_abbreviation(domain, company_tokens) is not None


def _domain_matches_institutional_acronym(
    domain: str,
    company_tokens: list[str],
) -> bool:
    institutional_acronym = _institutional_acronym(company_tokens)
    if not institutional_acronym or not domain.casefold().endswith(".edu"):
        return False
    label = domain.split(".")[-2] if "." in domain else domain
    return re.sub(r"[^a-z0-9]", "", label.casefold()) == institutional_acronym


def _is_access_controlled_institutional_acronym(
    domain: str,
    company_tokens: list[str],
    error: FetchError,
) -> bool:
    acronym = _institutional_acronym(company_tokens)
    if not acronym or len(acronym) < 4:
        return False
    if domain.casefold() != f"{acronym}.edu":
        return False
    return error.status in {401, 403}


def _matching_company_abbreviation(
    domain: str,
    company_tokens: list[str],
) -> str | None:
    abbreviation = _company_abbreviation(company_tokens)
    label = domain.split(".")[-2] if "." in domain else domain
    normalized_label = re.sub(r"[^a-z0-9]", "", label.casefold())
    if abbreviation and normalized_label == abbreviation:
        return abbreviation
    institutional_acronym = _institutional_acronym(company_tokens)
    if (
        institutional_acronym
        and domain.casefold().endswith(".edu")
        and normalized_label == institutional_acronym
    ):
        return institutional_acronym
    return None


def _contains_identity_token(text: str, token: str) -> bool:
    if not token:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text, flags=re.I) is not None


def _text_confirms_company_identity(text: str, company_tokens: list[str]) -> bool:
    if not text or not company_tokens:
        return False
    if not all(_contains_identity_token(text, token) for token in company_tokens):
        return False
    if not _is_ambiguous_company_name(company_tokens):
        return True
    token = re.escape(company_tokens[0])
    normalized = " ".join(html_unescape(text).split())
    legal_entity_suffix = (
        r"(?:ag|b\.?v\.?|corp(?:oration)?|gmbh|inc(?:orporated)?|"
        r"ltd|limited|llc|plc|pte\.?\s+ltd|s\.?a\.?)"
    )
    return any(
        re.search(pattern, normalized, flags=re.I) is not None
        for pattern in (
            rf"^\s*{token}\s*(?:$|[|,:\-])",
            rf"^\s*{token}\s+{legal_entity_suffix}\s*(?:$|[|,:\-])",
            rf"\bofficial\s+(?:website|homepage)\s+(?:of|for)\s+{token}(?:\W|$)",
            rf"\b{token}\s+(?:official\s+)?(?:website|homepage)(?:\W|$)",
        )
    )


def _domain_confirms_company_identity(domain: str, company_tokens: list[str]) -> bool:
    if not domain or not company_tokens:
        return False
    label = domain.split(".")[-2] if "." in domain else domain
    compact_name = "".join(company_tokens)
    dashed_name = "-".join(company_tokens)
    if label in {compact_name, dashed_name}:
        return True
    return _domain_uses_brand_token_as_tld(domain, company_tokens)


def _domain_uses_brand_token_as_tld(
    domain: str,
    company_tokens: list[str],
) -> bool:
    brand_as_tld = _brand_as_tld_candidate(company_tokens)
    return bool(brand_as_tld and domain == domain_of(brand_as_tld))


def _is_single_token_brand_extension_domain(domain: str, company_tokens: list[str]) -> bool:
    if len(company_tokens) != 1:
        return False
    label = domain.split(".")[-2] if "." in domain else domain
    token = company_tokens[0]
    if label == token or token not in label:
        return False
    if label in {
        f"get{token}",
        f"go{token}",
        f"join{token}",
        f"try{token}",
        f"use{token}",
        f"{token}corp",
        f"{token}hq",
    }:
        return False
    return True


def _linkedin_slug_confirms_domain(
    domain: str,
    company_tokens: list[str],
    linkedin_company_url: str | None,
) -> bool:
    if not linkedin_company_url or not _domain_confirms_company_identity(domain, company_tokens):
        return False
    path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "company":
        return False
    slug = re.sub(r"[^a-z0-9]", "", path_parts[1].lower())
    compact_name = "".join(company_tokens)
    domain_parts = domain.split(".")
    tld = domain_parts[-1] if len(domain_parts) > 1 else ""
    accepted = {
        compact_name,
        f"{compact_name}{tld}",
        f"{compact_name}corp",
        f"{compact_name}hq",
        f"get{compact_name}",
        f"go{compact_name}",
        f"join{compact_name}",
        f"try{compact_name}",
        f"use{compact_name}",
    }
    return slug in accepted


def _linkedin_slug_uses_marketing_prefix(linkedin_company_url: str | None) -> bool:
    if not linkedin_company_url:
        return False
    path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].casefold() != "company":
        return False
    slug = re.sub(r"[^a-z0-9]", "", path_parts[1].casefold())
    return any(slug.startswith(prefix) and len(slug) > len(prefix) for prefix in ("get", "go", "join", "try", "use"))


def _linkedin_slug_exactly_matches_domain(
    domain: str,
    company_tokens: list[str],
    linkedin_company_url: str | None,
) -> bool:
    if not linkedin_company_url or not _is_ambiguous_company_name(company_tokens):
        return False
    path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].casefold() != "company":
        return False
    slug = re.sub(r"[^a-z0-9]", "", path_parts[1].casefold())
    domain_parts = domain.casefold().split(".")
    if len(domain_parts) < 2:
        return False
    domain_label = re.sub(r"[^a-z0-9]", "", domain_parts[-2])
    compact_name = "".join(company_tokens)
    return bool(
        slug
        and slug == domain_label
        and domain_label != compact_name
        and compact_name in domain_label
    )


def _html_title(html: str) -> str:
    match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", html, flags=re.I | re.S)
    if not match:
        return ""
    title = re.sub(r"<[^>]+>", " ", match.group(1))
    return " ".join(html_unescape(title).split())


def _bounded_html_head(html: str, limit: int = 65536) -> str:
    prefix = html[:limit]
    closing = re.search(r"</head\s*>", prefix, flags=re.I)
    return prefix[: closing.end()] if closing else prefix


def _redirect_only_shell_target(html: str, base_url: str) -> str | None:
    if not html:
        return None
    meta_target = ""
    for tag in re.findall(r"<meta\b[^>]*>", html[:20000], flags=re.I | re.S):
        if not re.search(r"http-equiv\s*=\s*([\"'])?refresh\1", tag, flags=re.I):
            continue
        content = re.search(
            r"content\s*=\s*([\"'])(.*?)\1|content\s*=\s*([^\s>]+)",
            tag,
            flags=re.I | re.S,
        )
        value = (content.group(2) or content.group(3) or "") if content else ""
        refresh = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*;\s*url\s*=\s*['\"]?([^'\"\s]+)['\"]?\s*",
            html_unescape(value),
            flags=re.I,
        )
        if refresh and float(refresh.group(1)) <= 5:
            meta_target = refresh.group(2)
            break

    script_target = ""
    script_is_onload_wrapper = False
    scripts = re.findall(
        r"<script\b([^>]*)>(.*?)</script>", html[:20000], flags=re.I | re.S
    )
    if len(scripts) == 1:
        attrs, script = scripts[0]
        if not re.search(r"\bsrc\s*=", attrs, flags=re.I):
            script_target = _literal_location_redirect(script)
            if not script_target:
                script_target = _literal_location_redirect(
                    script, allow_onload_wrapper=True
                )
                script_is_onload_wrapper = bool(script_target)

    target = meta_target or script_target
    if not target:
        return None
    visible_body = _visible_body_text(html)
    if script_is_onload_wrapper and visible_body:
        return None
    if len(visible_body) > 200:
        return None
    try:
        normalized = normalize_url(target, base_url)
    except (TypeError, ValueError):
        return None
    if urlparse(normalized).scheme not in {"http", "https"} or not domain_of(normalized):
        return None
    return normalized


def _literal_location_redirect(script: str, allow_onload_wrapper: bool = False) -> str:
    direct = re.fullmatch(
        r"\s*(?:window\.)?location(?:\.href)?\s*=\s*(['\"])([^'\"]+)\1\s*;?\s*"
        r"|\s*(?:window\.)?location\.replace\(\s*(['\"])([^'\"]+)\3\s*\)\s*;?\s*",
        script,
        flags=re.I,
    )
    if direct:
        return direct.group(2) or direct.group(4)
    if not allow_onload_wrapper:
        return ""
    wrapper = re.fullmatch(
        r"\s*window\.onload\s*=\s*function\s*\(\s*\)\s*\{(.*?)\}\s*;?\s*",
        script,
        flags=re.I | re.S,
    )
    if not wrapper:
        return ""
    body = wrapper.group(1)
    assignment = re.fullmatch(
        r"\s*(?:window\.)?location\.href\s*=\s*(['\"])([^'\"]+)\1\s*;?\s*"
        r"|\s*(?:window\.)?location\.replace\(\s*(['\"])([^'\"]+)\3\s*\)\s*;?\s*",
        body,
        flags=re.I,
    )
    return (assignment.group(2) or assignment.group(4)) if assignment else ""


def _visible_body_text(html: str) -> str:
    body_match = re.search(r"<body\b[^>]*>(.*?)</body\s*>", html, flags=re.I | re.S)
    body = body_match.group(1) if body_match else html
    body = re.sub(
        r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        " ",
        body,
        flags=re.I | re.S,
    )
    return " ".join(html_unescape(re.sub(r"<[^>]+>", " ", body)).split())


def _body_confirms_company_identity(html: str, company_tokens: list[str]) -> bool:
    visible = _visible_body_text(html[:100000])
    if not visible or not company_tokens:
        return False
    if not all(_contains_identity_token(visible, token) for token in company_tokens):
        return False
    if not _is_ambiguous_company_name(company_tokens):
        return True
    token = re.escape(company_tokens[0])
    return bool(
        re.search(rf"^\s*{token}(?:\W|$)", visible, flags=re.I)
        or re.search(
            rf"\b(?:about|careers?|company|copyright|official|welcome to)\s+{token}(?:\W|$)",
            visible,
            flags=re.I,
        )
    )


def _structured_organization_confirms_identity(
    html: str,
    company_tokens: list[str],
) -> bool:
    for attrs, body in re.findall(
        r"<script\b([^>]*)>(.*?)</script>",
        html[:200000],
        flags=re.I | re.S,
    ):
        if "application/ld+json" not in attrs.casefold():
            continue
        try:
            payload = json.loads(html_unescape(body.strip()))
        except (json.JSONDecodeError, TypeError):
            continue
        for organization in _walk_linkedin_organizations(payload):
            for field in ("name", "legalName"):
                if _text_confirms_company_identity(
                    str(organization.get(field) or ""), company_tokens
                ):
                    return True
    return False


def _has_positive_page_identity(candidate: WebsiteCandidate) -> bool:
    return any(
        reason in candidate.reasons
        for reason in (
            "homepage organization data confirms company identity",
            "homepage title confirms company identity",
            "homepage title confirms company abbreviation",
            "homepage canonical confirms company identity",
            "homepage body confirms company identity",
        )
    )


def _has_positive_core_page_identity(candidate: WebsiteCandidate) -> bool:
    return any(
        reason in candidate.reasons
        for reason in (
            "homepage organization data confirms core company identity",
            "homepage title confirms core company identity",
            "homepage body confirms core company identity",
        )
    )


def _has_strong_identity_evidence(candidate: WebsiteCandidate) -> bool:
    return any(
        reason in candidate.reasons
        for reason in (
            "search result confirms company identity",
            "homepage organization data confirms company identity",
            "homepage title confirms company identity",
            "homepage title confirms company abbreviation",
            "homepage canonical confirms company identity",
            "homepage organization data confirms core company identity",
            "homepage title confirms core company identity",
            "LinkedIn slug confirms domain",
            "LinkedIn slug exactly matches domain",
        )
    )


def _canonical_company_url(html: str, base_url: str, company_tokens: list[str]) -> str | None:
    parser = _CanonicalLinkParser()
    parser.feed(html[:100000])
    if not parser.href:
        return None
    canonical_url = normalize_url(parser.href, base_url)
    canonical_domain = domain_of(canonical_url)
    if not canonical_domain or is_blocked_domain(canonical_url):
        return None
    if company_tokens and not _domain_confirms_company_identity(canonical_domain, company_tokens):
        return None
    return canonical_url


def normalize_company_key(company_name: str) -> str:
    return " ".join(tokenize_company_name(company_name))


def tokenize_company_name(company_name: str) -> list[str]:
    company_name = _strip_non_brand_qualifiers(company_name)
    cleaned = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology)\b", "", company_name, flags=re.I)
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", cleaned)
        if token
    ]


def _core_company_tokens(company_tokens: list[str]) -> list[str]:
    if len(company_tokens) > 1 and company_tokens[-1] in {"group"}:
        return company_tokens[:-1]
    return company_tokens


def _strip_non_brand_qualifiers(company_name: str) -> str:
    def replace_parenthetical(match: re.Match[str]) -> str:
        content = " ".join(match.group(1).split())
        normalized = content.casefold()
        is_funding_or_batch = any(
            re.search(pattern, normalized, flags=re.I)
            for pattern in (
                r"\b(?:yc|y\s+combinator)\b",
                r"\b(?:pre[- ]?seed|seed|series\s+[a-z]|funded|funding|venture[- ]?backed)\b",
                r"\b[wsf]\d{2}\b",
            )
        )
        is_legal_only = re.fullmatch(
            r"(?:incorporated|inc\.?|llc|ltd\.?|limited|corp\.?|corporation|plc)",
            normalized,
        ) is not None
        return " " if is_funding_or_batch or is_legal_only else match.group(0)

    return re.sub(r"\(([^()]*)\)", replace_parenthetical, company_name)


def _is_parked_domain_page(html: str, resolved_url: str) -> bool:
    host = domain_of(resolved_url)
    if any(host == parked or host.endswith(f".{parked}") for parked in PARKED_DOMAIN_HOSTS):
        return True
    html_head = (html or "")[:20000]
    normalized_markup = html_head.casefold()
    if any(marker in normalized_markup for marker in PARKED_DOMAIN_INFRASTRUCTURE_MARKERS):
        return True
    visible_head = re.sub(r"<[^>]+>", " ", html_head, flags=re.S)
    normalized = " ".join(html_unescape(visible_head).casefold().split())
    return any(marker in normalized for marker in PARKED_DOMAIN_TEXT_MARKERS)


def _has_non_www_subdomain(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").casefold().strip(".")
    except (TypeError, ValueError):
        return False
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return False
    two_level_suffixes = {"co.jp", "co.nz", "co.uk", "com.au", "com.br", "com.sg"}
    apex_label_count = 3 if ".".join(labels[-2:]) in two_level_suffixes else 2
    return len(labels) > apex_label_count


def _has_direct_identity_source(candidate: WebsiteCandidate) -> bool:
    direct_sources = {
        "candidate source: linkedin_official_website",
        "candidate source: linkedin_cached_official_website",
        "candidate source: preferred_input",
    }
    return any(reason in direct_sources for reason in candidate.reasons)


def _candidate_evidence_tier(sources: set[str]) -> int:
    if sources.intersection(
        {
            "linkedin_cached_official_website",
            "linkedin_official_website",
            "preferred_input",
        }
    ):
        return 1
    if sources.intersection(
        {
            "linkedin_evidence",
            "linkedin_slug",
            "regional_recovery",
            "search_evidence",
        }
    ):
        return 2
    return 3


def _retain_fetch_error(
    fetch_errors: list[dict] | None,
    error: FetchError,
    *,
    phase: str,
    url: str,
    evidence_tier: int,
) -> None:
    if fetch_errors is None:
        return
    fetch_errors.append(
        {
            "phase": phase,
            "url": url,
            "evidence_tier": evidence_tier,
            **project_fetch_error(error),
        }
    )


def _strongest_retryable_fetch_failure(fetch_errors: list[dict]) -> dict | None:
    retryable = [
        failure
        for failure in fetch_errors
        if failure.get("retryable") is True
        and failure.get("evidence_tier") in {1, 2}
    ]
    if not retryable:
        return None
    return dict(min(retryable, key=lambda failure: failure["evidence_tier"]))


def _candidate_source_map(*groups: tuple[str, list[str]]) -> dict[str, set[str]]:
    sources: dict[str, set[str]] = {}
    for source, urls in groups:
        for url in urls:
            domain = domain_of(url)
            if domain:
                sources.setdefault(domain, set()).add(source)
    return sources


def _append_candidate_sources(
    candidate: WebsiteCandidate,
    sources: set[str],
) -> WebsiteCandidate:
    candidate.reasons.extend(
        reason
        for source in sorted(sources)
        if (reason := f"candidate source: {source}") not in candidate.reasons
    )
    if "linkedin_official_website" in sources:
        candidate.score += 100
        candidate.reasons.append("LinkedIn company page identifies official website")
    return candidate


def _linkedin_json_ld_websites(html: str, company_name: str) -> list[str]:
    company_tokens = tokenize_company_name(company_name)
    websites: list[str] = []
    for attrs, body in re.findall(
        r"<script\b([^>]*)>(.*?)</script>",
        html,
        flags=re.I | re.S,
    ):
        if "application/ld+json" not in attrs.lower():
            continue
        try:
            payload = json.loads(html_unescape(body.strip()))
        except (json.JSONDecodeError, TypeError):
            continue
        for organization in _walk_linkedin_organizations(payload):
            if not _text_confirms_company_identity(
                str(organization.get("name") or ""),
                company_tokens,
            ):
                continue
            same_as = organization.get("sameAs")
            values = same_as if isinstance(same_as, list) else [same_as]
            for value in values:
                if not isinstance(value, str):
                    continue
                candidate = value.strip()
                if re.fullmatch(
                    r"(?:www\.)?[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?",
                    candidate,
                    flags=re.I,
                ):
                    candidate = f"https://{candidate}"
                cleaned = clean_search_url(candidate)
                if cleaned and not is_blocked_domain(cleaned):
                    websites.append(cleaned)
    return dedupe_urls(websites)


def _linkedin_company_page_incomplete(html: str) -> bool:
    return len(html) < 10_000 or "application/ld+json" not in html.casefold()


def _linkedin_company_page_quality(html: str) -> tuple[bool, int, int]:
    lowered = html.casefold()
    return (
        not _linkedin_company_page_incomplete(html),
        lowered.count("application/ld+json"),
        len(html),
    )


def _walk_linkedin_organizations(value):
    if isinstance(value, dict):
        item_type = value.get("@type")
        item_types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(kind).casefold() == "organization" for kind in item_types):
            yield value
        for child in value.values():
            yield from _walk_linkedin_organizations(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_linkedin_organizations(item)


def _allocate_verification_slots(
    scored: list[WebsiteCandidate],
    verify_count: int,
    candidate_sources: dict[str, set[str]],
) -> list[WebsiteCandidate]:
    if verify_count <= 0:
        return []

    selected: list[WebsiteCandidate] = []
    selected_domains: set[str] = set()
    # Direct page evidence is scarcer than generated guesses. Give each source
    # one opportunity before filling the remaining bounded slots by score.
    for source in (
        "preferred_input",
        "linkedin_official_website",
        "linkedin_evidence",
        "search_evidence",
        "linkedin_slug",
    ):
        candidate = next(
            (
                item
                for item in scored
                if domain_of(item.url) not in selected_domains
                and source in candidate_sources.get(domain_of(item.url), set())
            ),
            None,
        )
        if candidate is None:
            continue
        selected.append(candidate)
        selected_domains.add(domain_of(candidate.url))
        if len(selected) == verify_count:
            return selected

    direct_evidence_sources = {"linkedin_evidence", "search_evidence"}
    for candidate in scored:
        domain = domain_of(candidate.url)
        if domain in selected_domains:
            continue
        if not candidate_sources.get(domain, set()).intersection(direct_evidence_sources):
            continue
        selected.append(candidate)
        selected_domains.add(domain)
        if len(selected) == verify_count:
            return selected

    for candidate in scored:
        domain = domain_of(candidate.url)
        if domain in selected_domains:
            continue
        selected.append(candidate)
        selected_domains.add(domain)
        if len(selected) == verify_count:
            break
    return selected


def clean_search_url(url: str, preserve_region: str | None = None) -> str:
    url = html_unescape(url)
    parsed = urlparse(url)
    if parsed.path.startswith("/ck/a"):
        values = parse_qs(parsed.query)
        if values.get("u"):
            url = values["u"][0]
            if url.startswith("a1"):
                encoded = url[2:]
                try:
                    url = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
                except (UnicodeDecodeError, ValueError):
                    return ""
            else:
                url = unquote(url)
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com"):
        redirect_url = parse_qs(parsed.query).get("uddg", [""])[0]
        if redirect_url:
            url = unquote(redirect_url)
    if not url.startswith("http"):
        return ""
    parsed = urlparse(url)
    if parsed.netloc.endswith("bing.com") or parsed.netloc.endswith("microsoft.com"):
        return ""
    base = f"{parsed.scheme}://{parsed.netloc}"
    if preserve_region and url_region(url) == preserve_region:
        return normalize_url(f"{base}{parsed.path or '/'}")
    return normalize_url(base)


_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_NON_US_REGION_SEGMENTS = {
    "africa": "africa",
    "asia": "asia",
    "australia": "au",
    "au": "au",
    "ca": "ca",
    "canada": "ca",
    "de": "de",
    "fr": "fr",
    "in": "in",
    "india": "in",
    "ireland": "ie",
    "jp": "jp",
    "japan": "jp",
    "southeast-asia": "sea",
    "uk": "uk",
    "united-kingdom": "uk",
}
_LOCATION_REGION_NAMES = {
    "australia": "au",
    "belgium": "be",
    "canada": "ca",
    "france": "fr",
    "germany": "de",
    "india": "in",
    "ireland": "ie",
    "japan": "jp",
    "spain": "es",
    "united kingdom": "uk",
}


def location_region(location: str | None) -> str | None:
    if not location:
        return None
    normalized = location.casefold()
    if re.search(r"\b(united states|u\.s\.?a?\.?|usa)\b", normalized):
        return "us"
    parts = [part.strip().upper() for part in location.split(",")]
    if any(part in _US_STATE_CODES for part in parts[1:]):
        return "us"
    known_region_codes = set(_LOCATION_REGION_NAMES.values())
    if len(parts) > 1 and parts[-1].casefold() in known_region_codes:
        return parts[-1].casefold()
    for name, region in _LOCATION_REGION_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return region
    return None


def url_region(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return None
    host_labels = (parsed.hostname or "").casefold().split(".")
    if any(label in {"jobsus", "usjobs"} for label in host_labels):
        return "us"
    segments = [unquote(part).casefold() for part in parsed.path.split("/") if part]
    if not segments:
        return None
    locale_match = re.fullmatch(r"[a-z]{2,3}[-_]([a-z]{2})", segments[0])
    if locale_match:
        return locale_match.group(1)
    if segments[0] == "us":
        return "us"
    if segments[0] in _NON_US_REGION_SEGMENTS:
        return _NON_US_REGION_SEGMENTS[segments[0]]
    return None


def _regional_root_candidates(
    scored: list[WebsiteCandidate],
    job_location: str | None,
) -> list[str]:
    target_region = location_region(job_location)
    if target_region != "us":
        return []
    conflicting = next(
        (
            candidate
            for candidate in scored
            if "homepage verified" in candidate.reasons
            and any(
                reason.startswith("regional website conflicts with job location:")
                for reason in candidate.reasons
            )
        ),
        None,
    )
    if conflicting is None:
        return []
    try:
        parsed = urlparse(conflicting.url)
    except (TypeError, ValueError):
        return []
    if parsed.scheme != "https" or not parsed.netloc:
        return []
    origin = f"https://{parsed.netloc}"
    return [
        f"{origin}/us/en.html",
        f"{origin}/us/en/",
        f"{origin}/us/en/careers.html",
    ]


def is_blocked_domain(url: str) -> bool:
    domain = domain_of(url)
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in BLOCKED_DOMAINS):
        return True
    return any(part in domain for part in BLOCKED_DOMAIN_PARTS)


def _is_hosted_non_company_destination(url: str) -> bool:
    domain = domain_of(url)
    return any(
        domain == hosted or domain.endswith("." + hosted)
        for hosted in HOSTED_NON_COMPANY_DOMAINS
    )


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        domain = domain_of(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        deduped.append(url)
    return deduped
