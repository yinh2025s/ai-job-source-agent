from __future__ import annotations

import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .career_search import CareerSearchResolver
from .content_probe import (
    discover_first_party_career_navigation,
    probe_first_party_cms_payload,
    probe_first_party_provider_assets,
)
from .contracts import FetchClient, PipelineContext
from .errors import DiscoveryError
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
from .providers import DEFAULT_PROVIDER_REGISTRY, JobQuery, ProviderRegistry
from .reasons import canonical_reason_code, make_stage_result
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


class JobSourceAgent:
    def __init__(
        self,
        fetcher: FetchClient,
        provider_registry: ProviderRegistry | None = None,
        max_candidates: int = 12,
        max_job_pages: int = 8,
        max_career_candidate_fetches: int | None = None,
        max_career_search_queries: int = 5,
        max_ats_board_fetches: int = 5,
        enable_sitemap_discovery: bool = True,
        enable_career_search: bool = True,
        career_search_timeout: float | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.max_candidates = max_candidates
        self.max_job_pages = max_job_pages
        self.max_career_candidate_fetches = (
            max_candidates if max_career_candidate_fetches is None else max(0, max_career_candidate_fetches)
        )
        self.max_career_search_queries = max(0, max_career_search_queries)
        self.max_ats_board_fetches = max(0, max_ats_board_fetches)
        self.enable_sitemap_discovery = enable_sitemap_discovery
        self.enable_career_search = enable_career_search
        self.career_search_timeout = career_search_timeout

    def discover(self, company: CompanyInput) -> DiscoveryResult:
        company_website_url = normalize_url(company.company_website_url) if company.company_website_url else ""
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
            trace={
                "source": company.source,
                "linkedin_job_url": company.linkedin_job_url,
                "external_apply_url": company.external_apply_url,
                "linkedin_company_url": company.linkedin_company_url,
                "linkedin_job_title": company.job_title,
                "source_trace": company.source_trace,
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
    ) -> tuple[str, dict]:
        homepage_url = normalize_url(company_website_url)
        homepage: Page | None = None
        raw_candidates: list[RawLink] = []
        trace = {"homepage_url": homepage_url, "homepage_fetch_error": None, "candidates": [], "candidate_fetch_errors": []}
        try:
            homepage = self.fetcher.fetch(company_website_url)
            homepage_url = homepage.final_url or homepage.url
            raw_candidates = extract_links(homepage)
        except FetchError as exc:
            trace["homepage_fetch_error"] = str(exc)
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
        )
        if selected_url:
            trace["sitemap_discovery"] = {
                "skipped": True,
                "reason": "primary candidate verified before sitemap fanout",
            }
            return selected_url, trace

        if homepage is not None:
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
            )
            if selected_url:
                trace["selected_from"] = "sitemap_discovery"
                return selected_url, trace
        else:
            trace["sitemap_discovery"] = {"skipped": True}

        if self.enable_career_search and company_name:
            search_result = self._search_career_candidates(company_name, homepage_url)
            trace["search_discovery"] = search_result.trace
            selected_url = self._select_verified_career_candidate(
                search_result.candidates,
                trace,
                target_title=target_title,
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
            )
            if selected_url:
                trace["selected"] = ats_trace["selected"]
                trace["selected_page_source"] = ats_trace.get("selected_page_source")
                trace["selected_from"] = "ats_board_discovery"
                return selected_url, trace
        else:
            trace["ats_board_discovery"] = {"skipped": True}

        reason_code = (
            "FETCH_BUDGET_EXHAUSTED"
            if _trace_has_fetch_budget_exhaustion(trace)
            else "career_page_not_found"
        )
        detail = (
            "Career candidates remain unverified because the fetch budget was exhausted."
            if reason_code == "FETCH_BUDGET_EXHAUSTED"
            else "No reliable career page candidate found."
        )
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
    ) -> tuple[str, dict]:
        job_list_url, trace, _discovered_board = self.find_job_board_with_evidence(
            career_page_url,
            company_name=company_name,
        )
        return job_list_url, trace

    def find_job_board_with_evidence(
        self,
        career_page_url: str,
        company_name: str | None = None,
    ) -> tuple[str, dict, DiscoveredJobBoard | None]:
        if self._is_provider_job_board_url(career_page_url):
            return (
                career_page_url,
                {
                    "career_page_url": career_page_url,
                    "job_list_page_url": career_page_url,
                    "selected": {
                        "url": career_page_url,
                        "reason": "career page is already a provider job board",
                    },
                },
                None,
            )
        _opening_url, job_list_url, trace, discovered_board = self._discover_job_board_legacy(
            career_page_url
        )
        if (
            company_name
            and self.max_ats_board_fetches
            and (
                not job_list_url
                or (
                    self.provider_registry.detect(job_list_url) == "generic"
                    and job_list_url.rstrip("/") == career_page_url.rstrip("/")
                )
            )
        ):
            searched_url, search_trace = self._search_verified_ats_board(
                company_name,
                career_page_url,
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

    def _search_verified_ats_board(
        self,
        company_name: str,
        career_page_url: str,
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
        for candidate in search_result.candidates:
            if attempts >= self.max_ats_board_fetches:
                trace["fetch_budget_exhausted"] = self.max_ats_board_fetches
                break
            adapter = self.provider_registry.adapter_for(candidate.url)
            board = adapter.identify_board(candidate.url) if adapter else None
            if adapter is None or board is None or not adapter.supports_listing:
                continue
            attempts += 1
            try:
                result = adapter.list_jobs(self.fetcher, board, JobQuery())
            except FetchError as exc:
                trace["errors"].append({"url": candidate.url, "error": str(exc)})
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
        job_list_url, trace, discovered_board = self.find_job_board_with_evidence(career_page_url)
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
    ) -> tuple[str | None, str | None, dict, DiscoveredJobBoard | None]:
        if self._looks_like_job_detail_url(career_page_url):
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

        queue = [career_page_url]
        visited: set[str] = set()
        pages_checked = 0

        while queue and pages_checked < self.max_job_pages:
            page_url = queue.pop(0)
            normalized_page_url = page_url.rstrip("/")
            if normalized_page_url in visited:
                continue
            visited.add(normalized_page_url)
            pages_checked += 1

            try:
                page = self.fetcher.fetch(page_url)
            except FetchError as exc:
                trace["fetch_errors"].append({"url": page_url, "error": str(exc)})
                continue
            page, content_probe = probe_first_party_cms_payload(self.fetcher, page)
            if content_probe:
                trace.setdefault("content_payload_probes", []).append(content_probe)
            page, provider_asset_probe = probe_first_party_provider_assets(
                self.fetcher,
                page,
                self._is_provider_job_board_url,
            )
            if provider_asset_probe:
                trace.setdefault("content_payload_probes", []).append(provider_asset_probe)
            actual_page_url = page.final_url or page.url
            normalized_actual_url = actual_page_url.rstrip("/")
            if normalized_actual_url != normalized_page_url and normalized_actual_url in visited:
                pages_checked -= 1
                trace["pages_visited"].append(
                    {
                        "url": actual_page_url,
                        "requested_url": page_url,
                        "source": page.source,
                        "redirect_duplicate": True,
                        "top_candidates": [],
                    }
                )
                continue
            visited.add(normalized_actual_url)
            page_board = self.provider_registry.board_for_page(page, self.fetcher)
            if page_board is not None:
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
            if url_adapter is not None and url_board is not None and url_adapter.supports_listing:
                canonical_board_url = url_board.url
                trace["provider"] = url_adapter.name
                trace["provider_detection"] = {
                    "method": "url_evidence",
                    "provider": url_adapter.name,
                    "url": canonical_board_url,
                }
                trace["job_list_page_url"] = canonical_board_url
            elif self._is_provider_job_board_url(actual_page_url):
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
            verified_generic_listing = self._looks_like_generic_job_list_route(
                actual_page_url
            )
            if self._has_job_list_evidence(actual_page_url, deduped) and not trace["job_list_page_url"]:
                trace["job_list_page_url"] = actual_page_url
            elif verified_generic_listing:
                trace["job_list_page_url"] = actual_page_url
            trace["pages_visited"].append(
                {
                    "url": actual_page_url,
                    "source": page.source,
                    "top_candidates": dataclass_to_dict(deduped[:8]),
                }
            )
            trace["candidates"].extend(dataclass_to_dict(deduped[:5]))
            if verified_generic_listing:
                trace["selected_from"] = "explicit_first_party_listing_route"
                trace["selected_page_source"] = page.source
                return None, actual_page_url, trace, None

            linked_provider_board = next(
                (
                    (adapter, board, candidate)
                    for candidate in deduped
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
                trace["job_list_page_url"] = board.url
                return None, board.url, trace, None

            official_portal = next(
                (
                    candidate
                    for candidate in deduped
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
                deduped[: self.max_candidates],
                key=lambda candidate: (
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
                except FetchError:
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
                    queue.append(candidate.url)

        return None, trace["job_list_page_url"], trace, None

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
        if link.origin == "page_link":
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
        if link.origin == "page_link" and path_parts == ["team"]:
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
    ) -> str | None:
        fetch_attempts = 0
        fetch_limit = self.max_career_candidate_fetches if max_fetches is None else max_fetches
        ranked_candidates = sorted(
            candidates,
            key=lambda candidate: -(
                candidate.score + self._career_evidence_priority_boost(candidate)
            ),
        )
        for candidate in ranked_candidates[: self.max_candidates]:
            if candidate.score < 50:
                continue
            if fetch_attempts >= fetch_limit:
                trace["candidate_fetch_budget_exhausted"] = {
                    "limit": fetch_limit,
                    "remaining_candidates": len(candidates) - fetch_attempts,
                }
                return None
            fetch_attempts += 1
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
                trace["candidate_fetch_errors"].append({"url": candidate.url, "error": str(exc)})
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
        return None

    @staticmethod
    def _career_evidence_priority_boost(candidate: LinkCandidate) -> int:
        if any(
            reason.startswith("identity-supplied") or reason == "derived provider configuration"
            for reason in candidate.reasons
        ):
            return 1000
        if "homepage navigation link" in candidate.reasons and any(
            reason.startswith("career keyword")
            or reason in {
                "explicit job-list route",
                "homepage team link requiring employment evidence",
            }
            for reason in candidate.reasons
        ):
            return 500
        return 0

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
    if key in {"candidate_fetch_budget_exhausted", "fetch_budget_exhausted"}:
        return value not in (None, "", [], {})
    if isinstance(value, dict):
        return any(
            _trace_has_fetch_budget_exhaustion(item, str(name))
            for name, item in value.items()
        )
    if isinstance(value, list):
        return any(_trace_has_fetch_budget_exhaustion(item) for item in value)
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
