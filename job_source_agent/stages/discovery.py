from __future__ import annotations

import time
import re
from typing import Protocol
from urllib.parse import urlsplit

from ..contracts import PipelineContext, StageExecution
from ..errors import DiscoveryError
from ..homepage_navigation import HomepageNavigationEvidence
from ..candidate_portfolio import (
    CompositeCandidateDiscovery,
    ProviderCandidatePortfolioBuilder,
)
from ..identity_continuity import (
    HiringIdentityEvidence,
    HiringRelationshipEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from ..job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from ..models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
)
from ..opening_availability import diagnose_opening_availability
from ..providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from ..provider_candidates import CandidateDiscoveryRequest, VerifiedProviderCandidate
from ..reasons import canonical_reason_code, make_stage_result
from ..result_identity import canonicalize_identity_url, tenant_locator
from ..source_posting import trusted_linkedin_native_posting
from ..web import FetchError, normalize_url
from ..fetch_failure import project_fetch_error


class CareerDiscoveryService(Protocol):
    def find_career_page(
        self,
        company_website_url: str,
        company_name: str | None = None,
        preferred_url: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
        homepage_navigation_evidence: HomepageNavigationEvidence | None = None,
    ) -> tuple[str, dict]:
        ...


class JobBoardDiscoveryService(Protocol):
    def find_job_board(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict]:
        ...

    def find_job_board_with_evidence(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict, DiscoveredJobBoard | None]:
        ...

    def find_job_board_portfolio(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict, JobBoardPortfolio | None]:
        ...


class OpeningMatchService(Protocol):
    def match_opening(
        self,
        job_list_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        ...

    def match_discovered_board(
        self,
        discovered_board: DiscoveredJobBoard,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        ...


class CareerDiscoveryStage:
    name = STAGE_CAREER_DISCOVERY

    def __init__(self, service: CareerDiscoveryService) -> None:
        self.service = service

    def run(self, context: PipelineContext) -> StageExecution:
        if _upstream_stage_failed(context, STAGE_HIRING_IDENTITY_RESOLUTION):
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail=(
                        "Hiring identity resolution did not produce a safe hiring entity."
                    ),
                ),
                trace={
                    "scheduler": {
                        "status": "not_run",
                        "reason": "hiring_identity_unresolved",
                    }
                },
            )
        if not context.company_website_url:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Website resolution did not produce an input.",
                )
            )

        started = time.perf_counter()
        try:
            replay_trace = context.company.source_trace.get("replay")
            replay_root = context.company.source == "replay_input" or isinstance(replay_trace, dict)
            trusted_identity_root = _identity_stage_resolved_career_root(context)
            if context.career_root_url and (not replay_root or trusted_identity_root):
                career_url = normalize_url(context.career_root_url)
                trace = {
                    "homepage_url": context.company_website_url,
                    "selected": {
                        "url": career_url,
                        "reason": "trusted direct-input or identity career root",
                    },
                    "preferred_root_validation": "trusted_provenance",
                }
                detail = "Career root supplied by a trusted direct input or identity rule."
            else:
                find_kwargs = {
                    "company_name": context.hiring_entity_name or context.company.company_name,
                    "preferred_url": context.career_root_url,
                    "target_title": context.company.job_title,
                    "target_location": context.company.job_location,
                }
                if context.homepage_navigation_evidence is not None:
                    find_kwargs["homepage_navigation_evidence"] = (
                        context.homepage_navigation_evidence
                    )
                career_url, trace = self.service.find_career_page(
                    context.company_website_url,
                    **find_kwargs,
                )
                detail = (
                    "Replay career root was revalidated."
                    if context.career_root_url
                    and career_url.rstrip("/") == normalize_url(context.career_root_url).rstrip("/")
                    else None
                )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            return _failed_execution(
                self.name,
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "career_page_url", "url": career_url}],
                detail=detail,
            ),
            updates={"career_page_url": career_url},
            trace=trace,
        )


class JobBoardDiscoveryStage:
    name = STAGE_JOB_BOARD_DISCOVERY

    def __init__(
        self,
        service: JobBoardDiscoveryService,
        provider_registry: ProviderRegistry | None = None,
        *,
        candidate_discovery: CompositeCandidateDiscovery | None = None,
        enable_parallel_candidate_discovery: bool = False,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.candidate_discovery = candidate_discovery
        self.enable_parallel_candidate_discovery = enable_parallel_candidate_discovery

    def run(self, context: PipelineContext) -> StageExecution:
        if self.enable_parallel_candidate_discovery and self.candidate_discovery is not None:
            candidate_execution, candidate_trace = self._from_candidate_portfolio(context)
            if candidate_execution is not None:
                return candidate_execution
            legacy_execution = self._run_legacy(context)
            return StageExecution(
                result=legacy_execution.result,
                updates=legacy_execution.updates,
                trace={
                    **legacy_execution.trace,
                    "parallel_candidate_fallback": candidate_trace,
                },
                evidence_lineage=legacy_execution.evidence_lineage,
            )
        return self._run_legacy(context)

    def _run_legacy(self, context: PipelineContext) -> StageExecution:
        if not context.career_page_url:
            if context.company.external_apply_url:
                return self._from_external_apply(context)
            if self._career_path_is_definitively_missing(context):
                native_execution = self._from_linkedin_native_source(
                    context,
                    fallback_trace={"career_path": "definitively_missing"},
                )
                if native_execution is not None:
                    return native_execution
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Career discovery did not produce an input.",
                )
            )

        started = time.perf_counter()
        try:
            find_portfolio = getattr(self.service, "find_job_board_portfolio", None)
            if callable(find_portfolio):
                job_list_url, trace, portfolio = find_portfolio(
                    context.career_page_url,
                    company_name=(
                        context.hiring_entity_name or context.company.company_name
                    ),
                    target_title=context.company.job_title,
                    target_location=context.company.job_location,
                )
                discovered_board = portfolio.primary if portfolio is not None else None
            else:
                portfolio = None
                find_with_evidence = getattr(
                    self.service,
                    "find_job_board_with_evidence",
                    None,
                )
            if not callable(find_portfolio) and callable(find_with_evidence):
                job_list_url, trace, discovered_board = find_with_evidence(
                    context.career_page_url,
                    company_name=context.hiring_entity_name or context.company.company_name,
                    target_location=context.company.job_location,
                )
            elif not callable(find_portfolio):
                job_list_url, trace = self.service.find_job_board(
                    context.career_page_url,
                    company_name=context.hiring_entity_name or context.company.company_name,
                    target_location=context.company.job_location,
                )
                discovered_board = None
        except FetchError as exc:
            failure = project_fetch_error(exc)
            if context.company.external_apply_url:
                return self._from_external_apply(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_failure": failure,
                    },
                )
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            if context.company.external_apply_url:
                return self._from_external_apply(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_trace": exc.trace,
                    },
                )
            reason_code = canonical_reason_code(exc.code)
            if reason_code == "JOB_BOARD_NOT_FOUND" and not _trace_has_discovery_errors(exc.trace):
                native_execution = self._from_linkedin_native_source(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_trace": exc.trace,
                    },
                )
                if native_execution is not None:
                    return native_execution
            return _failed_execution(
                self.name,
                reason_code,
                started,
                str(exc),
                trace=exc.trace,
            )

        provider = trace.get("provider") or self.provider_registry.detect(job_list_url)
        provider = None if provider == "generic" else provider
        updates = {"job_list_page_url": job_list_url, "provider": provider}
        if discovered_board is not None:
            updates["discovered_job_board"] = discovered_board
        updates["provider_identity"] = _provider_identity(
            context,
            job_list_url,
            discovered_board,
            self.provider_registry,
        )
        if (
            portfolio is not None
            and (
                len(portfolio.boards) > 1
                or not portfolio.eligible_set_complete
            )
        ):
            updates["job_board_portfolio"] = portfolio
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                provider=provider,
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "job_list_page_url", "url": job_list_url}],
            ),
            updates=updates,
            trace=trace,
        )

    def _from_candidate_portfolio(
        self,
        context: PipelineContext,
    ) -> tuple[StageExecution | None, dict]:
        started = time.perf_counter()
        request = CandidateDiscoveryRequest(
            company_name=context.hiring_entity_name or context.company.company_name,
            target_title=context.company.job_title,
            target_location=context.company.job_location,
            company_website_url=context.company_website_url or None,
            career_page_url=context.career_page_url,
            external_apply_url=context.company.external_apply_url,
        )
        pool, discovery_trace = self.candidate_discovery.discover(request)
        built = ProviderCandidatePortfolioBuilder(self.provider_registry).build(pool)
        if built.portfolio is None:
            return None, {
                "candidate_discovery": discovery_trace,
                "candidate_verification": built.trace,
            }

        evaluated = tuple(
            (item, relationship)
            for item in built.verified
            if (relationship := _candidate_hiring_relationship(context, item))
            is not None
        )
        if not evaluated:
            return None, {
                "candidate_discovery": discovery_trace,
                "candidate_verification": built.trace,
                "relationship_verification": {
                    "status": "rejected",
                    "reason": "candidate_evidence_url_invalid",
                },
            }
        selected, relationship_evidence = next(
            (item for item in evaluated if item[1].verified),
            evaluated[0],
        )
        ordered_verified = (
            selected,
            *(item for item in built.verified if item is not selected),
        )
        portfolio = JobBoardPortfolio(
            boards=tuple(item.discovered_board for item in ordered_verified),
            eligible_set_complete=built.portfolio.eligible_set_complete,
        )
        discovered = selected.discovered_board
        hiring_evidence = _candidate_hiring_evidence(
            context,
            relationship_evidence,
        )
        provider_identity = _provider_identity(
            context,
            discovered.board.url,
            discovered,
            self.provider_registry,
            candidate=selected,
            hiring_evidence=hiring_evidence,
            relationship_evidence=relationship_evidence,
        )
        updates: dict[str, object] = {
            "job_list_page_url": discovered.board.url,
            "provider": discovered.board.provider,
            "discovered_job_board": discovered,
            "provider_identity": provider_identity,
        }
        if (
            hiring_evidence is not None
            and hiring_evidence != context.hiring_identity_evidence
        ):
            updates["hiring_identity_evidence"] = hiring_evidence
            updates["hiring_entity_name"] = hiring_evidence.hiring_entity_name
        if (
            len(portfolio.boards) > 1
            or not portfolio.eligible_set_complete
        ):
            updates["job_board_portfolio"] = portfolio
        trace = {
            "method": "parallel_candidate_discovery",
            "candidate_discovery": discovery_trace,
            "candidate_verification": built.trace,
            "selected": selected.candidate.to_trace_payload(),
            "provider": discovered.board.provider,
            "job_list_page_url": discovered.board.url,
            "relationship_verified": provider_identity.relationship_verified,
            "relationship_method": provider_identity.verification_method,
            "relationship_evidence": relationship_evidence.to_trace_payload(),
        }
        execution = StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                provider=discovered.board.provider,
                duration_ms=_elapsed_ms(started),
                input_count=len(pool.candidates),
                output_count=1,
                evidence=[
                    {"field": "job_list_page_url", "url": discovered.board.url},
                    {
                        "field": "candidate_source",
                        "value": selected.candidate.source_kind,
                    },
                ],
                detail="Provider board selected from the merged candidate portfolio.",
            ),
            updates=updates,
            trace=trace,
        )
        return execution, trace

    def _from_linkedin_native_source(
        self,
        context: PipelineContext,
        *,
        fallback_trace: dict | None = None,
    ) -> StageExecution | None:
        posting = trusted_linkedin_native_posting(
            context.company.source_trace,
            expected_job_url=context.company.linkedin_job_url or None,
        )
        if posting is None:
            return None

        evidence = {
            "type": "source_posting_availability",
            "disposition": "linkedin_native_only",
            "availability": posting.availability,
            "apply_mode": posting.apply_mode,
            "evidence_source": posting.evidence_source,
            "source_posting_url": posting.job_url,
        }
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code="LINKEDIN_NATIVE_ONLY",
                input_count=1,
                evidence=[evidence],
                detail=(
                    "The source posting is active and uses LinkedIn-native apply, while no "
                    "public company job board was verified."
                ),
            ),
            trace={
                "method": "source_posting_availability",
                **evidence,
                **(fallback_trace or {}),
            },
        )

    @staticmethod
    def _career_path_is_definitively_missing(context: PipelineContext) -> bool:
        for result in reversed(context.stage_results):
            if result.stage == STAGE_CAREER_DISCOVERY:
                return (
                    result.status == "failed"
                    and result.reason_code == "CAREER_PAGE_NOT_FOUND"
                    and not result.retryable
                )
        return False

    def _from_external_apply(
        self,
        context: PipelineContext,
        fallback_trace: dict | None = None,
    ) -> StageExecution:
        source_url = context.company.external_apply_url or ""
        try:
            source_url = normalize_url(source_url)
        except (TypeError, ValueError) as exc:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "unsupported",
                    reason_code="PROVIDER_UNSUPPORTED",
                    input_count=1,
                    detail=f"External Apply URL is malformed: {exc}",
                ),
                trace={"method": "external_apply_url", "error": str(exc)},
            )

        adapter = self.provider_registry.adapter_for(source_url)
        board = adapter.identify_board(source_url) if adapter else None
        if adapter is None or board is None or not adapter.supports_listing:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "unsupported",
                    reason_code="PROVIDER_UNSUPPORTED",
                    input_count=1,
                    evidence=[{"field": "external_apply_url", "url": source_url}],
                    detail="External Apply URL did not identify a supported native provider board.",
                ),
                trace={
                    "method": "external_apply_url",
                    "source_url": source_url,
                    "provider": adapter.name if adapter else None,
                    **(fallback_trace or {}),
                },
            )

        trace = {
            "method": "external_apply_url",
            "source_url": source_url,
            "job_list_page_url": board.url,
            "provider": adapter.name,
            "provider_detection": {
                "method": "external_apply_url",
                "provider": adapter.name,
                "url": board.url,
            },
            **(fallback_trace or {}),
        }
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="external_apply_url",
            evidence_url=source_url,
        )
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                provider=adapter.name,
                input_count=1,
                output_count=1,
                evidence=[
                    {"field": "external_apply_url", "url": source_url},
                    {"field": "job_list_page_url", "url": board.url},
                ],
                detail="Native provider board derived from the LinkedIn External Apply URL.",
            ),
            updates={
                "job_list_page_url": board.url,
                "provider": adapter.name,
                "discovered_job_board": discovered,
                "provider_identity": _provider_identity(
                    context,
                    board.url,
                    discovered,
                    self.provider_registry,
                ),
            },
            trace=trace,
        )


class OpeningMatchStage:
    name = STAGE_OPENING_MATCH

    def __init__(
        self,
        service: OpeningMatchService,
        provider_registry: ProviderRegistry | None = None,
        max_job_board_attempts: int = 1,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        if not isinstance(max_job_board_attempts, int) or isinstance(
            max_job_board_attempts, bool
        ) or not 1 <= max_job_board_attempts <= 8:
            raise ValueError("max_job_board_attempts must be between one and eight")
        self.max_job_board_attempts = max_job_board_attempts

    def run(self, context: PipelineContext) -> StageExecution:
        if not context.job_list_page_url:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Job-board discovery did not produce an input.",
                )
            )
        if (
            context.job_board_portfolio is not None
            and (
                len(context.job_board_portfolio.boards) > 1
                or not context.job_board_portfolio.eligible_set_complete
            )
        ):
            return self._run_portfolio(context)
        started = time.perf_counter()
        try:
            match_discovered = getattr(self.service, "match_discovered_board", None)
            if context.discovered_job_board is not None and callable(match_discovered):
                opening_url, job_list_url, trace = match_discovered(
                    context.discovered_job_board,
                    context.company.job_title,
                    context.company.job_location,
                )
            else:
                opening_url, job_list_url, trace = self.service.match_opening(
                    context.job_list_page_url,
                    context.company.job_title,
                    context.company.job_location,
                )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            return _failed_execution(
                self.name,
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        updates = {"job_list_page_url": job_list_url}
        if opening_url:
            updates["open_position_url"] = opening_url
            inventory_hiring = _provider_inventory_hiring_evidence(
                context,
                trace,
                opening_url,
            )
            if inventory_hiring is not None:
                updates["hiring_identity_evidence"] = inventory_hiring
                updates["hiring_entity_name"] = inventory_hiring.hiring_entity_name
            provider_identity = _provider_identity(
                context,
                job_list_url,
                context.discovered_job_board,
                self.provider_registry,
                hiring_evidence=inventory_hiring,
            )
            updates["provider_identity"] = provider_identity
            opening_identity = _opening_identity(
                context,
                opening_url,
                self.provider_registry,
                trace,
                provider_identity=provider_identity,
            )
            if opening_identity is not None:
                updates["opening_identity"] = opening_identity
                selection_evidence = _opening_selection_evidence(
                    opening_identity,
                    trace,
                )
                if selection_evidence is not None:
                    updates["opening_selection_evidence"] = selection_evidence
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "success",
                    provider=(
                        self.provider_registry.detect(opening_url)
                        if self.provider_registry.detect(opening_url) != "generic"
                        else context.provider
                    ),
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    output_count=1,
                    evidence=[{"field": "open_position_url", "url": opening_url}],
                ),
                updates=updates,
                trace=trace,
            )

        if not context.company.job_title:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_applicable",
                    provider=context.provider,
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    detail="No target title was provided; job-board discovery was the requested outcome.",
                ),
                updates=updates,
                trace=trace,
            )

        diagnostic = diagnose_opening_availability(trace, context.company.source_trace)
        trace["availability_diagnostic"] = {
            "disposition": diagnostic.disposition,
            "confidence": diagnostic.confidence,
            "reason_code": diagnostic.reason_code,
            **diagnostic.evidence,
        }
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code=diagnostic.reason_code,
                provider=context.provider,
                duration_ms=_elapsed_ms(started),
                input_count=1,
                evidence=[
                    {
                        "type": "availability_diagnostic",
                        "disposition": diagnostic.disposition,
                        "confidence": diagnostic.confidence,
                        **diagnostic.evidence,
                    }
                ],
                detail=diagnostic.detail,
            ),
            updates=updates,
            trace=trace,
        )

    def _run_portfolio(self, context: PipelineContext) -> StageExecution:
        portfolio = context.job_board_portfolio
        assert portfolio is not None
        started = time.perf_counter()
        attempts: list[dict] = []
        diagnostics = []
        match_discovered = getattr(self.service, "match_discovered_board", None)
        for position, discovered in enumerate(
            portfolio.boards[: self.max_job_board_attempts]
        ):
            board = discovered.board
            try:
                if callable(match_discovered):
                    opening_url, job_list_url, trace = match_discovered(
                        discovered,
                        context.company.job_title,
                        context.company.job_location,
                    )
                else:
                    opening_url, job_list_url, trace = self.service.match_opening(
                        board.url,
                        context.company.job_title,
                        context.company.job_location,
                    )
            except FetchError as exc:
                failure = project_fetch_error(exc)
                reason_code = failure["reason_code"]
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": board.url,
                        "status": "incomplete",
                        "reason_code": reason_code,
                        "fetch_failure": failure,
                    }
                )
                diagnostics.append((reason_code, None))
                continue
            except DiscoveryError as exc:
                reason_code = canonical_reason_code(exc.code)
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": board.url,
                        "status": "incomplete",
                        "reason_code": reason_code,
                        "trace": exc.trace,
                    }
                )
                diagnostics.append((reason_code, None))
                continue

            if opening_url:
                inventory_hiring = _provider_inventory_hiring_evidence(
                    context,
                    trace,
                    opening_url,
                )
                provider_identity = _provider_identity(
                    context,
                    job_list_url,
                    discovered,
                    self.provider_registry,
                    hiring_evidence=inventory_hiring,
                )
                opening_identity = _opening_identity(
                    context,
                    opening_url,
                    self.provider_registry,
                    trace,
                    provider_identity=provider_identity,
                )
                identity_updates: dict[str, object] = {
                    "provider_identity": provider_identity,
                }
                if inventory_hiring is not None:
                    identity_updates["hiring_identity_evidence"] = inventory_hiring
                    identity_updates["hiring_entity_name"] = (
                        inventory_hiring.hiring_entity_name
                    )
                if opening_identity is not None:
                    identity_updates["opening_identity"] = opening_identity
                    selection_evidence = _opening_selection_evidence(
                        opening_identity,
                        trace,
                    )
                    if selection_evidence is not None:
                        identity_updates["opening_selection_evidence"] = (
                            selection_evidence
                        )
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": job_list_url,
                        "status": "exact",
                        "trace": trace,
                    }
                )
                portfolio_trace = self._portfolio_trace(portfolio, attempts, "exact")
                return StageExecution(
                    result=make_stage_result(
                        self.name,
                        "success",
                        provider=board.provider,
                        duration_ms=_elapsed_ms(started),
                        input_count=len(attempts),
                        output_count=1,
                        evidence=[{"field": "open_position_url", "url": opening_url}],
                    ),
                    updates={
                        "job_list_page_url": job_list_url,
                        "discovered_job_board": discovered,
                        "provider": board.provider,
                        "open_position_url": opening_url,
                        **identity_updates,
                    },
                    trace=portfolio_trace,
                )

            diagnostic = diagnose_opening_availability(
                trace,
                context.company.source_trace,
            )
            diagnostics.append((diagnostic.reason_code, diagnostic))
            attempts.append(
                {
                    "position": position,
                    "provider": board.provider,
                    "board_url": job_list_url,
                    "status": diagnostic.disposition,
                    "reason_code": diagnostic.reason_code,
                    "trace": trace,
                }
            )

        attempted_all = len(attempts) == len(portfolio.boards)
        portfolio_complete = portfolio.eligible_set_complete and attempted_all
        incomplete = next(
            (
                (reason_code, diagnostic)
                for reason_code, diagnostic in diagnostics
                if reason_code
                not in {
                    "OPENING_DISCOVERY_INCOMPLETE",
                    "OPENING_NOT_FOUND",
                    "NO_PUBLIC_OPENINGS",
                }
            ),
            None,
        )
        if incomplete is not None:
            reason_code, diagnostic = incomplete
            detail = (
                diagnostic.detail
                if diagnostic is not None
                else "A verified job board could not be checked conclusively."
            )
        elif not portfolio_complete:
            reason_code = "JOB_BOARD_PORTFOLIO_INCOMPLETE"
            detail = (
                "Eligible job boards remain unattempted or the bounded portfolio was "
                "truncated; company-wide opening absence is not established."
            )
        elif any(
            reason_code == "OPENING_DISCOVERY_INCOMPLETE"
            for reason_code, _diagnostic in diagnostics
        ):
            reason_code = "OPENING_DISCOVERY_INCOMPLETE"
            detail = (
                "Every eligible job board was attempted, but at least one inventory "
                "could not be verified as complete."
            )
        elif diagnostics and all(
            reason_code == "NO_PUBLIC_OPENINGS"
            for reason_code, _diagnostic in diagnostics
        ):
            reason_code = "NO_PUBLIC_OPENINGS"
            detail = "Every eligible verified job board returned a complete empty inventory."
        else:
            reason_code = "OPENING_NOT_FOUND"
            detail = (
                "Every eligible verified job board was checked completely, but no title "
                "met the match threshold."
            )

        trace = self._portfolio_trace(portfolio, attempts, "no_exact")
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code=reason_code,
                provider=context.provider,
                duration_ms=_elapsed_ms(started),
                input_count=len(attempts),
                evidence=[
                    {
                        "type": "job_board_portfolio",
                        "attempted_count": len(attempts),
                        "eligible_count": len(portfolio.boards),
                        "eligible_set_complete": portfolio.eligible_set_complete,
                    }
                ],
                detail=detail,
            ),
            updates={"job_list_page_url": portfolio.primary.board.url},
            trace=trace,
        )

    @staticmethod
    def _portfolio_trace(
        portfolio: JobBoardPortfolio,
        attempts: list[dict],
        stopped_reason: str,
    ) -> dict:
        return {
            "board_portfolio": {
                "eligible_count": len(portfolio.boards),
                "eligible_set_complete": portfolio.eligible_set_complete,
                "attempted_count": len(attempts),
                "unattempted_count": max(0, len(portfolio.boards) - len(attempts)),
                "stopped_reason": stopped_reason,
                "attempts": attempts,
            }
        }


def _failed_execution(
    stage: str,
    reason_code: str,
    started: float,
    detail: str,
    trace: dict | None = None,
) -> StageExecution:
    return StageExecution(
        result=make_stage_result(
            stage,
            "failed",
            reason_code=reason_code,
            duration_ms=_elapsed_ms(started),
            input_count=1,
            detail=detail,
        ),
        trace=trace or {"error": detail},
    )


def _trace_has_discovery_errors(value: object, key: str = "") -> bool:
    """Keep source-channel classification from hiding incomplete network/provider work."""

    normalized_key = key.lower()
    if normalized_key.endswith("_error") and value not in (None, "", [], {}):
        return True
    if normalized_key.endswith("_errors") and value not in (None, "", [], {}):
        return True
    if isinstance(value, dict):
        return any(_trace_has_discovery_errors(item, str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_trace_has_discovery_errors(item) for item in value)
    return False


def _upstream_stage_failed(context: PipelineContext, stage: str) -> bool:
    return any(
        result.stage == stage and result.status in {"failed", "unsupported"}
        for result in context.stage_results
    )


def _identity_stage_resolved_career_root(context: PipelineContext) -> bool:
    identity_results = [
        result
        for result in context.stage_results
        if result.stage == STAGE_HIRING_IDENTITY_RESOLUTION
    ]
    if len(identity_results) != 1 or identity_results[0].status != "success":
        return False
    if not context.career_root_url or not isinstance(identity_results[0].evidence, list):
        return False

    stage_trace = context.trace.get("stages", {}).get(
        STAGE_HIRING_IDENTITY_RESOLUTION
    )
    selected = stage_trace.get("selected") if isinstance(stage_trace, dict) else None
    selected_root = (
        selected.get("career_root_url") if isinstance(selected, dict) else None
    )
    if not isinstance(selected_root, str):
        return False

    root_evidence = []
    for item in identity_results[0].evidence:
        if not isinstance(item, dict):
            return False
        if item.get("field") == "career_root_url":
            if set(item) != {"field", "url"} or not isinstance(item["url"], str):
                return False
            root_evidence.append(item["url"])

    if len(root_evidence) != 1:
        return False
    try:
        normalized_root = normalize_url(context.career_root_url)
        return (
            normalize_url(root_evidence[0]) == normalized_root
            and normalize_url(selected_root) == normalized_root
        )
    except (TypeError, ValueError):
        return False


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _provider_identity(
    context: PipelineContext,
    job_list_url: str,
    discovered: DiscoveredJobBoard | None,
    registry: ProviderRegistry,
    *,
    candidate: VerifiedProviderCandidate | None = None,
    hiring_evidence: HiringIdentityEvidence | None = None,
    relationship_evidence: HiringRelationshipEvidence | None = None,
) -> ProviderIdentity:
    board = discovered.board if discovered is not None else None
    adapter = registry.adapter_for(job_list_url)
    if board is None and adapter is not None:
        board = adapter.identify_board(job_list_url)
    if board is None:
        canonical_board = canonicalize_identity_url(job_list_url)
        provider = "generic"
        tenant = tenant_locator(canonical_board)
        evidence_url = context.career_page_url or canonical_board
    else:
        canonical_board = canonicalize_identity_url(board.url)
        provider = board.provider
        tenant = board.identifier or tenant_locator(canonical_board)
        evidence_url = (
            discovered.relationship_evidence_url
            or discovered.evidence_url
            if discovered is not None
            else job_list_url
        )
    verified, method = _authorize_provider_board(
        context,
        provider,
        tenant,
        canonical_board,
        discovered=discovered,
    )
    effective_hiring = hiring_evidence or context.hiring_identity_evidence
    if (
        hiring_evidence is not None
        and hiring_evidence.verified
        and hiring_evidence.verification_method == "provider_inventory"
    ):
        verified, method = True, "provider_inventory"
    if not verified and candidate is not None:
        verified, method = _authorize_candidate_relationship(
            candidate,
            tenant,
            relationship_evidence,
        )
    return ProviderIdentity(
        hiring_entity_name=(
            effective_hiring.hiring_entity_name
            if effective_hiring is not None
            else context.hiring_entity_name or context.company.company_name
        ),
        provider=provider,
        tenant=tenant,
        canonical_board_url=canonical_board,
        evidence_url=canonicalize_identity_url(evidence_url),
        verification_method=method,
        relationship_verified=verified,
    )


def _authorize_provider_board(
    context: PipelineContext,
    provider: str,
    tenant: str,
    canonical_board: str,
    *,
    discovered: DiscoveredJobBoard | None = None,
) -> tuple[bool, str]:
    hiring = context.hiring_identity_evidence
    if hiring is None or not hiring.verified:
        return False, "linked_url_only"
    if context.career_root_url and _same_url(context.career_root_url, canonical_board):
        return True, "identity_career_root"
    if (
        provider == "generic"
        and discovered is not None
        and discovered.detection_method == "verified_declared_inventory"
        and context.career_page_url
        and _same_url(discovered.evidence_url, canonical_board)
        and _same_url(context.career_page_url, canonical_board)
    ):
        return True, "verified_declared_inventory"
    if _identity_aliases(hiring.hiring_entity_name) & _identity_aliases(tenant):
        return True, "tenant_name_match"
    if provider == "generic" and context.career_page_url and _same_site(
        context.career_page_url, canonical_board
    ):
        return True, "first_party_same_site"
    if (
        provider != "generic"
        and discovered is not None
        and discovered.detection_method == "page_evidence"
        and context.career_page_url
        and _same_url(discovered.evidence_url, canonical_board)
        and (
            _same_url(context.career_page_url, canonical_board)
            or (
                discovered.relationship_evidence_url is not None
                and _same_url(
                    discovered.relationship_evidence_url,
                    context.career_page_url,
                )
            )
        )
    ):
        return True, "verified_first_party_provider_page"
    if (
        provider != "generic"
        and discovered is not None
        and discovered.detection_method == "linked_url_evidence"
        and context.career_page_url
        and _same_url(discovered.evidence_url, canonical_board)
        and discovered.relationship_evidence_url is not None
        and _same_url(
            discovered.relationship_evidence_url,
            context.career_page_url,
        )
    ):
        return True, "verified_first_party_handoff"
    return False, "linked_url_only"


def _candidate_hiring_relationship(
    context: PipelineContext,
    selected: VerifiedProviderCandidate,
) -> HiringRelationshipEvidence | None:
    candidate = selected.candidate
    try:
        evidence_url = canonicalize_identity_url(candidate.url)
    except (TypeError, ValueError):
        return None
    tenant = selected.discovered_board.board.identifier or ""
    provider = selected.discovered_board.board.provider
    company_name = context.hiring_entity_name or context.company.company_name
    if candidate.source_kind == "external_apply" and _same_url(
        candidate.url,
        context.company.external_apply_url or "",
    ):
        evidence_type = "linkedin_external_apply"
        strength = 100
    elif (
        candidate.source_kind == "first_party_ats_link"
        and context.hiring_identity_evidence is not None
        and context.hiring_identity_evidence.verified
        and context.career_page_url
        and _same_url(candidate.url, context.career_page_url)
    ):
        evidence_type = "first_party_handoff"
        strength = 95
    elif tenant and _strict_entity_key(company_name) == _strict_entity_key(tenant):
        evidence_type = "provider_tenant_match"
        strength = 80
    else:
        evidence_type = "unverified_candidate"
        strength = 0
    return HiringRelationshipEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=company_name,
        provider=provider,
        tenant=tenant,
        evidence_type=evidence_type,
        evidence_url=evidence_url,
        strength=strength,
        verified=strength >= 80,
    )


def _candidate_hiring_evidence(
    context: PipelineContext,
    relationship: HiringRelationshipEvidence,
) -> HiringIdentityEvidence | None:
    if not relationship.verified:
        return context.hiring_identity_evidence
    if (
        context.hiring_identity_evidence is not None
        and context.hiring_identity_evidence.verified
    ):
        return context.hiring_identity_evidence
    return HiringIdentityEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=relationship.hiring_entity_name,
        relationship_type=(
            "same_entity"
            if _strict_entity_key(context.company.company_name)
            == _strict_entity_key(relationship.hiring_entity_name)
            else "input_asserted"
        ),
        verification_method=relationship.evidence_type,
        verified=True,
        evidence_url=relationship.evidence_url,
    )


def _authorize_candidate_relationship(
    selected: VerifiedProviderCandidate,
    tenant: str,
    relationship: HiringRelationshipEvidence | None,
) -> tuple[bool, str]:
    if relationship is None or not relationship.verified:
        return False, "linked_url_only"
    board = selected.discovered_board.board
    if board.provider != relationship.provider or tenant != relationship.tenant:
        return False, "linked_url_only"
    return True, relationship.evidence_type


def _strict_entity_key(value: str) -> str:
    ignored = {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "the",
    }
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in ignored
    ]
    return "".join(tokens)


def _provider_inventory_hiring_evidence(
    context: PipelineContext,
    trace: dict,
    opening_url: str,
) -> HiringIdentityEvidence | None:
    """Promote only same-entity organization evidence from verified native inventory."""

    if not isinstance(trace, dict):
        return None
    selected = trace.get("selected")
    provider_api = trace.get("provider_api")
    inventory = provider_api.get("inventory") if isinstance(provider_api, dict) else None
    if (
        not isinstance(selected, dict)
        or not isinstance(inventory, dict)
        or inventory.get("source") != "native_adapter"
        or inventory.get("complete") is not True
        or not _same_url(str(selected.get("url") or ""), opening_url)
    ):
        return None
    organization = selected.get("hiring_organization_name")
    expected = context.hiring_entity_name or context.company.company_name
    if (
        not isinstance(organization, str)
        or not _strict_entity_key(organization)
        or _strict_entity_key(organization) != _strict_entity_key(expected)
    ):
        return None
    relationship_type = (
        context.hiring_identity_evidence.relationship_type
        if context.hiring_identity_evidence is not None
        else "same_entity"
    )
    return HiringIdentityEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=expected,
        relationship_type=relationship_type,
        verification_method="provider_inventory",
        verified=True,
        evidence_url=opening_url,
    )


def _opening_identity(
    context: PipelineContext,
    opening_url: str,
    registry: ProviderRegistry,
    match_trace: dict | None = None,
    *,
    provider_identity: ProviderIdentity | None = None,
) -> OpeningIdentity | None:
    provider_identity = provider_identity or context.provider_identity
    if provider_identity is None:
        return None
    canonical_opening = canonicalize_identity_url(opening_url)
    if provider_identity.provider == "generic":
        if not (
            _same_site(provider_identity.canonical_board_url, canonical_opening)
            or _trace_binds_declared_inventory(
                match_trace,
                opening_url,
                provider_identity,
                context.discovered_job_board,
            )
        ):
            return None
        return OpeningIdentity(
            hiring_entity_name=provider_identity.hiring_entity_name,
            provider="generic",
            tenant=provider_identity.tenant,
            canonical_board_url=provider_identity.canonical_board_url,
            canonical_opening_url=canonical_opening,
        )
    adapter = registry.adapter_named(provider_identity.provider)
    board = adapter.identify_board(opening_url) if adapter is not None else None
    if board is None:
        if not (
            _same_site(provider_identity.canonical_board_url, canonical_opening)
            or _trace_binds_opening_to_provider_board(
                match_trace,
                opening_url,
                provider_identity,
                context.discovered_job_board,
            )
        ):
            return None
        tenant = provider_identity.tenant
    else:
        if board.provider != provider_identity.provider:
            return None
        canonical_board = canonicalize_identity_url(board.url)
        tenant = board.identifier or tenant_locator(canonical_board)
        if tenant != provider_identity.tenant and not (
            _identity_aliases(tenant) & _identity_aliases(provider_identity.tenant)
        ):
            return None
        if (
            canonical_board != provider_identity.canonical_board_url
            and not _trace_binds_opening_to_provider_board(
                match_trace,
                opening_url,
                provider_identity,
                context.discovered_job_board,
            )
        ):
            return None
        tenant = provider_identity.tenant
    return OpeningIdentity(
        hiring_entity_name=provider_identity.hiring_entity_name,
        provider=provider_identity.provider,
        tenant=tenant,
        canonical_board_url=provider_identity.canonical_board_url,
        canonical_opening_url=canonical_opening,
    )


def _trace_binds_declared_inventory(
    match_trace: dict | None,
    opening_url: str,
    provider_identity: ProviderIdentity,
    discovered: DiscoveredJobBoard | None,
) -> bool:
    if (
        provider_identity.verification_method != "verified_declared_inventory"
        or discovered is None
        or discovered.detection_method != "verified_declared_inventory"
        or not isinstance(match_trace, dict)
    ):
        return False
    provider_api = match_trace.get("provider_api")
    detection = (
        provider_api.get("provider_detection")
        if isinstance(provider_api, dict)
        else None
    )
    selected = match_trace.get("selected")
    reasons = selected.get("reasons") if isinstance(selected, dict) else None
    return bool(
        isinstance(detection, dict)
        and detection.get("method") == "verified_declared_inventory"
        and detection.get("inventory_complete") is True
        and _same_url(
            str(detection.get("url") or ""),
            provider_identity.canonical_board_url,
        )
        and isinstance(detection.get("endpoint_url"), str)
        and isinstance(selected, dict)
        and _same_url(str(selected.get("url") or ""), opening_url)
        and isinstance(reasons, list)
        and "listing origin: verified_declared_inventory" in reasons
    )


def _opening_selection_evidence(
    opening_identity: OpeningIdentity,
    trace: dict | None,
) -> OpeningSelectionEvidence | None:
    if not isinstance(trace, dict):
        return None
    selected = trace.get("selected")
    if not isinstance(selected, dict):
        return None
    selected_url = selected.get("url")
    title = selected.get("title")
    if (
        not isinstance(selected_url, str)
        or not _same_url(selected_url, opening_identity.canonical_opening_url)
        or not isinstance(title, str)
        or not title.strip()
    ):
        return None
    location = selected.get("location")
    if not isinstance(location, str) or not location.strip():
        location = None
    provider_api = trace.get("provider_api")
    provider_api = provider_api if isinstance(provider_api, dict) else {}
    inventory = provider_api.get("inventory") or trace.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    detection = provider_api.get("provider_detection")
    if (
        not inventory
        and isinstance(detection, dict)
        and detection.get("method") == "verified_declared_inventory"
        and detection.get("inventory_complete") is True
    ):
        inventory = {
            "complete": True,
            "scope": "unknown",
            "candidate_count": detection.get("inventory_count"),
        }
    scope = inventory.get("scope")
    if scope not in {"full", "title_filtered"}:
        scope = "unknown"
    complete = inventory.get("complete")
    candidate_count = inventory.get("candidate_count")
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
        candidates = trace.get("candidates")
        candidate_count = len(candidates) if isinstance(candidates, list) else 1
    try:
        return OpeningSelectionEvidence(
            provider=opening_identity.provider,
            tenant=opening_identity.tenant,
            canonical_board_url=opening_identity.canonical_board_url,
            canonical_opening_url=opening_identity.canonical_opening_url,
            title=" ".join(title.split()),
            location=" ".join(location.split()) if location else None,
            inventory_scope=scope,
            inventory_complete=complete is True,
            candidate_count=max(0, candidate_count),
        )
    except (TypeError, ValueError):
        return None


def _same_url(left: str, right: str) -> bool:
    try:
        return canonicalize_identity_url(left) == canonicalize_identity_url(right)
    except ValueError:
        return False


def _same_site(left: str, right: str) -> bool:
    try:
        left_host = urlsplit(canonicalize_identity_url(left)).hostname or ""
        right_host = urlsplit(canonicalize_identity_url(right)).hostname or ""
        return _site_key(left_host) == _site_key(right_host)
    except ValueError:
        return False


def _trace_binds_opening_to_provider_board(
    trace: dict | None,
    opening_url: str,
    provider_identity: ProviderIdentity,
    discovered_board: DiscoveredJobBoard | None,
) -> bool:
    if not isinstance(trace, dict):
        return False
    provider_api = trace.get("provider_api")
    provider_api = provider_api if isinstance(provider_api, dict) else {}
    selected = trace.get("selected")
    detection = provider_api.get("provider_detection") or trace.get(
        "provider_detection"
    )
    if not isinstance(selected, dict):
        return False
    selected_url = selected.get("url")
    detected_url = detection.get("url") if isinstance(detection, dict) else None
    if not isinstance(selected_url, str):
        return False
    if not _same_url(selected_url, opening_url):
        return False
    detected_provider = provider_api.get("provider") or trace.get("provider")
    if detected_provider != provider_identity.provider:
        return False
    if isinstance(detected_url, str):
        return _same_url(detected_url, provider_identity.canonical_board_url)
    traced_board_url = trace.get("job_list_url")
    if isinstance(traced_board_url, str):
        return _same_url(traced_board_url, provider_identity.canonical_board_url)
    return bool(
        discovered_board is not None
        and discovered_board.board.provider == provider_identity.provider
        and _same_url(
            discovered_board.board.url,
            provider_identity.canonical_board_url,
        )
    )


def _identity_aliases(value: str) -> set[str]:
    ignored = {
        "co",
        "company",
        "corp",
        "corporation",
        "group",
        "holdings",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "the",
    }
    raw_tokens = re.findall(r"[a-z0-9]+", value.casefold())
    tokens = [
        token
        for token in raw_tokens
        if token not in ignored and len(token) >= 3
    ]
    aliases = {token for token in tokens if len(token) >= 4}
    if tokens:
        aliases.add("".join(tokens))
    if raw_tokens:
        aliases.add("".join(raw_tokens))
    return aliases


def _site_key(host: str) -> str:
    labels = [label for label in host.casefold().rstrip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    country_second_levels = {"ac", "co", "com", "gov", "net", "org"}
    width = 3 if len(labels[-1]) == 2 and labels[-2] in country_second_levels else 2
    return ".".join(labels[-width:])
