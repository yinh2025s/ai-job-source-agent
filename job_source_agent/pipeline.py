from __future__ import annotations

import re
from contextlib import nullcontext
from dataclasses import asdict
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlparse
from xml.etree import ElementTree as ET

from .career_search import CareerSearchResolver
from .checkpoint import execution_fingerprint
from .career_candidate_scheduler import (
    candidate_concrete_host,
    candidate_evidence_tier,
    candidate_host_family,
    candidate_locale_key,
    candidate_route_family,
    schedule_career_candidates,
)
from .content_probe import (
    discover_first_party_career_navigation,
    probe_first_party_cms_payload,
    probe_first_party_provider_assets,
)
from .contracts import FetchClient, PipelineContext
from .errors import DiscoveryError
from .homepage_navigation import HomepageNavigationEvidence
from .job_board import DiscoveredJobBoard
from .listing_extraction import explicit_empty_inventory_evidence
from .models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
    DiscoveryResult,
    LinkCandidate,
    StageResult,
    dataclass_to_dict,
)
from .opening_matcher import (
    MIN_TITLE_MATCH_SCORE,
    JobOpeningMatcher,
    build_provider_api_requests,
    detect_provider,
    provider_api_candidates,
    score_title_match,
    structured_job_links,
)
from .providers import (
    DEFAULT_PROVIDER_REGISTRY,
    JobBoard,
    JobQuery,
    ProviderAdapter,
    ProviderRegistry,
)
from .reasons import (
    canonical_reason_code,
    classify_fetch_error,
    make_stage_result,
    reason_spec,
)
from .run_configuration import AgentConfig, DeterministicRunConfig
from .scoring import (
    is_ats_url,
    is_likely_job_detail,
    is_likely_job_listing_page,
    is_resource_url,
    score_career_link,
    score_job_link,
)
from .stages import CareerDiscoveryStage, JobBoardDiscoveryStage, OpeningMatchStage, PipelineStageRunner
from .web import FetchError, Page, RawLink, domain_of, extract_links, normalize_url
from .website_resolver import location_region, url_region


COMMON_CAREER_PATHS = (
    "/careers",
    "/career",
    "/jobs",
    "/jobs/search",
    "/jobs/search-results",
    "/join-us",
    "/join-our-team",
    "/work-with-us",
    "/open-positions",
    "/job-openings",
    "/current-openings",
    "/opportunities",
    "/company/careers",
    "/about/careers",
    "/about-us/careers",
    "/about-us/jobs",
    "/careers/jobs",
    "/careers/listings",
    "/careers/search",
    "/en/careers",
    "/en/jobs",
    "/en-us/careers",
    "/en-us/career",
    "/en-us/jobs",
    "/en-us/careers/jobs",
    "/us/en/careers",
    "/us/en/jobs",
)

MIN_DERIVED_TENANT_TITLE_SCORE = 65
MAX_SITEMAPS_PER_DISCOVERY = 10

NON_JOB_BOARD_PATH_PARTS = {
    "api",
    "assets",
    "images",
    "logo",
    "share_image",
    "static",
}

_CAREER_REDIRECT_QUERY_KEYS = {
    "continue",
    "dest",
    "destination",
    "next",
    "redirect",
    "redirect_to",
    "redirect_uri",
    "return",
    "return_to",
    "returnurl",
    "target",
    "url",
}

_CAREER_REDIRECT_SURFACE_MARKERS = {
    "account",
    "auth",
    "blog",
    "cdn",
    "challenge",
    "feed",
    "image",
    "images",
    "login",
    "media",
    "news",
    "oauth",
    "press",
    "signin",
    "static",
    "track",
    "tracking",
}


class _CareerRedirectMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_urls: list[str] = []
        self.og_urls: list[str] = []
        self.identity_values: list[str] = []
        self._identity_tag: str | None = None
        self._identity_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_by_name = {name.casefold(): value or "" for name, value in attrs}
        tag = tag.casefold()
        if tag == "link" and "canonical" in attrs_by_name.get("rel", "").casefold().split():
            if attrs_by_name.get("href"):
                self.canonical_urls.append(attrs_by_name["href"])
        elif tag == "meta":
            name = (attrs_by_name.get("property") or attrs_by_name.get("name") or "").casefold()
            content = attrs_by_name.get("content", "").strip()
            if name == "og:url" and content:
                self.og_urls.append(content)
            if name in {"og:site_name", "og:title", "application-name"} and content:
                self.identity_values.append(content)
        if tag in {"title", "h1"}:
            self._identity_tag = tag
            self._identity_text = []

    def handle_data(self, data: str) -> None:
        if self._identity_tag:
            self._identity_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._identity_tag == tag.casefold():
            value = " ".join(" ".join(self._identity_text).split())
            if value:
                self.identity_values.append(value)
            self._identity_tag = None
            self._identity_text = []


class JobSourceAgent:
    def __init__(
        self,
        fetcher: FetchClient,
        provider_registry: ProviderRegistry | None = None,
        max_candidates: int = 12,
        max_job_pages: int = 8,
        max_career_candidate_fetches: int | None = None,
        max_career_discovery_transport_calls: int | None = None,
        max_career_search_queries: int = 5,
        max_ats_board_fetches: int = 5,
        enable_sitemap_discovery: bool = True,
        enable_career_search: bool = True,
        career_search_timeout: float | None = None,
        run_configuration: DeterministicRunConfig | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.max_candidates = max_candidates
        self.max_job_pages = max_job_pages
        self.max_career_candidate_fetches = (
            max_candidates if max_career_candidate_fetches is None else max(0, max_career_candidate_fetches)
        )
        self.max_career_discovery_transport_calls = max_career_discovery_transport_calls
        self.max_career_search_queries = max(0, max_career_search_queries)
        self.max_ats_board_fetches = max(0, max_ats_board_fetches)
        self.enable_sitemap_discovery = enable_sitemap_discovery
        self.enable_career_search = enable_career_search
        self.career_search_timeout = career_search_timeout
        self._career_transport_scope_active = False
        effective_agent_config = AgentConfig(
            max_candidates=max_candidates,
            max_job_pages=max_job_pages,
            max_career_candidate_fetches=self.max_career_candidate_fetches,
            max_career_discovery_transport_calls=(
                self.max_career_discovery_transport_calls
            ),
            max_career_search_queries=self.max_career_search_queries,
            max_ats_board_fetches=self.max_ats_board_fetches,
            enable_sitemap_discovery=enable_sitemap_discovery,
            enable_career_search=enable_career_search,
            career_search_timeout=career_search_timeout,
        )
        if (
            run_configuration is not None
            and run_configuration.to_agent_config() != effective_agent_config
        ):
            raise ValueError("run_configuration does not match the agent settings")
        self.run_configuration = run_configuration or DeterministicRunConfig.from_agent_config(
            effective_agent_config
        )

    def discover(self, company: CompanyInput) -> DiscoveryResult:
        company_website_url = normalize_url(company.company_website_url) if company.company_website_url else ""
        fingerprint = execution_fingerprint(asdict(company), self.run_configuration.digest)
        result = DiscoveryResult(
            company_name=company.company_name,
            company_website_url=company_website_url,
            hiring_entity_name=company.hiring_entity_name,
            career_root_url=company.career_root_url,
            linkedin_job_url=company.linkedin_job_url,
            external_apply_url=company.external_apply_url,
            linkedin_company_url=company.linkedin_company_url,
            linkedin_job_title=company.job_title,
            linkedin_job_location=company.job_location,
            run_configuration=self.run_configuration.to_payload(),
            run_configuration_digest=self.run_configuration.digest,
            execution_fingerprint=fingerprint,
            trace={
                "source": company.source,
                "linkedin_job_url": company.linkedin_job_url,
                "external_apply_url": company.external_apply_url,
                "linkedin_company_url": company.linkedin_company_url,
                "linkedin_job_title": company.job_title,
                "source_trace": company.source_trace,
                "run_configuration_digest": self.run_configuration.digest,
                "execution_fingerprint": fingerprint,
                "steps": [],
            },
        )
        result.stage_results.extend(self._upstream_stage_results(company, result.company_website_url))
        if not result.company_website_url and not company.external_apply_url:
            self._set_failure(result, "website_not_resolved", "No official company website was supplied.")
            self._append_not_run_stages(
                result,
                (
                    STAGE_HIRING_IDENTITY_RESOLUTION,
                    STAGE_CAREER_DISCOVERY,
                    STAGE_JOB_BOARD_DISCOVERY,
                    STAGE_OPENING_MATCH,
                ),
            )
            return self._finalize_result(result)

        context = PipelineContext.from_company(company)
        context.company_website_url = result.company_website_url
        PipelineStageRunner(
            (
                CareerDiscoveryStage(self),
                JobBoardDiscoveryStage(self, self.provider_registry),
                OpeningMatchStage(self, self.provider_registry),
            )
        ).run(context)
        result.career_page_url = context.career_page_url
        result.job_list_page_url = context.job_list_page_url
        result.open_position_url = context.open_position_url
        result.stage_results.extend(context.stage_results)
        for stage_result in context.stage_results:
            stage_trace = context.trace.get("stages", {}).get(stage_result.stage, {})
            result.trace["steps"].append(
                {"name": _legacy_step_name(stage_result.stage), **stage_trace}
            )
            if (
                stage_result.status == "failed"
                and result.error_code is None
                and not context.job_list_page_url
            ):
                result.error_code = stage_result.reason_code
                result.error = _legacy_error(stage_result.stage, stage_result.reason_code)
                result.trace["failure_detail"] = stage_result.detail
        return self._finalize_result(result)

    def _upstream_stage_results(self, company: CompanyInput, website_url: str) -> list[StageResult]:
        stage_metrics = company.source_trace.get("stage_metrics", {})
        has_linkedin_input = bool(
            company.linkedin_job_url
            or company.linkedin_company_url
            or company.external_apply_url
        )
        linkedin_status = "success" if has_linkedin_input else "not_applicable"
        linkedin_evidence = []
        if company.linkedin_job_url:
            linkedin_evidence.append(_url_evidence("linkedin_job_url", company.linkedin_job_url))
        if company.linkedin_company_url:
            linkedin_evidence.append(_url_evidence("linkedin_company_url", company.linkedin_company_url))
        if company.external_apply_url:
            linkedin_evidence.append(_url_evidence("external_apply_url", company.external_apply_url))

        website_status = "success" if website_url else "failed"
        website_reason = None if website_url else "WEBSITE_NOT_RESOLVED"
        website_evidence = [_url_evidence("company_website_url", website_url)] if website_url else []

        identity_evidence = []
        if company.hiring_entity_name:
            identity_evidence.append({"field": "hiring_entity_name", "value": company.hiring_entity_name})
        if company.career_root_url:
            identity_evidence.append(_url_evidence("career_root_url", company.career_root_url))
        identity_detail = (
            "An alternate hiring entity or explicit career root was supplied."
            if identity_evidence
            else "No alternate hiring entity was supplied; the input company remains the hiring entity."
        )

        return [
            _stage_result(
                STAGE_LINKEDIN_DISCOVERY,
                linkedin_status,
                input_count=1 if has_linkedin_input else 0,
                output_count=1 if has_linkedin_input else 0,
                evidence=linkedin_evidence,
                detail=None if has_linkedin_input else "Direct company input; LinkedIn discovery was upstream or not required.",
            ),
            _stage_result(
                STAGE_WEBSITE_RESOLUTION,
                website_status,
                reason_code=website_reason,
                duration_ms=int(stage_metrics.get("website_resolution_duration_ms") or 0),
                input_count=1,
                output_count=1 if website_url else 0,
                evidence=website_evidence,
                detail=None if website_url else "No official company website was supplied.",
            ),
            _stage_result(
                STAGE_HIRING_IDENTITY_RESOLUTION,
                "success" if website_url else "not_run",
                duration_ms=int(stage_metrics.get("hiring_identity_resolution_duration_ms") or 0),
                input_count=1 if website_url else 0,
                output_count=1 if website_url else 0,
                evidence=identity_evidence,
                detail=identity_detail if website_url else "Website resolution did not produce an input.",
            ),
        ]

    def _append_not_run_stages(self, result: DiscoveryResult, stages: tuple[str, ...]) -> None:
        existing = {stage_result.stage for stage_result in result.stage_results}
        for stage in stages:
            if stage not in existing:
                result.stage_results.append(
                    _stage_result(stage, "not_run", detail="A required upstream stage did not succeed.")
                )

    def _set_failure(self, result: DiscoveryResult, legacy_error: str, detail: str) -> None:
        result.error = legacy_error
        result.error_code = canonical_reason_code(legacy_error)
        result.trace["failure_detail"] = detail

    def _finalize_result(self, result: DiscoveryResult) -> DiscoveryResult:
        stages = {stage_result.stage: stage_result for stage_result in result.stage_results}
        opening_stage = stages.get(STAGE_OPENING_MATCH)
        job_board_stage = stages.get(STAGE_JOB_BOARD_DISCOVERY)
        career_stage = stages.get(STAGE_CAREER_DISCOVERY)

        if opening_stage and opening_stage.status == "success":
            result.pipeline_status = "success"
        elif job_board_stage and job_board_stage.status == "success":
            result.pipeline_status = "partial" if opening_stage and opening_stage.status == "partial" else "success"
        elif career_stage and career_stage.status == "success":
            result.pipeline_status = "partial"
        elif any(stage_result.status == "unsupported" for stage_result in result.stage_results):
            result.pipeline_status = "unsupported"
        else:
            result.pipeline_status = "failed"

        if result.job_list_page_url:
            result.status = "success"
        elif result.career_page_url:
            result.status = "partial"
        else:
            result.status = "failed"

        validation_status = "success"
        validation_detail = None
        if len({stage_result.stage for stage_result in result.stage_results}) != len(result.stage_results):
            validation_status = "failed"
            validation_detail = "Duplicate stage results were produced."
            result.error_code = "RESULT_VALIDATION_FAILED"
        result.stage_results.append(
            _stage_result(
                STAGE_RESULT_VALIDATION,
                validation_status,
                reason_code="RESULT_VALIDATION_FAILED" if validation_status == "failed" else None,
                input_count=1,
                output_count=1 if validation_status == "success" else 0,
                evidence=[{"field": "pipeline_status", "value": result.pipeline_status}],
                detail=validation_detail,
            )
        )
        return result

    def find_career_page(
        self,
        company_website_url: str,
        company_name: str | None = None,
        preferred_url: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
        homepage_navigation_evidence: HomepageNavigationEvidence | None = None,
    ) -> tuple[str, dict]:
        scope = getattr(self.fetcher, "career_discovery_scope", None)
        if not callable(scope):
            return self._find_career_page(
                company_website_url,
                company_name=company_name,
                preferred_url=preferred_url,
                target_title=target_title,
                target_location=target_location,
                homepage_navigation_evidence=homepage_navigation_evidence,
            )

        cache_hits_before = int(getattr(self.fetcher, "cache_hits", 0) or 0)
        with scope(self.max_career_discovery_transport_calls) as budget:
            self._career_transport_scope_active = True
            try:
                career_url, trace = self._find_career_page(
                    company_website_url,
                    company_name=company_name,
                    preferred_url=preferred_url,
                    target_title=target_title,
                    target_location=target_location,
                    homepage_navigation_evidence=homepage_navigation_evidence,
                )
            except DiscoveryError as exc:
                exc.trace["transport_budget"] = self._career_transport_budget_trace(
                    budget,
                    cache_hits_before,
                )
                raise
            finally:
                self._career_transport_scope_active = False
        trace["transport_budget"] = self._career_transport_budget_trace(
            budget,
            cache_hits_before,
        )
        return career_url, trace

    def _find_career_page(
        self,
        company_website_url: str,
        company_name: str | None = None,
        preferred_url: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
        homepage_navigation_evidence: HomepageNavigationEvidence | None = None,
    ) -> tuple[str, dict]:
        homepage_url = normalize_url(company_website_url)
        homepage: Page | None = None
        raw_candidates: list[RawLink] = []
        attempted_candidate_urls: set[str] | None = None
        trace = {
            "homepage_url": homepage_url,
            "homepage_fetch_error": None,
            "candidates": [],
            "candidate_fetch_errors": [],
        }
        evidence_trace = {
            "used": False,
            "candidate_count": 0,
            "fallback": "homepage_fetch",
        }
        trace["homepage_navigation_evidence"] = evidence_trace
        if homepage_navigation_evidence is None:
            evidence_trace["status"] = "absent"
        elif homepage_navigation_evidence.homepage_url != homepage_url:
            evidence_trace["status"] = "homepage_url_mismatch"
        else:
            attempted_candidate_urls = set()
            evidence_candidates = self._dedupe_candidates(
                sorted(
                    [
                        self._score_career_candidate(
                            link,
                            homepage_url,
                            target_title=target_title,
                            target_location=target_location,
                        )
                        for link in homepage_navigation_evidence.raw_links()
                    ],
                    key=lambda candidate: candidate.score,
                    reverse=True,
                )
            )
            evidence_trace.update(
                {
                    "used": True,
                    "status": "candidate_verification",
                    "candidate_count": len(evidence_candidates),
                }
            )
            selected_url = self._select_verified_career_candidate(
                evidence_candidates,
                trace,
                target_title=target_title,
                schedule_source="verified_homepage_navigation",
                company_name=company_name,
                homepage_url=homepage_url,
                attempted_candidate_urls=attempted_candidate_urls,
            )
            if selected_url:
                evidence_trace["fallback"] = None
                trace["selected_from"] = "verified_homepage_navigation"
                trace["sitemap_discovery"] = {
                    "skipped": True,
                    "reason": "verified homepage navigation candidate selected before homepage transport",
                }
                return selected_url, trace
            evidence_trace["status"] = "no_verified_candidate"
        try:
            with self._career_transport_phase("homepage"):
                homepage = self.fetcher.fetch(company_website_url)
            homepage_url = homepage.final_url or homepage.url
            raw_candidates = extract_links(homepage)
        except FetchError as exc:
            trace["homepage_fetch_error"] = str(exc)
            trace["homepage_fetch_failure"] = _fetch_failure_trace(exc)
        if preferred_url:
            raw_candidates.insert(
                0,
                RawLink(
                    url=normalize_url(preferred_url),
                    text="Career root",
                    source_url=homepage_url,
                    origin="identity_career_root",
                ),
            )
            trace["preferred_career_root"] = normalize_url(preferred_url)
        raw_candidates.extend(self._common_path_candidates(homepage_url))
        primary_scored = sorted(
            [
                self._score_career_candidate(
                    link,
                    homepage_url,
                    target_title=target_title,
                    target_location=target_location,
                )
                for link in raw_candidates
            ],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        primary_candidates = self._dedupe_candidates(primary_scored)
        trace["homepage_url"] = homepage_url
        trace["candidates"] = dataclass_to_dict(primary_candidates[:10])

        selected_url = self._select_verified_career_candidate(
            primary_candidates,
            trace,
            target_title=target_title,
            schedule_source="homepage_and_common_paths",
            company_name=company_name,
            homepage_url=homepage_url,
            attempted_candidate_urls=attempted_candidate_urls,
        )
        if selected_url:
            trace["sitemap_discovery"] = {
                "skipped": True,
                "reason": "primary candidate verified before sitemap fanout",
            }
            return selected_url, trace

        if homepage is not None:
            with self._career_transport_phase("bundle_navigation"):
                bundle_links, bundle_trace = discover_first_party_career_navigation(
                    self.fetcher,
                    homepage,
                )
            trace["bundle_navigation_discovery"] = bundle_trace
            bundle_candidates = self._dedupe_candidates(
                sorted(
                    [
                        self._score_career_candidate(
                            link,
                            homepage_url,
                            target_title=target_title,
                            target_location=target_location,
                        )
                        for link in bundle_links
                    ],
                    key=lambda candidate: candidate.score,
                    reverse=True,
                )
            )
            if bundle_candidates:
                trace["candidates"] = dataclass_to_dict(
                    self._dedupe_candidates(primary_candidates + bundle_candidates)[:10]
                )
                selected_url = self._select_verified_career_candidate(
                    bundle_candidates,
                    trace,
                    max_fetches=2,
                    target_title=target_title,
                    schedule_source="bundle_navigation",
                    company_name=company_name,
                    homepage_url=homepage_url,
                    attempted_candidate_urls=attempted_candidate_urls,
                )
                if selected_url:
                    trace["sitemap_discovery"] = {
                        "skipped": True,
                        "reason": "first-party bundle navigation verified before sitemap fanout",
                    }
                    trace["selected_from"] = "bundle_navigation_discovery"
                    return selected_url, trace
        else:
            trace["bundle_navigation_discovery"] = {"skipped": True}

        if self.enable_sitemap_discovery:
            target_region = location_region(target_location)
            with self._career_transport_phase("sitemap_discovery"):
                sitemap_links, sitemap_trace = self._sitemap_candidates(
                    homepage_url,
                    target_region=target_region,
                )
            trace["sitemap_discovery"] = sitemap_trace
            sitemap_scored = sorted(
                [
                    self._score_career_candidate(
                        link,
                        homepage_url,
                        target_title=target_title,
                        target_location=target_location,
                    )
                    for link in sitemap_links
                ],
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            sitemap_candidates = self._dedupe_candidates(sitemap_scored)
            combined_candidates = sorted(
                primary_candidates + sitemap_candidates,
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            trace["candidates"] = dataclass_to_dict(combined_candidates[:10])
            selected_url = self._select_verified_career_candidate(
                sitemap_candidates,
                trace,
                target_title=target_title,
                schedule_source="sitemap",
                company_name=company_name,
                homepage_url=homepage_url,
                attempted_candidate_urls=attempted_candidate_urls,
            )
            if selected_url:
                trace["selected_from"] = "sitemap_discovery"
                return selected_url, trace
        else:
            trace["sitemap_discovery"] = {"skipped": True}

        if self.enable_career_search and company_name:
            with self._career_transport_phase("search_discovery"):
                search_result = self._search_career_candidates(company_name, homepage_url)
            trace["search_discovery"] = search_result.trace
            selected_url = self._select_verified_career_candidate(
                search_result.candidates,
                trace,
                target_title=target_title,
                schedule_source="search",
                company_name=company_name,
                homepage_url=homepage_url,
                attempted_candidate_urls=attempted_candidate_urls,
            )
            if selected_url:
                trace["selected_from"] = "search_discovery"
                return selected_url, trace
        else:
            trace["search_discovery"] = {"skipped": True}

        if company_name and self.max_ats_board_fetches:
            ats_candidates = self._ats_board_candidates(company_name, homepage_url)
            ats_trace = {
                "candidates": dataclass_to_dict(ats_candidates),
                "candidate_fetch_errors": [],
            }
            trace["ats_board_discovery"] = ats_trace
            selected_url = self._select_verified_career_candidate(
                ats_candidates,
                ats_trace,
                max_fetches=self.max_ats_board_fetches,
                target_title=target_title,
                schedule_source="blind_ats",
                company_name=company_name,
                homepage_url=homepage_url,
                attempted_candidate_urls=attempted_candidate_urls,
            )
            if selected_url:
                trace["selected"] = ats_trace["selected"]
                trace["selected_page_source"] = ats_trace.get("selected_page_source")
                trace["selected_from"] = "ats_board_discovery"
                return selected_url, trace
        else:
            trace["ats_board_discovery"] = {"skipped": True}

        offline_fixture_failure = _offline_fixture_failure(trace)
        retryable_candidate_failure = _retryable_evidence_candidate_failure(trace)
        if offline_fixture_failure is not None:
            reason_code = "OFFLINE_FIXTURE_MISSING"
            detail = "Offline replay evidence is incomplete for career discovery."
        elif _trace_has_fetch_budget_exhaustion(trace):
            reason_code = "FETCH_BUDGET_EXHAUSTED"
            detail = "Career candidates remain unverified because the fetch budget was exhausted."
        elif retryable_candidate_failure is not None:
            reason_code = retryable_candidate_failure["reason_code"]
            detail = (
                "An evidence-backed career candidate could not be verified because of a "
                "retryable fetch failure."
            )
        else:
            reason_code = "career_page_not_found"
            detail = "No reliable career page candidate found."
        raise DiscoveryError(
            reason_code,
            detail,
            step_name="find_career_page",
            trace=trace,
        )

    def find_job_board(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict]:
        job_list_url, trace, _discovered_board = self.find_job_board_with_evidence(
            career_page_url,
            company_name=company_name,
            target_location=target_location,
        )
        return job_list_url, trace

    def find_job_board_with_evidence(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict, DiscoveredJobBoard | None]:
        target_region = location_region(target_location)
        if self._is_provider_job_board_url(career_page_url):
            if not self._url_matches_target_region(career_page_url, target_region):
                trace = {
                    "career_page_url": career_page_url,
                    "job_list_page_url": None,
                    "target_region": target_region,
                    "regional_exclusions": [
                        self._regional_exclusion(career_page_url, target_region)
                    ],
                }
                raise DiscoveryError(
                    "job_board_not_found",
                    "The provider board conflicts with the target location region.",
                    step_name="find_job_board",
                    trace=trace,
                )
            adapter = self.provider_registry.adapter_for(career_page_url)
            board = adapter.identify_board(career_page_url) if adapter else None
            job_list_page_url = (
                self._canonical_provider_board_url(
                    adapter.name,
                    board.url,
                    board.identifier,
                )
                if adapter is not None
                and board is not None
                and adapter.supports_listing
                else career_page_url
            )
            return (
                job_list_page_url,
                {
                    "career_page_url": career_page_url,
                    "job_list_page_url": job_list_page_url,
                    "selected": {
                        "url": job_list_page_url,
                        "reason": "career page is already a provider job board",
                    },
                },
                None,
            )
        _opening_url, job_list_url, trace, discovered_board = self._discover_job_board_legacy(
            career_page_url,
            target_location=target_location,
        )
        if (
            company_name
            and self.max_ats_board_fetches
            and not self._has_native_listing_board(discovered_board)
            and (
                not job_list_url
                or (
                    self.provider_registry.detect(job_list_url) == "generic"
                    and job_list_url.rstrip("/") == career_page_url.rstrip("/")
                )
            )
        ):
            if target_location is None:
                searched_url, search_trace = self._search_verified_ats_board(
                    company_name,
                    career_page_url,
                )
            else:
                searched_url, search_trace = self._search_verified_ats_board(
                    company_name,
                    career_page_url,
                    target_location=target_location,
                )
            trace["ats_search_fallback"] = search_trace
            if searched_url:
                job_list_url = searched_url
                discovered_board = None
                trace["job_list_page_url"] = searched_url
                trace["selected_from"] = "ats_search_fallback"
                trace["provider"] = self.provider_registry.detect(searched_url)
        if not job_list_url and trace.get("explicit_empty_inventory"):
            raise DiscoveryError(
                "NO_PUBLIC_OPENINGS",
                "The official career page explicitly reports no current public openings.",
                step_name="find_job_board",
                trace=trace,
            )
        offline_fixture_failure = _offline_fixture_failure(trace)
        retryable_candidate_failure = _retryable_evidence_candidate_failure(trace)
        if not job_list_url and offline_fixture_failure is not None:
            raise DiscoveryError(
                "OFFLINE_FIXTURE_MISSING",
                "Offline replay evidence is incomplete for job-board discovery.",
                step_name="find_job_board",
                trace=trace,
            )
        if not job_list_url and _trace_has_fetch_budget_exhaustion(trace):
            raise DiscoveryError(
                "FETCH_BUDGET_EXHAUSTED",
                "Job-board candidates remain unverified because the fetch budget was exhausted.",
                step_name="find_job_board",
                trace=trace,
            )
        if not job_list_url and retryable_candidate_failure is not None:
            reason_code = (
                "COMPANY_TIME_BUDGET_EXHAUSTED"
                if _trace_has_caller_deadline_exhaustion(trace)
                else retryable_candidate_failure["reason_code"]
            )
            raise DiscoveryError(
                reason_code,
                "An evidence-backed job-board candidate could not be verified because of a retryable fetch failure.",
                step_name="find_job_board",
                trace=trace,
            )
        if not job_list_url:
            raise DiscoveryError(
                "job_board_not_found",
                "No verified job board was found from the career page.",
                step_name="find_job_board",
                trace=trace,
            )
        trace.pop("selected", None)
        trace.pop("opening_error", None)
        trace["job_list_page_url"] = job_list_url
        return job_list_url, trace, discovered_board

    def _has_native_listing_board(
        self,
        discovered_board: DiscoveredJobBoard | None,
    ) -> bool:
        if discovered_board is None:
            return False
        adapter = self.provider_registry.adapter_named(discovered_board.board.provider)
        return adapter is not None and adapter.supports_listing

    def _search_verified_ats_board(
        self,
        company_name: str,
        career_page_url: str,
        target_location: str | None = None,
    ) -> tuple[str | None, dict]:
        search_result = self._search_career_candidates(
            company_name,
            career_page_url,
            ats_only=True,
        )
        trace = {
            "search": search_result.trace,
            "candidates": [],
            "errors": [],
        }
        attempts = 0
        target_region = location_region(target_location)
        search_candidates = sorted(
            search_result.candidates,
            key=lambda candidate: self._target_region_priority(
                candidate.url,
                target_region,
            ),
            reverse=True,
        )
        for candidate in search_candidates:
            if attempts >= self.max_ats_board_fetches:
                trace["fetch_budget_exhausted"] = self.max_ats_board_fetches
                break
            adapter = self.provider_registry.adapter_for(candidate.url)
            board = adapter.identify_board(candidate.url) if adapter else None
            if adapter is None or board is None or not adapter.supports_listing:
                continue
            if not self._url_matches_target_region(candidate.url, target_region):
                trace.setdefault("regional_exclusions", []).append(
                    self._regional_exclusion(candidate.url, target_region)
                )
                continue
            attempts += 1
            try:
                result = adapter.list_jobs(self.fetcher, board, JobQuery())
            except FetchError as exc:
                trace["errors"].append(
                    {"url": candidate.url, **_fetch_failure_trace(exc)}
                )
                continue
            trace["candidates"].append(
                {
                    "url": candidate.url,
                    "provider": adapter.name,
                    "board_url": board.url,
                    "job_count": len(result.candidates),
                    "reason_code": result.reason_code,
                }
            )
            if not result.candidates:
                continue
            return self._canonical_provider_board_url(adapter.name, board.url, board.identifier), trace
        return None, trace

    def _canonical_provider_board_url(
        self,
        provider: str,
        board_url: str,
        identifier: str | None,
    ) -> str:
        if not identifier:
            return board_url
        if provider == "greenhouse":
            return f"https://job-boards.greenhouse.io/{identifier}"
        if provider == "lever":
            return f"https://jobs.lever.co/{identifier}"
        return board_url

    def match_opening(
        self,
        job_list_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        return self._match_opening(
            job_list_url,
            target_title,
            target_location,
            discovered_board=None,
        )

    def match_discovered_board(
        self,
        discovered_board: DiscoveredJobBoard,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        return self._match_opening(
            discovered_board.board.url,
            target_title,
            target_location,
            discovered_board=discovered_board,
        )

    def _match_opening(
        self,
        job_list_url: str,
        target_title: str | None,
        target_location: str | None,
        *,
        discovered_board: DiscoveredJobBoard | None,
    ) -> tuple[str | None, str, dict]:
        if self._looks_like_job_detail_url(job_list_url):
            return job_list_url, job_list_url, {
                "job_list_page_url": job_list_url,
                "selected": {
                    "url": job_list_url,
                    "reason": "job board is already a job-detail URL",
                },
            }
        if not target_title:
            opening_url, resolved_job_list_url, trace, _board = self._discover_job_board_legacy(
                job_list_url
            )
            if opening_url:
                return opening_url, resolved_job_list_url or job_list_url, trace
            trace["opening_error"] = "open_position_not_found"
            return None, resolved_job_list_url or job_list_url, trace
        match, trace = JobOpeningMatcher(self.fetcher, self.provider_registry).match(
            job_list_url,
            target_title,
            target_location,
            discovered_board=discovered_board,
        )
        if match:
            trace["selected"] = {
                "url": match.url,
                "title": match.title,
                "score": match.score,
                "provider": match.provider,
                "reasons": match.reasons,
            }
            return match.url, match.job_list_page_url or job_list_url, trace

        trace["opening_error"] = "specific_opening_not_found"
        return None, job_list_url, trace

    def find_open_position(
        self,
        career_page_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str | None, dict]:
        job_list_url, trace, discovered_board = self.find_job_board_with_evidence(
            career_page_url,
            target_location=target_location,
        )
        opening_url, resolved_job_list_url, match_trace = self._match_opening(
            job_list_url,
            target_title,
            target_location,
            discovered_board=discovered_board,
        )
        trace["job_list_page_url"] = resolved_job_list_url
        trace["opening_matcher"] = match_trace
        if match_trace.get("selected"):
            trace["selected"] = match_trace["selected"]
        if not opening_url:
            trace["opening_error"] = match_trace.get("opening_error") or "open_position_not_found"
        return opening_url, resolved_job_list_url, trace

    def _discover_job_board_legacy(
        self,
        career_page_url: str,
        target_location: str | None = None,
    ) -> tuple[str | None, str | None, dict, DiscoveredJobBoard | None]:
        target_region = location_region(target_location)
        if self._looks_like_job_detail_url(career_page_url):
            if not self._url_matches_target_region(career_page_url, target_region):
                return (
                    None,
                    None,
                    {
                        "career_page_url": career_page_url,
                        "job_list_page_url": None,
                        "target_region": target_region,
                        "regional_exclusions": [
                            self._regional_exclusion(career_page_url, target_region)
                        ],
                    },
                    None,
                )
            return (
                career_page_url,
                career_page_url,
                {
                    "career_page_url": career_page_url,
                    "job_list_page_url": career_page_url,
                    "selected": {
                        "url": career_page_url,
                        "reason": "career page is already a job-detail URL",
                    },
                },
                None,
            )

        trace = {
            "career_page_url": career_page_url,
            "job_list_page_url": None,
            "pages_visited": [],
            "candidates": [],
            "fetch_errors": [],
        }
        if target_region:
            trace["target_region"] = target_region

        queue: list[tuple[str, Page | None]] = [(career_page_url, None)]
        queued_candidates: dict[str, LinkCandidate] = {}
        asset_backed_provider_targets: set[str] = set()
        deferred_candidate_slices: dict[str, tuple[int, int]] = {}
        visited: set[str] = set()
        pages_checked = 0

        while queue:
            page_url, deferred_page = queue.pop(0)
            normalized_page_url = page_url.rstrip("/")
            incoming_candidate = queued_candidates.get(normalized_page_url)
            if not self._url_matches_target_region(page_url, target_region):
                trace.setdefault("regional_exclusions", []).append(
                    self._regional_exclusion(page_url, target_region)
                )
                continue
            if deferred_page is not None:
                page = deferred_page
            elif pages_checked >= self.max_job_pages:
                continue
            if normalized_page_url in visited:
                if deferred_page is None:
                    continue
            elif deferred_page is None:
                visited.add(normalized_page_url)
                pages_checked += 1

                try:
                    page = self.fetcher.fetch(page_url)
                except FetchError as exc:
                    failure = {"url": page_url, **_fetch_failure_trace(exc)}
                    if incoming_candidate is not None:
                        failure.update(
                            {
                                "origin": incoming_candidate.origin,
                                "score": incoming_candidate.score,
                                "evidence_tier": (
                                    1
                                    if incoming_candidate.origin
                                    in {"page_link", "form_action"}
                                    and (
                                        "explicit job-list command"
                                        in incoming_candidate.reasons
                                        or (
                                            target_region
                                            and url_region(incoming_candidate.url)
                                            == target_region
                                        )
                                    )
                                    else candidate_evidence_tier(incoming_candidate)
                                ),
                            }
                        )
                    elif normalized_page_url == career_page_url.rstrip("/"):
                        failure.update(
                            {
                                "origin": "verified_career_page",
                                "evidence_tier": 0,
                            }
                        )
                    trace["fetch_errors"].append(failure)
                    continue
                page, content_probe = probe_first_party_cms_payload(self.fetcher, page)
                if content_probe:
                    trace.setdefault("content_payload_probes", []).append(content_probe)

            actual_page_url = page.final_url or page.url
            normalized_actual_url = actual_page_url.rstrip("/")
            actual_page_compatible = self._url_matches_target_region(
                actual_page_url,
                target_region,
            )
            redirects_to_visited_page = (
                normalized_actual_url != normalized_page_url
                and normalized_actual_url in visited
            )
            page_board = (
                None
                if redirects_to_visited_page
                else self.provider_registry.board_for_page(page)
            )
            if page_board is not None and actual_page_compatible:
                adapter, board = page_board
                trace["provider"] = adapter.name
                trace["provider_detection"] = {
                    "method": "page_evidence",
                    "provider": adapter.name,
                    "url": board.url,
                }
                trace["job_list_page_url"] = board.url
                trace["pages_visited"].append(
                    {"url": actual_page_url, "source": page.source, "top_candidates": []}
                )
                return (
                    None,
                    board.url,
                    trace,
                    DiscoveredJobBoard(
                        board=board,
                        detection_method="page_evidence",
                        evidence_url=actual_page_url,
                    ),
                )

            if redirects_to_visited_page:
                pages_checked -= 1
                requested_adapter = self.provider_registry.adapter_for(page_url)
                requested_board = (
                    requested_adapter.identify_board(page_url)
                    if requested_adapter is not None
                    else None
                )
                preserve_provider_handoff = (
                    incoming_candidate is not None
                    and requested_adapter is not None
                    and requested_adapter.supports_listing
                    and requested_board is not None
                    and (
                        normalized_page_url in asset_backed_provider_targets
                        or (
                            incoming_candidate.origin in {"page_link", "form_action"}
                            and bool(incoming_candidate.text)
                        )
                    )
                )
                trace["pages_visited"].append(
                    {
                        "url": actual_page_url,
                        "requested_url": page_url,
                        "source": page.source,
                        "redirect_duplicate": True,
                        "provider_handoff_preserved": preserve_provider_handoff,
                        "top_candidates": [],
                    }
                )
                if preserve_provider_handoff:
                    canonical_board_url = self._canonical_provider_board_url(
                        requested_adapter.name,
                        requested_board.url,
                        requested_board.identifier,
                    )
                    trace["provider"] = requested_adapter.name
                    trace["provider_detection"] = {
                        "method": "redirected_linked_url_evidence",
                        "provider": requested_adapter.name,
                        "url": canonical_board_url,
                        "evidence_url": incoming_candidate.source_url,
                    }
                    trace["job_list_page_url"] = canonical_board_url
                    return (
                        None,
                        canonical_board_url,
                        trace,
                        DiscoveredJobBoard(
                            board=requested_board,
                            detection_method="redirected_linked_url_evidence",
                            evidence_url=incoming_candidate.source_url,
                        ),
                    )
                continue

            priority_deduped: list[LinkCandidate] = []
            can_prioritize_page_links = (
                deferred_page is None
                and normalized_actual_url == normalized_page_url
                and self._same_site_host(
                    urlparse(actual_page_url).hostname or "",
                    urlparse(career_page_url).hostname or "",
                )
            )
            if can_prioritize_page_links:
                priority_scored = sorted(
                    [
                        score_job_link(
                            self._upgrade_same_site_http_link(link, actual_page_url),
                            actual_page_url,
                        )
                        for link in extract_links(page)
                    ],
                    key=lambda candidate: candidate.score,
                    reverse=True,
                )
                priority_deduped = self._dedupe_candidates(priority_scored)
                priority_compatible = self._region_compatible_candidates(
                    priority_deduped,
                    target_region,
                    trace,
                )
                visible_provider_board = self._visible_canonical_provider_board(
                    priority_compatible
                )
                if visible_provider_board is not None:
                    adapter, board, candidate = visible_provider_board
                    trace["pages_visited"].append(
                        {
                            "url": actual_page_url,
                            "source": page.source,
                            "top_candidates": dataclass_to_dict(priority_deduped[:8]),
                        }
                    )
                    trace["candidates"].extend(dataclass_to_dict(priority_deduped[:5]))
                    trace["selected"] = dataclass_to_dict(candidate)
                    trace["provider"] = adapter.name
                    trace["provider_detection"] = {
                        "method": "linked_url_evidence",
                        "provider": adapter.name,
                        "url": board.url,
                    }
                    canonical_board_url = self._canonical_provider_board_url(
                        adapter.name,
                        board.url,
                        board.identifier,
                    )
                    trace["job_list_page_url"] = canonical_board_url
                    return None, canonical_board_url, trace, None

            if can_prioritize_page_links and pages_checked < self.max_job_pages:
                priority_candidate = self._strong_same_site_listing_candidate(
                    priority_compatible,
                    actual_page_url,
                    career_page_url,
                    target_region,
                )
                if (
                    priority_candidate is not None
                    and priority_candidate.url.rstrip("/") not in visited
                ):
                    queued_candidates.setdefault(
                        priority_candidate.url.rstrip("/"), priority_candidate
                    )
                    trace["pages_visited"].append(
                        {
                            "url": actual_page_url,
                            "source": page.source,
                            "top_candidates": dataclass_to_dict(priority_deduped[:8]),
                        }
                    )
                    serialized_candidates = dataclass_to_dict(priority_deduped[:5])
                    candidate_start = len(trace["candidates"])
                    trace["candidates"].extend(serialized_candidates)
                    deferred_candidate_slices[normalized_page_url] = (
                        candidate_start,
                        len(serialized_candidates),
                    )
                    queue.insert(0, (page_url, page))
                    queue.insert(0, (priority_candidate.url, None))
                    continue

            page, provider_asset_probe = probe_first_party_provider_assets(
                self.fetcher,
                page,
                self._is_provider_job_board_url,
            )
            if provider_asset_probe:
                trace.setdefault("content_payload_probes", []).append(provider_asset_probe)
            actual_page_url = page.final_url or page.url
            normalized_actual_url = actual_page_url.rstrip("/")
            actual_page_compatible = self._url_matches_target_region(
                actual_page_url,
                target_region,
            )
            visited.add(normalized_actual_url)
            page_board = self.provider_registry.board_for_page(page, self.fetcher)
            if page_board is not None and actual_page_compatible:
                adapter, board = page_board
                trace["provider"] = adapter.name
                trace["provider_detection"] = {
                    "method": "page_evidence",
                    "provider": adapter.name,
                    "url": board.url,
                }
                trace["job_list_page_url"] = board.url
                trace["pages_visited"].append(
                    {"url": actual_page_url, "source": page.source, "top_candidates": []}
                )
                return (
                    None,
                    board.url,
                    trace,
                    DiscoveredJobBoard(
                        board=board,
                        detection_method="page_evidence",
                        evidence_url=actual_page_url,
                    ),
                )
            url_adapter = self.provider_registry.adapter_for(actual_page_url)
            url_board = url_adapter.identify_board(actual_page_url) if url_adapter else None
            if (
                actual_page_compatible
                and url_adapter is not None
                and url_board is not None
                and url_adapter.supports_listing
            ):
                canonical_board_url = url_board.url
                trace["provider"] = url_adapter.name
                trace["provider_detection"] = {
                    "method": "url_evidence",
                    "provider": url_adapter.name,
                    "url": canonical_board_url,
                }
                trace["job_list_page_url"] = canonical_board_url
            elif actual_page_compatible and self._is_provider_job_board_url(actual_page_url):
                trace["job_list_page_url"] = actual_page_url
            elif not self._same_site_host(
                urlparse(actual_page_url).hostname or "",
                urlparse(career_page_url).hostname or "",
            ):
                trace["pages_visited"].append(
                    {
                        "url": actual_page_url,
                        "source": page.source,
                        "top_candidates": [],
                        "rejected_cross_site_page": True,
                    }
                )
                continue
            empty_evidence = explicit_empty_inventory_evidence(page.html)
            if empty_evidence:
                trace["explicit_empty_inventory"] = {
                    "url": actual_page_url,
                    "source": page.source,
                    "phrase": empty_evidence,
                }
            scored = sorted(
                [
                    score_job_link(
                        self._upgrade_same_site_http_link(link, actual_page_url),
                        actual_page_url,
                    )
                    for link in extract_links(page)
                ],
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            deduped = self._dedupe_candidates(scored)
            compatible_candidates = self._region_compatible_candidates(
                deduped,
                target_region,
                trace,
            )
            if provider_asset_probe:
                for candidate in deduped:
                    if self._provider_asset_confirms_candidate(
                        provider_asset_probe,
                        candidate,
                    ):
                        asset_backed_provider_targets.add(candidate.url.rstrip("/"))
            verified_generic_listing = self._looks_like_generic_job_list_route(
                actual_page_url
            ) or (
                incoming_candidate is not None
                and "explicit all-jobs route" in incoming_candidate.reasons
                and normalize_url(incoming_candidate.url) == normalize_url(actual_page_url)
            )
            if (
                actual_page_compatible
                and self._has_job_list_evidence(actual_page_url, compatible_candidates)
                and not trace["job_list_page_url"]
            ):
                trace["job_list_page_url"] = actual_page_url
            elif actual_page_compatible and verified_generic_listing:
                trace["job_list_page_url"] = actual_page_url
            visited_page = {
                "url": actual_page_url,
                "source": page.source,
                "top_candidates": dataclass_to_dict(deduped[:8]),
            }
            if deferred_page is not None and normalized_page_url in deferred_candidate_slices:
                existing_page = next(
                    (
                        item
                        for item in reversed(trace["pages_visited"])
                        if item["url"].rstrip("/") == normalized_page_url
                    ),
                    None,
                )
                if existing_page is not None:
                    existing_page.update(visited_page)
            else:
                trace["pages_visited"].append(visited_page)
            serialized_candidates = dataclass_to_dict(deduped[:5])
            if deferred_page is not None and normalized_page_url in deferred_candidate_slices:
                candidate_start, candidate_count = deferred_candidate_slices[
                    normalized_page_url
                ]
                trace["candidates"][
                    candidate_start : candidate_start + candidate_count
                ] = serialized_candidates
            else:
                trace["candidates"].extend(serialized_candidates)
            if actual_page_compatible and verified_generic_listing:
                trace["selected_from"] = "explicit_first_party_listing_route"
                trace["selected_page_source"] = page.source
                return None, actual_page_url, trace, None

            visible_linked_provider_board = self._visible_canonical_provider_board(
                compatible_candidates
            )
            linked_provider_board = visible_linked_provider_board or next(
                (
                    (adapter, board, candidate)
                    for candidate in compatible_candidates
                    if url_adapter is None or url_board is None
                    if not candidate.text
                    and is_likely_job_detail(candidate)
                    if (adapter := self.provider_registry.adapter_for(candidate.url)) is not None
                    and adapter.supports_listing
                    and (board := adapter.identify_board(candidate.url)) is not None
                ),
                None,
            )
            if linked_provider_board is not None:
                adapter, board, candidate = linked_provider_board
                trace["selected"] = dataclass_to_dict(candidate)
                trace["provider"] = adapter.name
                trace["provider_detection"] = {
                    "method": "linked_url_evidence",
                    "provider": adapter.name,
                    "url": board.url,
                }
                canonical_board_url = self._canonical_provider_board_url(
                    adapter.name,
                    board.url,
                    board.identifier,
                )
                trace["job_list_page_url"] = canonical_board_url
                return None, canonical_board_url, trace, None

            official_portal = next(
                (
                    candidate
                    for candidate in compatible_candidates
                    if self._is_first_party_job_portal(candidate, actual_page_url)
                ),
                None,
            )
            if official_portal is not None:
                trace["selected"] = dataclass_to_dict(official_portal)
                trace["selected_page_source"] = "first_party_portal_link"
                trace["job_list_page_url"] = official_portal.url
                return None, official_portal.url, trace, None

            traversal_candidates = sorted(
                compatible_candidates[: self.max_candidates],
                key=lambda candidate: (
                    self._target_region_priority(candidate.url, target_region),
                    candidate.score,
                    self._career_category_priority(
                        candidate,
                        actual_page_url,
                        career_page_url,
                    ),
                    self._shared_path_prefix(candidate.url, actual_page_url),
                ),
                reverse=True,
            )
            for candidate in traversal_candidates:
                if candidate.score < 55:
                    continue
                if (
                    (url_adapter is None or url_board is None)
                    and not candidate.text
                    and self._has_listing_provider_adapter(candidate.url)
                ):
                    continue
                if not is_likely_job_detail(candidate):
                    continue
                try:
                    opened = self.fetcher.fetch(candidate.url)
                except FetchError as exc:
                    trace["fetch_errors"].append(
                        {"url": candidate.url, **_fetch_failure_trace(exc)}
                    )
                    continue
                trace["selected"] = dataclass_to_dict(candidate)
                trace["job_list_page_url"] = actual_page_url
                trace["selected_page_source"] = opened.source
                return opened.final_url or opened.url, actual_page_url, trace, None

            for candidate in traversal_candidates:
                if (
                    (
                        is_likely_job_listing_page(candidate)
                        or self._looks_like_generic_job_list_route(candidate.url)
                        or self._has_listing_provider_adapter(candidate.url)
                        or self._career_category_priority(
                            candidate,
                            actual_page_url,
                            career_page_url,
                        )
                    )
                    and self._is_safe_traversal_target(candidate, actual_page_url)
                    and candidate.url.rstrip("/") not in visited
                ):
                    queued_candidates.setdefault(candidate.url.rstrip("/"), candidate)
                    queue.append((candidate.url, None))

        return None, trace["job_list_page_url"], trace, None

    def _strong_same_site_listing_candidate(
        self,
        candidates: list[LinkCandidate],
        source_url: str,
        career_root_url: str,
        target_region: str | None = None,
    ) -> LinkCandidate | None:
        source_host = urlparse(source_url).hostname or ""
        bounded = candidates[: self.max_candidates]
        prioritized = sorted(
            bounded,
            key=lambda candidate: (
                self._target_region_priority(candidate.url, target_region),
                candidate.score,
                self._career_category_priority(
                    candidate,
                    source_url,
                    career_root_url,
                ),
                self._shared_path_prefix(candidate.url, source_url),
            ),
            reverse=True,
        )
        for candidate in prioritized:
            target_host = urlparse(candidate.url).hostname or ""
            if (
                target_host
                and self._same_site_host(target_host, source_host)
                and self._looks_like_generic_job_list_route(candidate.url)
                and is_likely_job_listing_page(candidate)
                and self._is_safe_traversal_target(candidate, source_url)
            ):
                return candidate
        return None

    @staticmethod
    def _target_region_priority(url: str, target_region: str | None) -> int:
        if not target_region:
            return 0
        return 1 if url_region(url) == target_region else 0

    @staticmethod
    def _url_matches_target_region(url: str, target_region: str | None) -> bool:
        candidate_region = url_region(url)
        return not target_region or not candidate_region or candidate_region == target_region

    @staticmethod
    def _regional_exclusion(url: str, target_region: str | None) -> dict:
        return {
            "url": url,
            "candidate_region": url_region(url),
            "target_region": target_region,
            "reason": "conflicts_with_target_region",
        }

    def _region_compatible_candidates(
        self,
        candidates: list[LinkCandidate],
        target_region: str | None,
        trace: dict | None = None,
    ) -> list[LinkCandidate]:
        if not target_region:
            return candidates
        compatible: list[LinkCandidate] = []
        for candidate in candidates:
            candidate_region = url_region(candidate.url)
            if candidate_region and candidate_region != target_region:
                reason = (
                    f"conflicts with target location region '{target_region}': "
                    f"'{candidate_region}'"
                )
                if reason not in candidate.reasons:
                    candidate.reasons.append(reason)
                if trace is not None:
                    exclusion = self._regional_exclusion(candidate.url, target_region)
                    exclusions = trace.setdefault("regional_exclusions", [])
                    if exclusion not in exclusions:
                        exclusions.append(exclusion)
                continue
            if candidate_region == target_region:
                reason = f"matches target location region '{target_region}'"
                if reason not in candidate.reasons:
                    candidate.reasons.append(reason)
            compatible.append(candidate)
        return sorted(
            compatible,
            key=lambda candidate: self._target_region_priority(
                candidate.url,
                target_region,
            ),
            reverse=True,
        )

    def _visible_canonical_provider_board(
        self,
        candidates: list[LinkCandidate],
    ) -> tuple[ProviderAdapter, JobBoard, LinkCandidate] | None:
        return next(
            (
                (adapter, board, candidate)
                for candidate in candidates
                if candidate.origin == "page_link" and candidate.text
                if (adapter := self.provider_registry.adapter_for(candidate.url)) is not None
                and adapter.supports_listing
                and (board := adapter.identify_board(candidate.url)) is not None
                and self._visible_url_matches_canonical_board(
                    candidate.url,
                    self._canonical_provider_board_url(
                        adapter.name,
                        board.url,
                        board.identifier,
                    ),
                )
            ),
            None,
        )

    @staticmethod
    def _visible_url_matches_canonical_board(
        candidate_url: str,
        canonical_url: str,
    ) -> bool:
        normalized_candidate = normalize_url(candidate_url)
        normalized_canonical = normalize_url(canonical_url)
        if normalized_candidate == normalized_canonical:
            return True

        candidate = urlparse(normalized_candidate)
        canonical = urlparse(normalized_canonical)
        return bool(
            candidate.query
            and not canonical.query
            and (candidate.scheme, candidate.netloc, candidate.path)
            == (canonical.scheme, canonical.netloc, canonical.path)
        )

    def _career_category_priority(
        self,
        candidate: LinkCandidate,
        source_url: str,
        career_root_url: str,
    ) -> int:
        target = urlparse(candidate.url)
        source = urlparse(source_url)
        root = urlparse(career_root_url)
        if target.hostname != source.hostname or source.hostname != root.hostname:
            return 0
        target_parts = [part.casefold() for part in target.path.split("/") if part]
        root_parts = [part.casefold() for part in root.path.split("/") if part]
        if (
            not root_parts
            or target_parts[: len(root_parts)] != root_parts
            or len(target_parts) <= len(root_parts)
            or len(target_parts) > len(root_parts) + 2
        ):
            return 0
        label = " ".join(
            (candidate.text + " " + target_parts[-1].replace("-", " "))
            .casefold()
            .split()
        )
        if any(marker in label for marker in ("staff", "business services", "professionals")):
            return 3
        if any(marker in label for marker in ("lateral", "student", "graduate", "clerk", "attorney")):
            return 2
        return 0

    def _is_first_party_job_portal(
        self,
        candidate: LinkCandidate,
        source_url: str,
    ) -> bool:
        target = urlparse(candidate.url)
        source = urlparse(source_url)
        if not target.hostname or not source.hostname or target.hostname == source.hostname:
            return False
        if self._registrable_site(target.hostname) != self._registrable_site(source.hostname):
            return False
        target_label = target.hostname.split(".", 1)[0].casefold()
        text = " ".join(candidate.text.casefold().split())
        return (
            any(marker in target_label for marker in ("jobs", "careers", "apply"))
            and any(
                phrase in text
                for phrase in (
                    "job opportunities",
                    "job search",
                    "open positions",
                    "search jobs",
                    "staff careers",
                    "view jobs",
                )
            )
        )

    def _shared_path_prefix(self, target_url: str, source_url: str) -> int:
        target_parts = [part.casefold() for part in urlparse(target_url).path.split("/") if part]
        source_parts = [part.casefold() for part in urlparse(source_url).path.split("/") if part]
        shared = 0
        for target_part, source_part in zip(target_parts, source_parts):
            if target_part != source_part:
                break
            shared += 1
        return shared

    def _is_safe_traversal_target(self, candidate: LinkCandidate, source_url: str) -> bool:
        url = candidate.url
        if is_resource_url(url):
            return False
        parsed_target = urlparse(url)
        if parsed_target.username or parsed_target.password:
            return False
        try:
            if parsed_target.port not in {None, 80, 443}:
                return False
        except ValueError:
            return False
        target_host = parsed_target.hostname or ""
        source_host = urlparse(source_url).hostname or ""
        if not target_host or not source_host:
            return False
        return (
            self._is_provider_job_board_url(url)
            or is_ats_url(url)
            or self._same_site_host(target_host, source_host)
            or self._is_explicit_cross_site_job_portal(candidate)
        )

    def _is_explicit_cross_site_job_portal(self, candidate: LinkCandidate) -> bool:
        parsed = urlparse(candidate.url)
        path_parts = [part.casefold() for part in parsed.path.split("/") if part]
        return (
            parsed.scheme == "https"
            and bool(path_parts)
            and path_parts[-1] in {"careers", "jobs", "openings", "positions", "search-results"}
            and "explicit job-list command" in candidate.reasons
            and is_likely_job_listing_page(candidate)
        )

    def _same_site_host(self, first: str, second: str) -> bool:
        if first == second or first.endswith("." + second) or second.endswith("." + first):
            return True
        return self._registrable_site(first) == self._registrable_site(second)

    def _registrable_site(self, host: str) -> str:
        parts = host.casefold().strip(".").split(".")
        if len(parts) <= 2:
            return ".".join(parts)
        two_level_suffixes = {"co.uk", "com.au", "com.br", "com.sg", "co.jp", "co.nz"}
        suffix = ".".join(parts[-2:])
        return ".".join(parts[-3:]) if suffix in two_level_suffixes else suffix

    def _has_job_list_evidence(self, page_url: str, candidates: list[LinkCandidate]) -> bool:
        if self._is_provider_job_board_url(page_url):
            return True
        return any(is_likely_job_detail(candidate) for candidate in candidates)

    def _provider_asset_confirms_candidate(
        self,
        probe: dict,
        candidate: LinkCandidate,
    ) -> bool:
        candidate_adapter = self.provider_registry.adapter_for(candidate.url)
        candidate_board = (
            candidate_adapter.identify_board(candidate.url)
            if candidate_adapter is not None
            else None
        )
        if (
            candidate_adapter is None
            or not candidate_adapter.supports_listing
            or candidate_board is None
            or not candidate_board.identifier
        ):
            return False
        provider_urls = probe.get("provider_urls")
        if not isinstance(provider_urls, list):
            return False
        synthetic = Page(
            url=candidate.source_url,
            html="\n".join(url for url in provider_urls if isinstance(url, str)),
        )
        for link in extract_links(synthetic):
            adapter = self.provider_registry.adapter_for(link.url)
            board = adapter.identify_board(link.url) if adapter is not None else None
            if (
                adapter is not None
                and board is not None
                and adapter.name == candidate_adapter.name
                and board.identifier == candidate_board.identifier
            ):
                return True
        return False

    def _looks_like_generic_job_list_route(self, url: str) -> bool:
        parts = [part.casefold() for part in urlparse(url).path.split("/") if part]
        if not parts or any(part in NON_JOB_BOARD_PATH_PARTS for part in parts):
            return False
        leaf = parts[-1]
        if leaf in {
            "jobs",
            "positions",
            "openings",
            "job-openings",
            "job-results",
            "search-results",
        }:
            return True
        return any(
            marker in leaf
            for marker in (
                "job-results",
                "job-search",
                "jobs-search",
                "career-opportunities-search",
            )
        )

    def _is_provider_job_board_url(self, url: str) -> bool:
        if is_resource_url(url):
            return False
        provider = self.provider_registry.detect(url)
        if provider == "generic":
            return is_ats_url(url)
        parts = [part.lower() for part in urlparse(url).path.split("/") if part]
        if provider == "workday":
            return bool(parts) and not any(part in NON_JOB_BOARD_PATH_PARTS for part in parts)
        if provider == "bamboohr":
            return parts == ["careers"]
        if provider == "rippling":
            return "embed" in parts and "jobs" in parts
        return True

    def _has_listing_provider_adapter(self, url: str) -> bool:
        adapter = self.provider_registry.adapter_for(url)
        return bool(
            adapter is not None
            and adapter.supports_listing
            and adapter.identify_board(url) is not None
        )

    def _common_path_candidates(self, homepage_url: str) -> list[RawLink]:
        parsed = urlparse(homepage_url)
        candidates = []
        base_urls = self._base_url_variants(parsed.scheme, parsed.netloc)
        locale_paths = self._locale_career_paths(parsed.path)
        for base in base_urls:
            candidates.extend(
                RawLink(
                    url=normalize_url(path, base),
                    text="",
                    source_url=homepage_url,
                    origin="path_probe",
                )
                for path in COMMON_CAREER_PATHS + tuple(locale_paths)
            )
        brand_label = self._brand_label_from_host(parsed.netloc)
        if brand_label:
            for base in base_urls:
                for path in (f"/join-{brand_label}", f"/en/join-{brand_label}", f"/en-us/join-{brand_label}"):
                    candidates.append(
                        RawLink(
                            url=normalize_url(path, base),
                            text="",
                            source_url=homepage_url,
                            origin="path_probe",
                        )
                    )
        root_domain = parsed.netloc.lower().removeprefix("www.")
        for subdomain in ("careers", "jobs"):
            candidates.append(
                RawLink(
                    url=normalize_url(f"https://{subdomain}.{root_domain}"),
                    text="",
                    source_url=homepage_url,
                    origin="subdomain_probe",
                )
            )
        return candidates

    def _ats_board_candidates(self, company_name: str, homepage_url: str) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        for slug in self._ats_slug_candidates(company_name, homepage_url):
            urls = (
                (f"https://jobs.smartrecruiters.com/{slug}", "SmartRecruiters"),
                (f"https://jobs.lever.co/{slug}", "Lever"),
                (f"https://boards.greenhouse.io/{slug}", "Greenhouse"),
                (f"https://jobs.ashbyhq.com/{slug}", "Ashby"),
                (f"https://{slug}.breezy.hr/", "Breezy"),
                (f"https://apply.workable.com/{slug}", "Workable"),
                (f"https://{slug}.bamboohr.com/careers", "BambooHR"),
                (f"https://ats.rippling.com/embed/{slug}/jobs", "Rippling"),
            )
            candidates.extend(
                LinkCandidate(
                    url=url,
                    text="",
                    source_url=homepage_url,
                    score=180,
                    reasons=[f"derived {provider} board candidate", f"company slug '{slug}'"],
                    origin="blind_ats_probe",
                )
                for url, provider in urls
            )
        return self._dedupe_candidates(candidates)

    def _ats_slug_candidates(self, company_name: str, homepage_url: str) -> list[str]:
        name_tokens = re.findall(r"[a-z0-9]+", company_name.lower())
        ignored_tokens = {"inc", "llc", "ltd", "corp", "corporation", "company", "co"}
        name_tokens = [token for token in name_tokens if token not in ignored_tokens]
        host_label = self._brand_label_from_host(urlparse(homepage_url).netloc)
        candidates = ["".join(name_tokens), "-".join(name_tokens), host_label or ""]
        seen: set[str] = set()
        return [candidate for candidate in candidates if candidate and not (candidate in seen or seen.add(candidate))]

    def _base_url_variants(self, scheme: str, netloc: str) -> list[str]:
        host = netloc.lower()
        hosts = [host]
        if host and not host.startswith("www."):
            hosts.append(f"www.{host}")
        return [f"{scheme}://{candidate}" for candidate in hosts if candidate]

    def _locale_career_paths(self, path: str) -> list[str]:
        first_part = next((part for part in path.split("/") if part), "")
        if not first_part or not self._looks_like_locale_path(first_part):
            return []
        return [
            f"/{first_part}/careers",
            f"/{first_part}/career",
            f"/{first_part}/jobs",
            f"/{first_part}/careers/jobs",
            f"/{first_part}/company/careers",
            f"/{first_part}/about/careers",
            f"/{first_part}/about-us/careers",
        ]

    def _looks_like_locale_path(self, value: str) -> bool:
        parts = value.lower().split("-")
        return len(parts) in {1, 2} and all(len(part) == 2 and part.isalpha() for part in parts)

    def _search_career_candidates(
        self,
        company_name: str,
        homepage_url: str,
        *,
        ats_only: bool = False,
    ):
        original_timeout = getattr(self.fetcher, "timeout", None)
        if self.career_search_timeout and original_timeout and self.career_search_timeout > original_timeout:
            self.fetcher.timeout = self.career_search_timeout
        try:
            return CareerSearchResolver(
                self.fetcher,
                max_queries=self.max_career_search_queries,
            ).search(company_name, homepage_url, ats_only=ats_only)
        finally:
            if original_timeout is not None:
                self.fetcher.timeout = original_timeout

    def _score_career_candidate(
        self,
        link: RawLink,
        homepage_url: str | None = None,
        *,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> LinkCandidate:
        original_url = link.url
        link = self._upgrade_same_site_http_link(link, homepage_url)
        upgraded_same_site_http = link.url != original_url
        candidate = score_career_link(link)
        path_parts = [part.lower() for part in urlparse(link.url).path.split("/") if part]
        normalized_text = " ".join(link.text.casefold().split())
        if homepage_url and normalize_url(link.url) == normalize_url(homepage_url):
            candidate.score -= 250
            candidate.reasons.append("homepage self-link")
        if upgraded_same_site_http:
            candidate.reasons.append("upgraded same-site HTTP link to HTTPS")
        if normalized_text in {"career home", "careers home"} or urlparse(link.url).path.casefold().endswith(
            "/careers/careers.html"
        ):
            candidate.score += 140
            candidate.reasons.append("explicit career landing root")
        audience_mismatch = _career_audience_mismatch(link.url, link.text, target_title)
        if audience_mismatch:
            candidate.score -= 220
            candidate.reasons.append(f"career audience mismatch: {audience_mismatch}")
        homepage_locale = _leading_locale_segment(homepage_url)
        candidate_locale = _leading_locale_segment(link.url)
        target_region = location_region(target_location)
        candidate_region = url_region(link.url)
        if target_region and candidate_region:
            if candidate_region == target_region:
                candidate.score += 180
                candidate.reasons.append(f"matches target location region '{target_region}'")
            else:
                candidate.score -= 300
                candidate.reasons.append(
                    f"conflicts with target location region '{target_region}': '{candidate_region}'"
                )
        if (
            homepage_locale
            and candidate_locale
            and domain_of(link.url) == domain_of(homepage_url or "")
        ):
            if homepage_locale == candidate_locale:
                candidate.score += 100
                candidate.reasons.append(f"matches homepage locale '{homepage_locale}'")
            else:
                candidate.score -= 500
                candidate.reasons.append(
                    f"conflicts with homepage locale '{homepage_locale}': '{candidate_locale}'"
                )
        if self._is_concise_career_path(path_parts):
            candidate.score += 120
            candidate.reasons.append("concise career root path")
        if self._is_localized_career_section(path_parts):
            candidate.score += 35
            candidate.reasons.append("localized career section")
        if (
            link.origin != "path_probe"
            and self._looks_like_generic_job_list_route(link.url)
        ):
            candidate.score += 220
            candidate.reasons.append("explicit job-list route")
        if link.origin in {"page_link", "verified_homepage_navigation"}:
            candidate.score += 110
            candidate.reasons.append("homepage navigation link")
        elif link.origin == "identity_career_root":
            candidate.score += 600
            candidate.reasons.append("identity-supplied career root requiring verification")
        elif link.origin == "derived_provider_config":
            candidate.score += 200
            candidate.reasons.append("derived provider configuration")
        elif link.origin == "path_probe":
            join_path = path_parts[-1] if path_parts else ""
            is_brand_join_path = join_path.startswith("join-")
            candidate.score -= 40 if is_brand_join_path else 75
            candidate.reasons.append("generated path probe")
            if is_brand_join_path and join_path not in {"join-us", "join-our-team"}:
                candidate.score += 100
                candidate.reasons.append("brand-specific join path")
        if link.origin in {"page_link", "verified_homepage_navigation"} and path_parts == ["team"]:
            candidate.score += 200
            candidate.reasons.append("homepage team link requiring employment evidence")
        source_path = urlparse(link.source_url).path.lower()
        if "sitemap" in source_path or source_path.endswith(".xml"):
            candidate.score += 150
            candidate.reasons.append("sitemap source")
        return candidate

    def _upgrade_same_site_http_link(
        self,
        link: RawLink,
        source_url: str | None,
    ) -> RawLink:
        if not source_url:
            return link
        source = urlparse(source_url)
        target = urlparse(link.url)
        if (
            source.scheme != "https"
            or target.scheme != "http"
            or not self._same_site_host(target.hostname or "", source.hostname or "")
        ):
            return link
        return RawLink(
            url=target._replace(scheme="https").geturl(),
            text=link.text,
            source_url=link.source_url,
            origin=link.origin,
        )

    def _is_concise_career_path(self, path_parts: list[str]) -> bool:
        if len(path_parts) == 1:
            return path_parts[0] in {"career", "careers", "jobs", "job-openings", "open-positions"} or path_parts[0].startswith("join-")
        if len(path_parts) == 2:
            return (
                self._looks_like_locale_path(path_parts[0])
                and path_parts[1] in {"career", "careers", "jobs"}
            ) or path_parts[1].startswith("join-")
        if len(path_parts) == 3:
            return self._is_localized_career_section(path_parts)
        return False

    def _is_localized_career_section(self, path_parts: list[str]) -> bool:
        return (
            len(path_parts) == 3
            and self._looks_like_locale_path(path_parts[0])
            and path_parts[1] in {"company", "about", "about-us"}
            and path_parts[2] == "careers"
        )

    def _select_verified_career_candidate(
        self,
        candidates: list[LinkCandidate],
        trace: dict,
        max_fetches: int | None = None,
        target_title: str | None = None,
        schedule_source: str = "candidate_selection",
        company_name: str | None = None,
        homepage_url: str | None = None,
        attempted_candidate_urls: set[str] | None = None,
    ) -> str | None:
        with self._career_transport_phase(f"{schedule_source}_candidates"):
            return self._select_verified_career_candidate_in_phase(
                candidates,
                trace,
                max_fetches=max_fetches,
                target_title=target_title,
                schedule_source=schedule_source,
                company_name=company_name,
                homepage_url=homepage_url,
                attempted_candidate_urls=attempted_candidate_urls,
            )

    def _select_verified_career_candidate_in_phase(
        self,
        candidates: list[LinkCandidate],
        trace: dict,
        max_fetches: int | None = None,
        target_title: str | None = None,
        schedule_source: str = "candidate_selection",
        company_name: str | None = None,
        homepage_url: str | None = None,
        attempted_candidate_urls: set[str] | None = None,
    ) -> str | None:
        if attempted_candidate_urls:
            candidates = [
                candidate
                for candidate in candidates
                if self._career_candidate_key(candidate.url) not in attempted_candidate_urls
            ]
        fetch_attempts = 0
        fetch_limit = self.max_career_candidate_fetches if max_fetches is None else max_fetches
        scheduled_fetch_limit = min(self.max_candidates, fetch_limit)
        scheduled_candidates, schedule_trace = schedule_career_candidates(
            candidates,
            fetch_limit=scheduled_fetch_limit,
        )
        bounded_candidates = scheduled_candidates[:scheduled_fetch_limit]
        untried_candidates = scheduled_candidates[len(bounded_candidates):]
        untried_evidence_backed = [
            candidate
            for candidate in untried_candidates
            if candidate_evidence_tier(candidate) <= 2
        ]
        roles_by_url = schedule_trace.pop("roles_by_url")
        original_positions = {
            candidate.url: position
            for position, candidate in enumerate(candidates)
        }
        candidate_schedule = {
            **schedule_trace,
            "source": schedule_source,
            "max_candidates": self.max_candidates,
            "scheduled_count": len(bounded_candidates),
            "bounded_count": len(bounded_candidates),
            "max_candidates_truncated_count": max(0, len(scheduled_candidates) - len(bounded_candidates)),
            "scheduled": [
                {
                    "url": candidate.url,
                    "schedule_position": schedule_position,
                    "original_position": original_positions[candidate.url],
                    "score": candidate.score,
                    "origin": candidate.origin,
                    "evidence_tier": candidate_evidence_tier(candidate),
                    "host_family": candidate_host_family(candidate),
                    "concrete_host": candidate_concrete_host(candidate.url),
                    "locale_key": candidate_locale_key(candidate.url),
                    "route_family": candidate_route_family(candidate),
                    "family_role": roles_by_url[candidate.url],
                }
                for schedule_position, candidate in enumerate(bounded_candidates)
            ],
        }
        trace["candidate_schedule"] = candidate_schedule
        trace.setdefault("candidate_schedules", []).append(candidate_schedule)
        for candidate in bounded_candidates:
            fetch_attempts += 1
            if attempted_candidate_urls is not None:
                attempted_candidate_urls.add(self._career_candidate_key(candidate.url))
            derived_reasons = [reason for reason in candidate.reasons if reason.startswith("derived ")]
            if derived_reasons:
                adapter_decision = self._verify_derived_provider_with_adapter(
                    candidate.url,
                    target_title=target_title,
                    trusted_configuration="derived provider configuration" in derived_reasons,
                )
                if adapter_decision is not None:
                    verified_url, verification = adapter_decision
                    trace.setdefault("provider_board_verification", []).append(verification)
                    if verified_url:
                        trace["selected"] = dataclass_to_dict(candidate)
                        trace["selected_page_source"] = "provider_adapter"
                        return verified_url
                    trace["candidate_fetch_errors"].append(
                        {"url": candidate.url, "error": "derived provider adapter rejected tenant or title"}
                    )
                    continue
            try:
                page = self.fetcher.fetch(candidate.url)
            except FetchError as exc:
                verified_provider = self._verify_provider_candidate_without_page(
                    candidate.url,
                    target_title,
                )
                if verified_provider is not None:
                    trace.setdefault("provider_board_verification", []).append(
                        verified_provider[1]
                    )
                    trace["selected"] = dataclass_to_dict(candidate)
                    trace["selected_page_source"] = "provider_adapter"
                    return verified_provider[0]
                reason_code = exc.reason_code or classify_fetch_error(str(exc))
                retryable = (
                    exc.retryable
                    if exc.retryable is not None
                    else reason_spec(reason_code).retryable
                )
                trace["candidate_fetch_errors"].append(
                    {
                        "url": candidate.url,
                        "error": str(exc),
                        "reason_code": reason_code,
                        "reason_code_source": (
                            "exception" if exc.reason_code is not None else "classified_message"
                        ),
                        "retryable": retryable,
                        "origin": candidate.origin,
                        "evidence_tier": candidate_evidence_tier(candidate),
                    }
                )
                continue
            actual_url = page.final_url or page.url
            if self._looks_like_error_page(actual_url, page.html):
                trace["candidate_fetch_errors"].append({"url": candidate.url, "error": f"error page: {actual_url}"})
                continue
            if not self._same_site_host(
                urlparse(actual_url).hostname or "",
                urlparse(candidate.url).hostname or "",
            ):
                url_adapter = self.provider_registry.adapter_for(actual_url)
                url_board = url_adapter.identify_board(actual_url) if url_adapter else None
                page_board = self.provider_registry.board_for_page(page)
                if url_board is not None and url_adapter is not None and url_adapter.supports_listing:
                    trace["selected"] = dataclass_to_dict(candidate)
                    trace["selected_page_source"] = "provider_adapter"
                    trace["redirect_provider_detection"] = {
                        "method": "url_evidence",
                        "provider": url_adapter.name,
                        "url": url_board.url,
                    }
                    return url_board.url
                if page_board is not None and page_board[0].supports_listing:
                    adapter, board = page_board
                    trace["selected"] = dataclass_to_dict(candidate)
                    trace["selected_page_source"] = "provider_adapter"
                    trace["redirect_provider_detection"] = {
                        "method": "page_evidence",
                        "provider": adapter.name,
                        "url": board.url,
                    }
                    return board.url
                redirect_verification = self._verify_generic_official_career_redirect(
                    candidate,
                    page,
                    company_name=company_name,
                    homepage_url=homepage_url,
                )
                trace.setdefault("generic_career_redirect_verification", []).append(
                    redirect_verification
                )
                if redirect_verification["verified"]:
                    trace["selected"] = dataclass_to_dict(candidate)
                    trace["selected_page_source"] = page.source
                    trace["selected_redirect_kind"] = "generic_official_career_root"
                    return actual_url
                trace["candidate_fetch_errors"].append(
                    {
                        "url": candidate.url,
                        "error": f"unverified cross-site redirect: {actual_url}",
                    }
                )
                continue
            page, content_probe = probe_first_party_cms_payload(self.fetcher, page)
            if content_probe:
                trace.setdefault("content_payload_probes", []).append(content_probe)
            if derived_reasons:
                verified, verification = self._verify_derived_provider_board(
                    actual_url,
                    page.html,
                    target_title=target_title,
                    trusted_configuration="derived provider configuration" in derived_reasons,
                )
                trace.setdefault("provider_board_verification", []).append(verification)
                if not verified:
                    trace["candidate_fetch_errors"].append(
                        {"url": candidate.url, "error": "derived provider board lacked job or API evidence"}
                    )
                    continue
            if (
                "generated path probe" not in candidate.reasons
                and self._looks_like_generic_job_list_route(actual_url)
                and self._same_site_host(
                    urlparse(actual_url).hostname or "",
                    urlparse(candidate.source_url).hostname or "",
                )
            ):
                trace["selected"] = dataclass_to_dict(candidate)
                trace["selected_page_source"] = page.source
                return actual_url
            if self._looks_like_career_page(candidate, page.html):
                trace["selected"] = dataclass_to_dict(candidate)
                trace["selected_page_source"] = page.source
                return actual_url
        if untried_evidence_backed:
            trace["candidate_fetch_budget_exhausted"] = {
                "limit": scheduled_fetch_limit,
                "remaining_candidates": len(untried_candidates),
                "remaining_bounded_candidates": 0,
                "untried_evidence_backed_count": len(untried_evidence_backed),
            }
        return None

    def _career_candidate_key(self, url: str) -> str:
        return normalize_url(url).rstrip("/")

    def _career_transport_phase(self, name: str):
        phase = getattr(self.fetcher, "career_discovery_phase", None)
        return (
            phase(name)
            if callable(phase) and self._career_transport_scope_active
            else nullcontext()
        )

    def _career_transport_budget_trace(
        self,
        budget,
        cache_hits_before: int,
    ) -> dict:
        snapshot = budget.snapshot()
        cache_hits_after = int(getattr(self.fetcher, "cache_hits", 0) or 0)
        return {
            "policy": "stage_transport_dispatch_budget",
            **snapshot,
            "cache_hits": max(0, cache_hits_after - cache_hits_before),
        }

    def _verify_generic_official_career_redirect(
        self,
        candidate: LinkCandidate,
        page: Page,
        *,
        company_name: str | None,
        homepage_url: str | None,
    ) -> dict:
        actual_url = page.final_url or page.url
        verification = {
            "candidate_url": candidate.url,
            "final_url": actual_url,
            "verified": False,
            "kind": "generic_official_career_root",
        }

        source_url = homepage_url or candidate.source_url
        source = urlparse(source_url)
        requested = urlparse(candidate.url)
        final = urlparse(actual_url)
        unsafe_reason = self._generic_career_redirect_url_rejection(requested, require_career_intent=True)
        if unsafe_reason is None:
            unsafe_reason = self._generic_career_redirect_url_rejection(final, require_career_intent=False)
        if unsafe_reason:
            verification["reason"] = unsafe_reason
            return verification
        if not source.hostname or source.scheme != "https" or not self._is_default_https_origin(source):
            verification["reason"] = "official source origin is not safe HTTPS"
            return verification
        if self._registrable_site(requested.hostname or "") != self._registrable_site(source.hostname):
            verification["reason"] = "redirect request did not originate on the official site"
            return verification
        if self._registrable_site(requested.hostname or "") == self._registrable_site(final.hostname or ""):
            verification["reason"] = "redirect did not cross registrable domains"
            return verification
        if self._is_provider_job_board_url(actual_url) or is_ats_url(actual_url):
            verification["reason"] = "provider redirects require provider verification"
            return verification
        if not company_name:
            verification["reason"] = "company identity unavailable"
            return verification

        metadata = _CareerRedirectMetadataParser()
        try:
            metadata.feed(page.html[:500000])
        except (ValueError, TypeError):
            verification["reason"] = "invalid redirect page markup"
            return verification

        company_tokens = self._company_identity_tokens(company_name)
        identity_matches = [
            value
            for value in metadata.identity_values
            if company_tokens and company_tokens.issubset(set(re.findall(r"[a-z0-9]+", value.casefold())))
        ]
        if not identity_matches:
            verification["reason"] = "redirect page company identity mismatch"
            return verification

        declared_identity_urls = metadata.canonical_urls + metadata.og_urls
        if not declared_identity_urls:
            verification["reason"] = "redirect page lacks canonical or og:url identity"
            return verification
        normalized_identity_urls = [urljoin(actual_url, value) for value in declared_identity_urls]
        if any(not self._same_url_origin(value, actual_url) for value in normalized_identity_urls):
            verification["reason"] = "redirect page canonical or og:url crosses origin"
            return verification

        page_links = extract_links(page)
        actionable_routes = [
            link.url
            for link in page_links
            if self._same_url_origin(link.url, actual_url)
            and self._is_safe_generic_redirect_link(link.url)
            and self._is_actionable_career_route(link)
        ]
        if not actionable_routes:
            verification["reason"] = "redirect page lacks same-origin job route"
            return verification

        official_backlinks = [
            link.url
            for link in page_links
            if link.origin == "page_link"
            and not self._same_url_origin(link.url, actual_url)
            and self._is_safe_generic_redirect_link(link.url)
            and self._is_company_bound_corporate_backlink(
                link.url,
                source_url,
                company_tokens,
            )
        ]
        if not official_backlinks:
            verification["reason"] = "redirect page lacks official source-origin backlink"
            return verification

        verification.update(
            {
                "verified": True,
                "identity_evidence": identity_matches[:3],
                "identity_urls": normalized_identity_urls[:3],
                "actionable_routes": actionable_routes[:3],
                "official_backlinks": official_backlinks[:3],
            }
        )
        return verification

    def _generic_career_redirect_url_rejection(self, parsed, *, require_career_intent: bool) -> str | None:
        if parsed.scheme != "https" or not parsed.hostname or not self._is_default_https_origin(parsed):
            return "redirect URL is not credential-free HTTPS on port 443"
        query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
        if parsed.query:
            if query_keys & _CAREER_REDIRECT_QUERY_KEYS:
                return "redirect URL contains an open-redirect query target"
            return "redirect URL is not query-free"
        path_parts = {part.casefold() for part in parsed.path.split("/") if part}
        host_parts = set(re.split(r"[.-]", parsed.hostname.casefold()))
        if (path_parts | host_parts) & _CAREER_REDIRECT_SURFACE_MARKERS:
            return "redirect URL targets a disallowed surface"
        if require_career_intent and not path_parts & {
            "career",
            "careers",
            "jobs",
            "join-us",
            "join-our-team",
            "open-positions",
            "opportunities",
        }:
            return "requested URL lacks career intent"
        return None

    def _is_default_https_origin(self, parsed) -> bool:
        if parsed.username or parsed.password:
            return False
        try:
            return parsed.port in {None, 443}
        except ValueError:
            return False

    def _same_url_origin(self, first_url: str, second_url: str) -> bool:
        first = urlparse(first_url)
        second = urlparse(second_url)
        if not self._is_default_https_origin(first) or not self._is_default_https_origin(second):
            return False
        return (
            first.scheme == second.scheme == "https"
            and (first.hostname or "").casefold() == (second.hostname or "").casefold()
            and (first.port or 443) == (second.port or 443)
        )

    def _is_safe_generic_redirect_link(self, url: str) -> bool:
        parsed = urlparse(url)
        return self._generic_career_redirect_url_rejection(parsed, require_career_intent=False) is None

    def _is_company_bound_corporate_backlink(
        self,
        backlink_url: str,
        source_url: str,
        company_tokens: set[str],
    ) -> bool:
        if self._same_url_origin(backlink_url, source_url):
            return True
        source_host = urlparse(source_url).hostname or ""
        backlink_host = urlparse(backlink_url).hostname or ""
        source_brand = self._registrable_site(source_host).split(".", 1)[0]
        backlink_brand = self._registrable_site(backlink_host).split(".", 1)[0]
        return (
            bool(source_brand)
            and source_brand == backlink_brand
            and source_brand in company_tokens
        )

    def _is_actionable_career_route(self, link: RawLink) -> bool:
        path_parts = {part.casefold() for part in urlparse(link.url).path.split("/") if part}
        route_markers = {
            "jobs",
            "job-search",
            "job-listings",
            "openings",
            "open-positions",
            "positions",
            "search-jobs",
            "search-results",
        }
        command_text = " ".join(link.text.casefold().split())
        return bool(path_parts & route_markers) and (
            link.origin == "form_action"
            or any(
                marker in command_text
                for marker in ("apply", "jobs", "openings", "positions", "roles", "search")
            )
        )

    def _company_identity_tokens(self, company_name: str) -> set[str]:
        legal_suffixes = {
            "co",
            "company",
            "corp",
            "corporation",
            "gmbh",
            "inc",
            "incorporated",
            "limited",
            "llc",
            "ltd",
            "plc",
        }
        return {
            token
            for token in re.findall(r"[a-z0-9]+", company_name.casefold())
            if token not in legal_suffixes
        }

    def _verify_derived_provider_with_adapter(
        self,
        url: str,
        *,
        target_title: str | None,
        trusted_configuration: bool,
    ) -> tuple[str | None, dict] | None:
        adapter = self.provider_registry.adapter_for(url)
        board = adapter.identify_board(url) if adapter else None
        if adapter is None or board is None or not adapter.supports_listing:
            return None
        try:
            result = adapter.list_jobs(self.fetcher, board, JobQuery(title=target_title))
        except FetchError:
            return None

        matching = [
            candidate
            for candidate in result.candidates
            if target_title
            and score_title_match(candidate.title, target_title)[0]
            >= MIN_DERIVED_TENANT_TITLE_SCORE
        ]
        verification = {
            "url": board.url,
            "provider": adapter.name,
            "method": "native_adapter_first",
            "candidate_count": len(result.candidates),
            "title_match_count": len(matching),
            "reason_code": result.reason_code,
            "adapter_trace": result.trace,
        }
        verified = bool(matching) or (
            bool(result.candidates)
            and (
                trusted_configuration
                or result.trace.get("tenant_identity_verified") is True
            )
        )
        if verified:
            return (
                self._canonical_provider_board_url(adapter.name, board.url, board.identifier),
                verification,
            )

        conclusive = (
            bool(result.candidates)
            or result.reason_code == "EMPTY_PROVIDER_RESPONSE"
            or "404" in repr(result.trace)
        )
        return (None, verification) if conclusive else None

    def _verify_provider_candidate_without_page(
        self,
        url: str,
        target_title: str | None,
    ) -> tuple[str, dict] | None:
        adapter = self.provider_registry.adapter_for(url)
        board = adapter.identify_board(url) if adapter else None
        if adapter is None or board is None or not adapter.supports_listing:
            return None
        try:
            result = adapter.list_jobs(self.fetcher, board, JobQuery(title=target_title))
        except FetchError:
            return None
        matches = [
            candidate
            for candidate in result.candidates
            if not target_title
            or score_title_match(candidate.title, target_title)[0] >= MIN_TITLE_MATCH_SCORE
        ]
        if not matches:
            return None
        canonical_url = self._canonical_provider_board_url(
            adapter.name,
            board.url,
            board.identifier,
        )
        return canonical_url, {
            "url": canonical_url,
            "provider": adapter.name,
            "method": "provider_adapter_without_page",
            "candidate_count": len(result.candidates),
            "title_match_count": len(matches),
        }

    def _verify_derived_provider_board(
        self,
        board_url: str,
        html: str,
        *,
        target_title: str | None = None,
        trusted_configuration: bool = False,
    ) -> tuple[bool, dict]:
        provider = self.provider_registry.detect(board_url)
        verification = {
            "url": board_url,
            "provider": provider,
            "method": None,
            "api_errors": [],
        }
        for request in build_provider_api_requests(board_url):
            try:
                page = self.fetcher.fetch(request.url, data=request.data, headers=request.headers)
            except FetchError as exc:
                verification["api_errors"].append({"url": request.url, "error": str(exc)})
                continue
            candidates = provider_api_candidates(provider, page.html, board_url)
            verification["method"] = "provider_api"
            verification["candidate_count"] = len(candidates)
            matching = [
                title
                for title, _url in candidates
                if target_title
                and score_title_match(title, target_title)[0] >= MIN_DERIVED_TENANT_TITLE_SCORE
            ]
            verification["title_match_count"] = len(matching)
            return bool(matching) if target_title else trusted_configuration and bool(candidates), verification

        links = extract_links(Page(url=board_url, html=html, final_url=board_url))
        links.extend(structured_job_links(html, board_url))
        detail_links = [
            link
            for link in links
            if is_likely_job_detail(score_job_link(link, board_url))
        ]
        matching_links = [
            link
            for link in detail_links
            if target_title
            and score_title_match(link.text, target_title)[0] >= MIN_DERIVED_TENANT_TITLE_SCORE
        ]
        has_job_detail = bool(matching_links) if target_title else trusted_configuration and bool(detail_links)
        verification["method"] = "page_job_links" if has_job_detail else "unverified"
        verification["candidate_count"] = len(links)
        verification["title_match_count"] = len(matching_links)
        return has_job_detail, verification

    def _sitemap_candidates(
        self,
        homepage_url: str,
        *,
        target_region: str | None = None,
    ) -> tuple[list[RawLink], dict]:
        parsed = urlparse(homepage_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        sitemap_urls = [normalize_url("/sitemap.xml", base), normalize_url("/sitemap_index.xml", base)]
        trace = {
            "sitemaps_checked": [],
            "candidate_count": 0,
            "fanout_limit": MAX_SITEMAPS_PER_DISCOVERY,
            "fanout_limit_reached": False,
            "sitemaps_not_scheduled": 0,
            "target_region": target_region,
        }

        try:
            robots = self.fetcher.fetch(normalize_url("/robots.txt", base))
            for line in robots.html.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_urls.append(normalize_url(line.split(":", 1)[1].strip()))
        except FetchError:
            pass

        links: list[RawLink] = []
        seen_sitemaps: set[str] = set()
        pending_sitemaps = list(dict.fromkeys(sitemap_urls))
        queued_sitemaps = set(pending_sitemaps)
        while pending_sitemaps and len(seen_sitemaps) < MAX_SITEMAPS_PER_DISCOVERY:
            pending_sitemaps.sort(
                key=lambda url: _sitemap_queue_priority(url, target_region)
            )
            sitemap_url = pending_sitemaps.pop(0)
            queued_sitemaps.discard(sitemap_url)
            if sitemap_url in seen_sitemaps:
                continue
            seen_sitemaps.add(sitemap_url)
            try:
                page = self.fetcher.fetch(sitemap_url)
            except FetchError as exc:
                trace["sitemaps_checked"].append({"url": sitemap_url, "error": str(exc)})
                continue

            urls = self._extract_sitemap_locs(page.html)
            urls.sort(key=lambda url: _sitemap_queue_priority(url, target_region))
            trace["sitemaps_checked"].append({"url": sitemap_url, "url_count": len(urls)})
            candidates_before = len(links)
            for url in urls:
                lower_url = url.lower()
                if lower_url.endswith(".xml"):
                    normalized_sitemap = normalize_url(url)
                    if (
                        normalized_sitemap in seen_sitemaps
                        or normalized_sitemap in queued_sitemaps
                    ):
                        continue
                    queued_sitemaps.add(normalized_sitemap)
                    pending_sitemaps.append(normalized_sitemap)
                    continue
                normalized_url = normalize_url(url)
                target_host = urlparse(normalized_url).hostname or ""
                homepage_host = parsed.hostname or ""
                if is_resource_url(normalized_url):
                    continue
                if not (
                    self._same_site_host(target_host, homepage_host)
                    or self._is_provider_job_board_url(normalized_url)
                ):
                    continue
                if any(
                    token in lower_url
                    for token in ("career", "careers", "jobs", "join-us", "join-our-team", "join-", "openings")
                ):
                    links.append(
                        RawLink(
                            url=normalized_url,
                            text=urlparse(url).path,
                            source_url=sitemap_url,
                            origin="sitemap",
                        )
                    )

            if (
                target_region
                and _sitemap_matches_region(sitemap_url, target_region)
                and len(links) > candidates_before
            ):
                trace["stopped_reason"] = "target_region_candidates_found"
                trace["sitemaps_not_scheduled"] = len(pending_sitemaps)
                pending_sitemaps.clear()
                break

        if pending_sitemaps:
            trace["fanout_limit_reached"] = True
            trace["sitemaps_not_scheduled"] = len(pending_sitemaps)
        trace["candidate_count"] = len(links)
        return links, trace

    def _extract_sitemap_locs(self, xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        urls: list[str] = []
        for element in root.iter():
            if element.tag.endswith("loc") and element.text:
                urls.append(element.text.strip())
        return urls

    def _dedupe_candidates(self, candidates: list[LinkCandidate]) -> list[LinkCandidate]:
        seen: set[str] = set()
        deduped: list[LinkCandidate] = []
        for candidate in candidates:
            key = candidate.url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _looks_like_career_page(self, candidate: LinkCandidate, html: str) -> bool:
        if is_ats_url(candidate.url):
            return self._is_provider_job_board_url(candidate.url)
        text = html[:200000].lower()
        if self._has_ats_link(text):
            return True
        page_links = extract_links(Page(url=candidate.url, html=html, final_url=candidate.url))
        if any(
            is_likely_job_detail(score_job_link(link, candidate.url))
            for link in page_links
        ):
            return True
        if any(
            reason in candidate.reasons
            for reason in (
                "identity-supplied career root requiring verification",
                "generated path probe",
            )
        ):
            strong_employment_signals = (
                "open roles",
                "open positions",
                "current openings",
                "job openings",
                "view open jobs",
                "search jobs",
                "apply now",
                "join our team",
                "jobs at",
                "employment opportunities",
            )
            if any(signal in text for signal in strong_employment_signals):
                return True
            non_employment_surface_signals = (
                "homes for sale",
                "subreddit",
                "zestimate",
                "careers channel",
                "channel videos",
                "live streams",
                "videos and streams",
                "streaming channel",
            )
            if any(signal in text for signal in non_employment_surface_signals):
                return False
            title_match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", text, flags=re.I | re.S)
            title = re.sub(r"<[^>]+>", " ", title_match.group(1)) if title_match else ""
            return re.search(r"\b(?:careers?|jobs?)\b", title, flags=re.I) is not None
        career_signals = (
            "open roles",
            "open positions",
            "current openings",
            "job openings",
            "view open jobs",
            "apply now",
            "join our team",
            "join us",
            "life at",
            "careers",
        )
        generic_job_only = candidate.score < 120 and "career keyword 'jobs'" in " ".join(candidate.reasons)
        return not generic_job_only and any(signal in text for signal in career_signals)

    def _looks_like_error_page(self, url: str, html: str) -> bool:
        path = urlparse(url).path.lower()
        if any(marker in path for marker in ("/404", "404/", "/error", "/errors", "/not-found")):
            return True
        text = html[:5000].lower()
        return any(
            marker in text
            for marker in (
                "404 not found",
                "page not found",
                "we can't find the page",
                "the page you are looking for could not be found",
            )
        )

    def _brand_label_from_host(self, host: str) -> str | None:
        label = host.lower().split(":")[0].removeprefix("www.").split(".")[0]
        label = "".join(char if char.isalnum() else "-" for char in label).strip("-")
        return label or None

    def _has_ats_link(self, text: str) -> bool:
        ats_markers = (
            "jobs.lever.co",
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "ashbyhq.com",
            "apply.workable.com",
            "smartrecruiters.com",
            "myworkdayjobs.com",
            "icims.com",
            "eightfold.ai",
            "careers.oracle.com",
            "oraclecloud.com",
        )
        return any(marker in text for marker in ats_markers)

    def _looks_like_job_detail_url(self, url: str) -> bool:
        candidate = score_job_link(
            RawLink(url=url, text="", source_url=url, origin="job_detail_check"),
            url,
        )
        return is_likely_job_detail(candidate)


def _stage_result(
    stage: str,
    status: str,
    *,
    reason_code: str | None = None,
    provider: str | None = None,
    duration_ms: int = 0,
    input_count: int = 0,
    output_count: int = 0,
    evidence: list[dict] | None = None,
    detail: str | None = None,
) -> StageResult:
    return make_stage_result(
        stage,
        status,
        reason_code=reason_code,
        provider=provider,
        duration_ms=duration_ms,
        input_count=input_count,
        output_count=output_count,
        evidence=evidence,
        detail=detail,
    )


def _url_evidence(field: str, url: str) -> dict[str, str]:
    return {"field": field, "url": url}


def _leading_locale_segment(url: str | None) -> str | None:
    if not url:
        return None
    try:
        first = next((part.casefold() for part in urlparse(url).path.split("/") if part), "")
    except (TypeError, ValueError):
        return None
    if len(first) == 2 and first.isalpha():
        return first
    if first in {"southeast-asia", "united-kingdom", "australia", "canada", "india"}:
        return first
    return None


def _sitemap_matches_region(url: str, region: str) -> bool:
    try:
        path = urlparse(url).path.casefold()
    except (TypeError, ValueError):
        return False
    aliases = {
        "us": ("us", "usa", "united-states", "unitedstates", "en-us", "en_us"),
    }.get(region.casefold(), (region.casefold(),))
    return any(
        re.search(
            rf"(?:^|[._/-]){re.escape(alias)}(?:[._/-]|$)",
            path,
        )
        is not None
        for alias in aliases
    )


def _sitemap_queue_priority(url: str, target_region: str | None) -> tuple[int, int]:
    try:
        path = urlparse(url).path.casefold()
    except (TypeError, ValueError):
        path = ""
    region_rank = (
        0
        if target_region and _sitemap_matches_region(url, target_region)
        else 1
    )
    inventory_rank = 0 if "job" in path else 1
    return region_rank, inventory_rank


def _career_audience_mismatch(
    url: str,
    text: str,
    target_title: str | None,
) -> str | None:
    if not target_title:
        return None
    candidate_text = f"{urlparse(url).path} {text}".casefold()
    target = target_title.casefold()
    if any(marker in candidate_text for marker in ("executive", "partner-jobs")) and not any(
        marker in target for marker in ("executive", "partner", "principal", "director")
    ):
        return "executive"
    if any(marker in candidate_text for marker in ("student", "graduate", "internship")) and not any(
        marker in target for marker in ("student", "graduate", "intern", "apprentice")
    ):
        return "early-career"
    return None


def _legacy_step_name(stage: str) -> str:
    return {
        STAGE_CAREER_DISCOVERY: "find_career_page",
        STAGE_JOB_BOARD_DISCOVERY: "find_job_board",
        STAGE_OPENING_MATCH: "match_opening",
    }.get(stage, stage)


def _trace_has_fetch_budget_exhaustion(value: object, key: str = "") -> bool:
    if key == "reason_code" and value == "FETCH_BUDGET_EXHAUSTED":
        return True
    if key == "candidate_fetch_budget_exhausted":
        if not isinstance(value, dict):
            return False
        evidence_count = value.get("untried_evidence_backed_count")
        return bool(value) if evidence_count is None else evidence_count > 0
    if key == "fetch_budget_exhausted":
        return value not in (None, "", [], {})
    if isinstance(value, dict):
        return any(
            _trace_has_fetch_budget_exhaustion(item, str(name))
            for name, item in value.items()
        )
    if isinstance(value, list):
        return any(_trace_has_fetch_budget_exhaustion(item) for item in value)
    return False


def _fetch_failure_trace(error: FetchError) -> dict:
    reason_code = error.reason_code or classify_fetch_error(str(error))
    retryable = (
        error.retryable
        if error.retryable is not None
        else reason_spec(reason_code).retryable
    )
    return {
        "error": str(error),
        "reason_code": reason_code,
        "reason_code_source": (
            "exception" if error.reason_code is not None else "classified_message"
        ),
        "retryable": retryable,
    }


def _offline_fixture_failure(value: object) -> dict | None:
    if isinstance(value, dict):
        if (
            value.get("reason_code") == "OFFLINE_FIXTURE_MISSING"
            and value.get("reason_code_source") == "exception"
        ):
            return value
        for item in value.values():
            failure = _offline_fixture_failure(item)
            if failure is not None:
                return failure
    elif isinstance(value, list):
        for item in value:
            failure = _offline_fixture_failure(item)
            if failure is not None:
                return failure
    return None


def _retryable_evidence_candidate_failure(value: object) -> dict | None:
    if isinstance(value, dict):
        reason_code = value.get("reason_code")
        evidence_tier = value.get("evidence_tier")
        if (
            value.get("retryable") is True
            and isinstance(reason_code, str)
            and isinstance(evidence_tier, int)
            and evidence_tier <= 2
        ):
            return value
        for item in value.values():
            failure = _retryable_evidence_candidate_failure(item)
            if failure is not None:
                return failure
    elif isinstance(value, list):
        for item in value:
            failure = _retryable_evidence_candidate_failure(item)
            if failure is not None:
                return failure
    return None


def _trace_has_caller_deadline_exhaustion(value: object) -> bool:
    if isinstance(value, dict):
        detail = value.get("error")
        if isinstance(detail, str) and "caller deadline" in detail.casefold():
            return True
        return any(_trace_has_caller_deadline_exhaustion(item) for item in value.values())
    if isinstance(value, list):
        return any(_trace_has_caller_deadline_exhaustion(item) for item in value)
    return False


def _legacy_error(stage: str, reason_code: str | None) -> str:
    if stage == STAGE_CAREER_DISCOVERY and reason_code == "CAREER_PAGE_NOT_FOUND":
        return "career_page_not_found"
    if stage == STAGE_JOB_BOARD_DISCOVERY and reason_code == "JOB_BOARD_NOT_FOUND":
        return "job_board_not_found"
    if reason_code in {
        "NETWORK_TIMEOUT",
        "DNS_FAILED",
        "CONNECTION_FAILED",
        "HTTP_FORBIDDEN",
        "RATE_LIMITED",
        "SERVER_ERROR",
    }:
        return "fetch_failed"
    return (reason_code or "discovery_failed").lower()
