from __future__ import annotations

from dataclasses import replace
import time
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from ..contracts import FetchClient, PipelineContext, StageExecution
from ..company_discovery_evidence import (
    CompanyDiscoveryEvidenceStore,
    VerifiedCareerEvidence,
    VerifiedCompanyDiscoveryEvidence,
    VerifiedProviderBoardEvidence,
)
from ..errors import DiscoveryError
from ..homepage_navigation import HomepageNavigationEvidence
from ..candidate_portfolio import (
    CompositeCandidateDiscovery,
    ProviderCandidatePortfolioBuilder,
)
from ..candidate_discovery_coordinator import (
    CandidateDiscoveryCoordinator,
    CandidateDiscoveryInput,
    CandidateDiscoveryRoute,
    CandidateDiscoveryRouteStatus,
    ExternalApplyObservation,
    WebsiteCareerRouteInput,
    WebsiteCareerRouteSuppression,
    canonicalize_linkedin_evidence_url,
)
from ..identity_continuity import (
    HiringIdentityEvidence,
    HiringRelationshipEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
    validate_opening_identity_chain,
)
from ..job_board import (
    DiscoveredJobBoard,
    JobBoard,
    JobBoardPortfolio,
    JobBoardRouteEvidence,
)
from ..models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    StageResult,
)
from ..opening_availability import diagnose_opening_availability
from ..opening_selection_validation import validate_opening_selection
from ..providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from ..provider_candidates import (
    MAX_PROVIDER_CANDIDATES,
    CandidateDiscoveryRequest,
    ProviderCandidate,
    ProviderCandidatePool,
    STORED_PROVIDER_CANDIDATE_SOURCE_KINDS,
    VerifiedProviderCandidate,
    provider_employer_matches_company,
)
from ..reasons import canonical_reason_code, make_stage_result
from ..result_identity import canonicalize_identity_url, tenant_locator
from ..source_posting import trusted_linkedin_native_posting
from ..web import FetchError, domain_of, normalize_url
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

    def __init__(
        self,
        service: CareerDiscoveryService,
        company_discovery_evidence_store: CompanyDiscoveryEvidenceStore | None = None,
        provider_registry: ProviderRegistry | None = None,
        enable_parallel_candidate_discovery: bool = True,
    ) -> None:
        self.service = service
        self.company_discovery_evidence_store = company_discovery_evidence_store
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.enable_parallel_candidate_discovery = (
            enable_parallel_candidate_discovery
        )

    def run(self, context: PipelineContext) -> StageExecution:
        stored_career_url, stored_website_url = self._stored_career_candidate(context)
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
        if (
            self.enable_parallel_candidate_discovery
            and not context.career_root_url
            and (
            (
                not context.company_website_url
                and self._has_stored_provider_candidate(context)
            )
            or self._has_stored_provider_career_candidate(context)
            )
        ):
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail=(
                        "A stored listable provider candidate is deferred to S5 for "
                        "current inventory revalidation."
                    ),
                ),
                trace={
                    "scheduler": {
                        "status": "not_run",
                        "reason": "stored_provider_candidate_deferred_to_s5",
                    }
                },
            )
        provisional_website_url = (
            context.provisional_website_evidence.url
            if context.provisional_website_evidence is not None
            else None
        )
        discovery_website_url = (
            context.company_website_url
            or provisional_website_url
            or stored_website_url
        )
        if not discovery_website_url:
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
                    "preferred_url": context.career_root_url or stored_career_url,
                    "target_title": context.company.job_title,
                    "target_location": context.company.job_location,
                }
                if context.homepage_navigation_evidence is not None:
                    find_kwargs["homepage_navigation_evidence"] = (
                        context.homepage_navigation_evidence
                    )
                career_url, trace = self.service.find_career_page(
                    discovery_website_url,
                    **find_kwargs,
                )
                if provisional_website_url and not context.company_website_url:
                    trace = {
                        **trace,
                        "provisional_official_website": {
                            "url": provisional_website_url,
                            "authority": "exploration_only",
                        },
                    }
                if stored_website_url and not context.company_website_url:
                    trace = {
                        **trace,
                        "stored_company_discovery_candidate": {
                            "career_url": stored_career_url,
                            "website_url": stored_website_url,
                            "authority": "candidate_requiring_current_revalidation",
                            "revalidated": True,
                        },
                    }
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
            self._invalidate_rejected_stored_career(
                context,
                stored_career_url,
                exc.trace,
            )
            return _failed_execution(
                self.name,
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        updates: dict[str, object] = {"career_page_url": career_url}
        evidence = [{"field": "career_page_url", "url": career_url}]
        recovered_hiring_identity = _revalidated_stored_career_hiring_identity(
            context,
            career_url=career_url,
            stored_career_url=stored_career_url,
            stored_website_url=stored_website_url,
        )
        provisional_hiring_identity = _provisional_career_hiring_identity(
            context,
            career_url=career_url,
            discovery_website_url=discovery_website_url,
            trace=trace,
        )
        if not (context.career_root_url and (not replay_root or trusted_identity_root)):
            self._save_verified_career(
                context,
                career_url,
                trace,
                verified_website_url=(
                    context.company_website_url
                    or (
                        stored_website_url
                        if recovered_hiring_identity is not None
                        and recovered_hiring_identity.verification_method
                        == "revalidated_stored_website_career"
                        else None
                    )
                ),
            )
        if recovered_hiring_identity is not None:
            updates["hiring_identity_evidence"] = recovered_hiring_identity
            updates["hiring_entity_name"] = recovered_hiring_identity.hiring_entity_name
            evidence.append(
                {
                    "type": "hiring_identity",
                    "relationship_type": recovered_hiring_identity.relationship_type,
                    "verified": True,
                    "evidence_url": recovered_hiring_identity.evidence_url,
                }
            )
            trace["revalidated_stored_career_identity"] = {
                "status": "verified",
                "relationship_type": recovered_hiring_identity.relationship_type,
                "verification_method": recovered_hiring_identity.verification_method,
                "evidence_url": recovered_hiring_identity.evidence_url,
            }
        elif provisional_hiring_identity is not None:
            updates["provisional_website_evidence"] = (
                context.provisional_website_evidence
            )
            if context.homepage_navigation_evidence is not None:
                updates["homepage_navigation_evidence"] = (
                    context.homepage_navigation_evidence
                )
            updates["hiring_identity_evidence"] = provisional_hiring_identity
            updates["hiring_entity_name"] = (
                provisional_hiring_identity.hiring_entity_name
            )
            evidence.append(
                {
                    "type": "hiring_identity",
                    "relationship_type": provisional_hiring_identity.relationship_type,
                    "verified": True,
                    "evidence_url": provisional_hiring_identity.evidence_url,
                }
            )
            trace["provisional_career_identity"] = {
                "status": "verified",
                "verification_method": (
                    provisional_hiring_identity.verification_method
                ),
                "evidence_url": provisional_hiring_identity.evidence_url,
            }
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=evidence,
                detail=detail,
            ),
            updates=updates,
            trace=trace,
        )

    def _stored_career_candidate(
        self,
        context: PipelineContext,
    ) -> tuple[str | None, str | None]:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url or context.career_root_url:
            return None, None
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return None, None
        if record is None or record.website is None:
            return None, None
        if not context.company_website_url:
            return (
                record.career.url if record.career is not None else None,
                record.website.url,
            )
        if record.career is None:
            return None, None
        try:
            current_website = normalize_url(context.company_website_url).rstrip("/")
            stored_website = normalize_url(record.career.website_url).rstrip("/")
        except (TypeError, ValueError):
            return None, None
        return (
            (record.career.url, record.career.website_url)
            if current_website == stored_website
            else (None, None)
        )

    def _has_stored_provider_candidate(self, context: PipelineContext) -> bool:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url:
            return False
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return False
        return bool(record is not None and record.provider_boards)

    def _has_stored_provider_career_candidate(
        self,
        context: PipelineContext,
    ) -> bool:
        record = self._stored_company_discovery_record(context)
        if (
            record is None
            or record.career is None
            or record.career.source != "verified_career_search"
        ):
            return False
        adapter = self.provider_registry.adapter_for(record.career.url)
        board = adapter.identify_board(record.career.url) if adapter else None
        return bool(adapter is not None and adapter.supports_listing and board is not None)

    def _stored_company_discovery_record(
        self,
        context: PipelineContext,
    ) -> VerifiedCompanyDiscoveryEvidence | None:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url:
            return None
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return None
        if record is None:
            return None
        if _strict_entity_key(record.company_name) != _strict_entity_key(
            context.company.company_name
        ) or not _same_url(record.linkedin_company_url, linkedin_url):
            return None
        return record

    def _save_verified_career(
        self,
        context: PipelineContext,
        career_url: str,
        trace: dict,
        *,
        verified_website_url: str | None = None,
    ) -> None:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        website_url = verified_website_url or context.company_website_url
        if store is None or not linkedin_url or not website_url:
            return
        source, evidence_url = _verified_career_provenance(trace, website_url)
        try:
            store.save(
                context.company.company_name,
                linkedin_url,
                career=VerifiedCareerEvidence(
                    url=career_url,
                    website_url=website_url,
                    source=source,
                    evidence_url=evidence_url,
                    observed_at=time.time(),
                ),
            )
        except (OSError, TypeError, ValueError):
            return

    def _invalidate_rejected_stored_career(
        self,
        context: PipelineContext,
        stored_career_url: str | None,
        trace: dict,
    ) -> None:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url or not stored_career_url:
            return
        # A stale transport response or a generic "not found" result is not
        # evidence that the stored relationship is wrong. TTL handles aging;
        # only an explicit rejection or a completed current semantic check may
        # erase this durable layer.
        if not isinstance(trace, dict) or not (
            trace.get("stored_candidate_identity_rejected") is True
            or _has_deterministic_stored_career_rejection(
                trace,
                stored_career_url,
            )
        ):
            return
        try:
            store.invalidate(
                context.company.company_name,
                linkedin_url,
                layer="career",
                evidence_url=stored_career_url,
            )
        except (OSError, TypeError, ValueError):
            return


def _verified_career_provenance(
    trace: dict,
    website_url: str,
) -> tuple[str, str]:
    selected = trace.get("selected") if isinstance(trace, dict) else None
    if not isinstance(selected, dict):
        return "verified_career_search", website_url

    reasons = selected.get("reasons")
    reason_values = (
        [str(reason) for reason in reasons]
        if isinstance(reasons, list)
        else []
    )
    singular_reason = selected.get("reason")
    if isinstance(singular_reason, str):
        reason_values.append(singular_reason)
    reason_text = " ".join(reason_values).casefold()
    origin = selected.get("origin")
    selected_page_source = trace.get("selected_page_source")

    if (
        selected_page_source == "provider_adapter"
        or origin == "derived_provider_config"
        or "provider" in reason_text
        or "ats" in reason_text
    ):
        source = "provider_handoff"
    elif origin in {
        "page_link",
        "verified_homepage_navigation",
        "first_party_bundle_navigation",
    }:
        source = "first_party_navigation"
    else:
        # Sitemap, search, stored, and identity-supplied candidates are durable
        # only as currently verified discovery results, never as navigation.
        source = "verified_career_search"

    source_url = selected.get("source_url")
    evidence_url = source_url if isinstance(source_url, str) and source_url else website_url
    return source, evidence_url


def _has_deterministic_stored_career_rejection(
    trace: dict,
    stored_career_url: str,
) -> bool:
    preferred_url = trace.get("preferred_career_root")
    if not isinstance(preferred_url, str) or not _same_url(
        preferred_url,
        stored_career_url,
    ):
        return False

    schedules = trace.get("candidate_schedules")
    if not isinstance(schedules, list):
        schedule = trace.get("candidate_schedule")
        schedules = [schedule] if isinstance(schedule, dict) else []
    scheduled = any(
        isinstance(item, dict)
        and item.get("origin") == "identity_career_root"
        and isinstance(item.get("url"), str)
        and _same_url(item["url"], stored_career_url)
        for schedule in schedules
        if isinstance(schedule, dict)
        for item in schedule.get("scheduled", [])
        if isinstance(schedule.get("scheduled"), list)
    )
    if not scheduled:
        return False

    # A scheduled preferred candidate with no fetch/denial record reached the
    # semantic verifier and was rejected based on its current page content.
    for key in ("candidate_fetch_errors", "official_host_denial_skips"):
        entries = trace.get(key)
        if not isinstance(entries, list):
            continue
        if any(
            isinstance(entry, dict)
            and isinstance(entry.get("url"), str)
            and _same_url(entry["url"], stored_career_url)
            for entry in entries
        ):
            return False
    return True


class JobBoardDiscoveryStage:
    name = STAGE_JOB_BOARD_DISCOVERY

    def __init__(
        self,
        service: JobBoardDiscoveryService,
        provider_registry: ProviderRegistry | None = None,
        *,
        candidate_discovery: CompositeCandidateDiscovery | None = None,
        candidate_coordinator: CandidateDiscoveryCoordinator | None = None,
        enable_parallel_candidate_discovery: bool = False,
        evaluate_all_candidate_routes: bool = False,
        candidate_discovery_engine: str = "stage_v1",
        company_discovery_evidence_store: CompanyDiscoveryEvidenceStore | None = None,
        candidate_fetcher: FetchClient | None = None,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.candidate_discovery = candidate_discovery
        self.candidate_coordinator = candidate_coordinator
        self.enable_parallel_candidate_discovery = enable_parallel_candidate_discovery
        self.evaluate_all_candidate_routes = evaluate_all_candidate_routes
        if candidate_discovery_engine not in {"stage_v1", "coordinator_v2"}:
            raise ValueError("Candidate discovery engine is unsupported")
        if candidate_discovery_engine == "coordinator_v2" and not enable_parallel_candidate_discovery:
            raise ValueError("Coordinator candidate discovery requires candidate discovery")
        if candidate_discovery_engine == "coordinator_v2" and candidate_coordinator is None:
            raise ValueError("Coordinator candidate discovery requires a coordinator")
        self.candidate_discovery_engine = candidate_discovery_engine
        self.company_discovery_evidence_store = company_discovery_evidence_store
        self.candidate_fetcher = candidate_fetcher
        if evaluate_all_candidate_routes and not enable_parallel_candidate_discovery:
            raise ValueError(
                "Candidate route evaluation requires parallel candidate discovery"
            )

    def run(self, context: PipelineContext) -> StageExecution:
        if (
            self.candidate_discovery_engine == "stage_v1"
            and _upstream_stage_failed(context, STAGE_HIRING_IDENTITY_RESOLUTION)
        ):
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail=(
                        "A deterministic hiring-identity failure blocks provider candidates."
                    ),
                ),
                trace={
                    "scheduler": {
                        "status": "not_run",
                        "reason": "hiring_identity_unresolved",
                    }
                },
            )
        execution = self._run_without_evidence_store(context)
        self._save_verified_provider_board(context, execution)
        return execution

    def _run_without_evidence_store(self, context: PipelineContext) -> StageExecution:
        if self.candidate_discovery_engine == "coordinator_v2":
            return self._from_candidate_coordinator(context)
        if self.enable_parallel_candidate_discovery and self.candidate_discovery is not None:
            legacy_execution = (
                (
                    self._run_legacy(context)
                    if context.career_page_url
                    else StageExecution(
                        make_stage_result(
                            self.name,
                            "not_run",
                            detail=(
                                "Website/Career route did not produce a Career input."
                            ),
                        )
                    )
                )
                if self.evaluate_all_candidate_routes
                else None
            )
            candidate_execution, candidate_trace, staged_legacy_execution = (
                self._from_candidate_portfolio(context)
            )
            if legacy_execution is None:
                legacy_execution = staged_legacy_execution
            if candidate_execution is not None:
                if legacy_execution is not None and not self.evaluate_all_candidate_routes:
                    candidate_execution.trace.setdefault(
                        "candidate_scheduler",
                        {
                            "strategy": "direct_then_website_then_search",
                            "website_direct_status": legacy_execution.result.status,
                            "website_direct_reason_code": (
                                legacy_execution.result.reason_code
                            ),
                            "search_wave": "selected",
                        },
                    )
                if legacy_execution is not None:
                    return _merge_legacy_website_route(
                        context,
                        candidate_execution,
                        legacy_execution,
                        self.provider_registry,
                    )
                return candidate_execution
            legacy_execution = legacy_execution or self._run_legacy(context)
            if self.evaluate_all_candidate_routes:
                return _attach_legacy_route_trace(
                    legacy_execution,
                    candidate_trace,
                    self.provider_registry,
                )
            no_public_evidence = _verified_no_public_recruiting_surface(
                context,
                candidate_trace,
            )
            if no_public_evidence is not None:
                return StageExecution(
                    result=make_stage_result(
                        self.name,
                        "partial",
                        reason_code="NO_PUBLIC_OPENINGS",
                        input_count=1,
                        output_count=0,
                        evidence=[no_public_evidence],
                        detail=(
                            "The verified official website exposed no public recruiting "
                            "surface after bounded first-party and provider discovery."
                        ),
                    ),
                    trace={
                        "method": "verified_no_public_recruiting_surface",
                        "no_public_recruiting_surface": no_public_evidence,
                        "parallel_candidate_fallback": candidate_trace,
                    },
                )
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

    def _save_verified_provider_board(
        self,
        context: PipelineContext,
        execution: StageExecution,
    ) -> None:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        identity = execution.updates.get("provider_identity")
        if (
            store is None
            or not linkedin_url
            or execution.result.status != "success"
            or not isinstance(identity, ProviderIdentity)
            or not identity.relationship_verified
            or identity.provider == "generic"
            or not identity.tenant
        ):
            return

        discovered = execution.updates.get("discovered_job_board")
        source = _durable_provider_evidence_source(context, identity, discovered)
        if source is None:
            return
        adapter = self.provider_registry.adapter_for(identity.canonical_board_url)
        board = (
            adapter.identify_board(identity.canonical_board_url)
            if adapter is not None
            else None
        )
        if adapter is None or board is None:
            return
        canonicalize_board = getattr(adapter, "canonicalize_board", None)
        if callable(canonicalize_board):
            board = canonicalize_board(board)
        board_tenant = board.identifier or tenant_locator(board.url)
        if (
            board.provider != identity.provider
            or board_tenant != identity.tenant
        ):
            return
        try:
            store.save(
                context.company.company_name,
                linkedin_url,
                provider_board=VerifiedProviderBoardEvidence(
                    provider=identity.provider,
                    tenant=identity.tenant,
                    canonical_board_url=board.url,
                    relationship_evidence_url=identity.evidence_url,
                    verification_method=identity.verification_method,
                    source=source,
                    observed_at=time.time(),
                ),
            )
        except (OSError, TypeError, ValueError):
            return

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
                if portfolio is not None:
                    portfolio = _rank_first_party_portfolio(context, portfolio)
                discovered_board = portfolio.primary if portfolio is not None else None
                if discovered_board is not None:
                    job_list_url = discovered_board.board.url
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
            if reason_code == "JOB_BOARD_NOT_FOUND":
                dynamic_inventory_reason = _dynamic_inventory_failure_reason(exc.trace)
                if dynamic_inventory_reason is not None:
                    reason_code = dynamic_inventory_reason
                elif _trace_has_unlinked_third_party_handoff(exc.trace):
                    reason_code = "UNVERIFIABLE_THIRD_PARTY_HANDOFF"
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
                evidence=(
                    [_unlinked_third_party_handoff_evidence(exc.trace)]
                    if reason_code == "UNVERIFIABLE_THIRD_PARTY_HANDOFF"
                    else None
                ),
            )

        embedded_provider_boards = _first_party_inventory_provider_boards(
            trace,
            self.provider_registry,
        )
        if embedded_provider_boards and (
            discovered_board is None
            or discovered_board.board.provider == "generic"
        ):
            existing_boards = list(portfolio.boards) if portfolio is not None else []
            combined = _deduplicate_public_board_identities(
                [*embedded_provider_boards, *existing_boards]
            )
            portfolio = JobBoardPortfolio(
                boards=tuple(combined[:8]),
                eligible_set_complete=(
                    portfolio.eligible_set_complete if portfolio is not None else True
                ),
            )
            discovered_board = portfolio.primary
            job_list_url = discovered_board.board.url
            trace["provider_board_promotion"] = {
                "source": "verified_first_party_listing_inventory",
                "provider": discovered_board.board.provider,
                "url": discovered_board.board.url,
            }

        updates: dict[str, object] = {}
        if portfolio is not None:
            job_list_url, provider = _project_job_board_portfolio(
                trace,
                updates,
                portfolio,
            )
            discovered_board = portfolio.primary
        else:
            provider = trace.get("provider") or self.provider_registry.detect(
                job_list_url
            )
            provider = None if provider == "generic" else provider
            updates.update({"job_list_page_url": job_list_url, "provider": provider})
            if discovered_board is not None:
                updates["discovered_job_board"] = discovered_board
        updates["provider_identity"] = _provider_identity(
            context,
            job_list_url,
            discovered_board,
            self.provider_registry,
        )
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

    def _from_candidate_coordinator(self, context: PipelineContext) -> StageExecution:
        started = time.perf_counter()
        assert self.candidate_coordinator is not None
        source_input = _coordinator_source_input(context)
        website_input = _coordinator_website_input(context)
        website_suppression = _coordinator_website_suppression(
            context,
            website_input,
        )
        legacy_execution = None
        if website_input is not None and website_suppression is None and context.career_page_url:
            # Preserve the strongest first-party evidence before lower-confidence
            # search and tenant probes can consume the shared S5 budget.
            legacy_execution = self._run_legacy(context)
        coordinated = self.candidate_coordinator.discover(
            source_input,
            website_career_input=website_input,
            website_career_suppression=website_suppression,
        )
        route_trace = {
            result.route.value: _coordinator_route_trace(result)
            for result in coordinated.route_results
        }

        builder = ProviderCandidatePortfolioBuilder(
            self.provider_registry,
            self.candidate_fetcher,
        )
        evaluated: list[
            tuple[
                CandidateDiscoveryRoute,
                VerifiedProviderCandidate,
                HiringRelationshipEvidence,
            ]
        ] = []
        verification_trace: dict[str, dict] = {}
        for route_result in coordinated.route_results:
            if route_result.status is not CandidateDiscoveryRouteStatus.COMPLETED:
                continue
            route_pool = ProviderCandidatePool.build(
                route_result.candidates,
                limit=MAX_PROVIDER_CANDIDATES,
            )
            built = builder.build(route_pool)
            verification_trace[route_result.route.value] = built.trace
            for item in built.verified:
                relationship = _candidate_hiring_relationship(context, item)
                if relationship is not None:
                    evaluated.append((route_result.route, item, relationship))

        evaluated.sort(
            key=lambda entry: (
                not entry[2].verified,
                -entry[2].strength,
                -entry[1].candidate.priority,
                entry[1].candidate.result_rank or 0,
                entry[1].candidate.url.casefold(),
                entry[0].value,
            )
        )
        boards: list[DiscoveredJobBoard] = []
        for _, item, _ in evaluated:
            if any(
                existing.board.provider == item.discovered_board.board.provider
                and _same_url(existing.board.url, item.discovered_board.board.url)
                for existing in boards
            ):
                continue
            boards.append(item.discovered_board)
            if len(boards) >= 8:
                break

        coordinator_trace = {
            "method": "candidate_discovery_coordinator_v2",
            "physical_concurrency": False,
            "candidate_count": len(coordinated.candidates),
            "truncated": coordinated.truncated,
            "routes": route_trace,
            "candidate_attributions": [
                {
                    "url": attribution.candidate_url,
                    "routes": [route.value for route in attribution.routes],
                }
                for attribution in coordinated.attributions
            ],
            "candidate_verification": verification_trace,
        }
        if not boards:
            if legacy_execution is not None:
                return StageExecution(
                    result=legacy_execution.result,
                    updates=legacy_execution.updates,
                    trace={
                        **legacy_execution.trace,
                        "candidate_coordinator": coordinator_trace,
                    },
                    evidence_lineage=legacy_execution.evidence_lineage,
                )
            retryable_route = next(
                (
                    result
                    for result in coordinated.route_results
                    if result.failure is not None and result.failure.retryable
                ),
                None,
            )
            reason_code = (
                retryable_route.failure.reason_code
                if retryable_route is not None
                else "JOB_BOARD_NOT_FOUND"
            )
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "failed",
                    reason_code=reason_code,
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    detail="Independent candidate routes produced no adapter-verified Job Board.",
                ),
                trace={"candidate_coordinator": coordinator_trace},
            )

        retained = {
            (board.board.provider, canonicalize_identity_url(board.board.url))
            for board in boards
        }
        route_evidence = _deduplicate_route_evidence(
            [
                _candidate_route_evidence(
                    item,
                    relationship,
                    route_kind=route.value,
                )
                for route, item, relationship in evaluated
                if (
                    item.discovered_board.board.provider,
                    canonicalize_identity_url(item.discovered_board.board.url),
                )
                in retained
            ]
        )
        provider_route = next(
            (
                result
                for result in coordinated.route_results
                if result.route is CandidateDiscoveryRoute.PROVIDER_SEARCH
            ),
            None,
        )
        portfolio = JobBoardPortfolio(
            boards=tuple(boards),
            eligible_set_complete=(
                not coordinated.truncated
                and all(
                    result.status
                    in {
                        CandidateDiscoveryRouteStatus.COMPLETED,
                        CandidateDiscoveryRouteStatus.NOT_APPLICABLE,
                        CandidateDiscoveryRouteStatus.SUPPRESSED,
                    }
                    for result in coordinated.route_results
                )
                and not (
                    provider_route is not None and provider_route.candidates
                )
            ),
            route_evidence=tuple(route_evidence),
        )
        selected_route, selected, relationship_evidence = evaluated[0]
        discovered = selected.discovered_board
        hiring_evidence = _candidate_hiring_evidence(context, relationship_evidence)
        provider_identity = _provider_identity(
            context,
            discovered.board.url,
            discovered,
            self.provider_registry,
            candidate=selected,
            hiring_evidence=hiring_evidence,
            relationship_evidence=relationship_evidence,
        )
        updates: dict[str, object] = {"provider_identity": provider_identity}
        if hiring_evidence is not None and hiring_evidence != context.hiring_identity_evidence:
            updates["hiring_identity_evidence"] = hiring_evidence
            updates["hiring_entity_name"] = hiring_evidence.hiring_entity_name
        trace = {
            **coordinator_trace,
            "selected": selected.candidate.to_trace_payload(),
            "selected_route": selected_route.value,
            "provider": discovered.board.provider,
            "job_list_page_url": discovered.board.url,
            "relationship_verified": provider_identity.relationship_verified,
            "relationship_method": provider_identity.verification_method,
            "relationship_evidence": relationship_evidence.to_trace_payload(),
        }
        _project_job_board_portfolio(trace, updates, portfolio)
        relationship_verified = any(route.authorized for route in portfolio.route_evidence)
        execution = StageExecution(
            result=make_stage_result(
                self.name,
                "success" if relationship_verified else "partial",
                reason_code=None if relationship_verified else "COMPANY_IDENTITY_AMBIGUOUS",
                provider=discovered.board.provider,
                duration_ms=_elapsed_ms(started),
                input_count=len(coordinated.candidates),
                output_count=1,
                evidence=[
                    {"field": "job_list_page_url", "url": discovered.board.url},
                    {"field": "candidate_route", "value": selected_route.value},
                ],
                detail=(
                    "Provider board selected from independently coordinated candidate routes."
                    if relationship_verified
                    else "Candidate routes produced provider boards, but no route yet proves the hiring relationship."
                ),
            ),
            updates=updates,
            trace=trace,
        )
        if legacy_execution is not None:
            return _merge_legacy_website_route(
                context,
                execution,
                legacy_execution,
                self.provider_registry,
            )
        return execution

    def _from_candidate_portfolio(
        self,
        context: PipelineContext,
    ) -> tuple[StageExecution | None, dict, StageExecution | None]:
        started = time.perf_counter()
        request = CandidateDiscoveryRequest(
            company_name=context.hiring_entity_name or context.company.company_name,
            target_title=context.company.job_title,
            target_location=context.company.job_location,
            company_website_url=context.company_website_url or None,
            career_page_url=context.career_page_url,
            external_apply_url=context.company.external_apply_url,
            linkedin_company_url=context.company.linkedin_company_url,
        )
        direct_pool, direct_trace = self.candidate_discovery.discover_wave(
            request,
            "direct",
        )
        stored_provider_candidates = self._stored_provider_candidates(context)
        if stored_provider_candidates:
            direct_pool = ProviderCandidatePool.build(
                (*direct_pool.candidates, *stored_provider_candidates),
                limit=MAX_PROVIDER_CANDIDATES,
            )
            direct_trace = {
                **direct_trace,
                "sources": [
                    *direct_trace.get("sources", []),
                    {
                        "source": "StoredProviderBoardDiscovery",
                        "wave": "direct",
                        "status": "success",
                        "candidate_count": len(stored_provider_candidates),
                        "trace": {
                            "source": "stored_verified_provider_board",
                            "authority": "candidate_requiring_current_revalidation",
                            "candidate_count": len(stored_provider_candidates),
                        },
                    },
                ],
                "pool": direct_pool.to_trace_payload(),
            }
        builder = ProviderCandidatePortfolioBuilder(
            self.provider_registry,
            self.candidate_fetcher,
        )
        direct_built = builder.build(direct_pool)
        direct_evaluated = tuple(
            (item, relationship)
            for item in direct_built.verified
            if (relationship := _candidate_hiring_relationship(context, item))
            is not None
        )
        verified_direct = tuple(
            item for item in direct_evaluated if item[1].verified
        )

        website_direct_execution = None
        website_provider_direct = False
        if (
            (
                not direct_pool.candidates
                or (stored_provider_candidates and not verified_direct)
            )
            and not self.evaluate_all_candidate_routes
            and context.career_page_url
        ):
            website_direct_execution = self._run_legacy(context)
            if website_direct_execution.result.status == "success":
                discovered = website_direct_execution.updates.get(
                    "discovered_job_board"
                )
                website_provider_direct = bool(
                    isinstance(discovered, DiscoveredJobBoard)
                    and discovered.board.provider != "generic"
                )
                direct_trace = {
                    **direct_trace,
                    "website_direct": {
                        "status": "success",
                        "reason_code": None,
                    },
                }

        search_trace: dict
        search_built = None
        if (
            verified_direct
            or stored_provider_candidates
            or website_provider_direct
        ) and not self.evaluate_all_candidate_routes:
            pool = direct_pool
            built = direct_built
            evaluated = direct_evaluated
            selected_wave = "direct"
            search_trace = {
                "wave": "search",
                "status": "skipped",
                "reason": (
                    "verified_direct_candidate"
                    if verified_direct
                    else "website_provider_board"
                    if website_provider_direct
                    else "stored_candidate_requires_inventory_revalidation"
                ),
                "sources": [
                    {
                        **source,
                        "status": "skipped",
                        "reason": (
                            "verified_direct_candidate"
                            if verified_direct
                            else "website_provider_board"
                            if website_provider_direct
                            else "stored_candidate_requires_inventory_revalidation"
                        ),
                    }
                    for source in direct_trace["sources"]
                    if source.get("wave") == "search"
                ],
            }
        else:
            search_pool, search_trace = self.candidate_discovery.discover_wave(
                request,
                "search",
            )
            search_built = builder.build(search_pool)
            search_evaluated = tuple(
                (item, relationship)
                for item in search_built.verified
                if (relationship := _candidate_hiring_relationship(context, item))
                is not None
            )
            if direct_pool.candidates:
                pool = ProviderCandidatePool.build(
                    (*direct_pool.candidates, *search_pool.candidates),
                    limit=MAX_PROVIDER_CANDIDATES,
                )
                built = builder.build(pool)
                evaluated = tuple(
                    (item, relationship)
                    for item in built.verified
                    if (relationship := _candidate_hiring_relationship(context, item))
                    is not None
                )
                selected_wave = (
                    "direct"
                    if verified_direct
                    else "search_with_stored_fallback"
                    if stored_provider_candidates
                    else "search"
                )
            else:
                pool = search_pool
                built = search_built
                evaluated = search_evaluated
                selected_wave = "search"

        discovery_trace = {
            **(
                direct_trace
                if selected_wave == "direct"
                else search_trace
            ),
            "strategy": (
                "exhaustive_route_evaluation"
                if self.evaluate_all_candidate_routes
                else "staged_direct_then_search"
            ),
            "selected_wave": selected_wave,
            "waves": {
                "direct": direct_trace,
                "search": search_trace,
            },
        }
        if website_direct_execution is not None:
            discovery_trace["website_direct_attempt"] = {
                "status": website_direct_execution.result.status,
                "reason_code": website_direct_execution.result.reason_code,
            }
        verification_trace = {
            **built.trace,
            "waves": {
                "direct": direct_built.trace,
                "search": search_built.trace if search_built is not None else {
                    "status": "skipped",
                    "reason": "verified_direct_candidate",
                },
            },
        }
        relationship_trace = {
            "direct": _relationship_wave_trace(direct_evaluated),
            "search": (
                _relationship_wave_trace(
                    search_evaluated if search_built is not None else ()
                )
                if search_built is not None
                else {
                    "status": "skipped",
                    "reason": "verified_direct_candidate",
                }
            ),
        }
        route_evaluation_trace = (
            _candidate_route_trace(
                context,
                direct_trace,
                search_trace,
                direct_built,
                search_built,
                direct_evaluated,
                search_evaluated if search_built is not None else (),
            )
            if self.evaluate_all_candidate_routes
            else None
        )
        if built.portfolio is None:
            return None, {
                "candidate_discovery": discovery_trace,
                "candidate_verification": verification_trace,
                "relationship_verification": relationship_trace,
                **(
                    {"route_evaluation": route_evaluation_trace}
                    if route_evaluation_trace is not None
                    else {}
                ),
            }, website_direct_execution

        if not evaluated:
            return None, {
                "candidate_discovery": discovery_trace,
                "candidate_verification": verification_trace,
                "relationship_verification": {
                    "status": "rejected",
                    "reason": "candidate_evidence_url_invalid",
                    "waves": relationship_trace,
                },
                **(
                    {"route_evaluation": route_evaluation_trace}
                    if route_evaluation_trace is not None
                    else {}
                ),
            }, website_direct_execution
        evaluated = tuple(
            sorted(
                evaluated,
                key=lambda item: (
                    not item[1].verified,
                    -item[1].strength,
                    item[0].candidate.url.casefold(),
                ),
            )
        )
        selected, relationship_evidence = evaluated[0]
        ordered_verified = (
            selected,
            *(item for item in built.verified if item is not selected),
        )
        portfolio = JobBoardPortfolio(
            boards=tuple(item.discovered_board for item in ordered_verified),
            eligible_set_complete=built.portfolio.eligible_set_complete,
            route_evidence=tuple(
                _candidate_route_evidence(
                    item,
                    next(
                        (
                            relationship
                            for evaluated_item, relationship in evaluated
                            if evaluated_item is item
                        ),
                        None,
                    ),
                )
                for item in ordered_verified
            ),
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
        updates: dict[str, object] = {"provider_identity": provider_identity}
        if (
            hiring_evidence is not None
            and hiring_evidence != context.hiring_identity_evidence
        ):
            updates["hiring_identity_evidence"] = hiring_evidence
            updates["hiring_entity_name"] = hiring_evidence.hiring_entity_name
        trace = {
            "method": "parallel_candidate_discovery",
            "candidate_discovery": discovery_trace,
            "candidate_verification": verification_trace,
            "relationship_verification": relationship_trace,
            "candidate_wave": selected_wave,
            "selected": selected.candidate.to_trace_payload(),
            "provider": discovered.board.provider,
            "job_list_page_url": discovered.board.url,
            "relationship_verified": provider_identity.relationship_verified,
            "relationship_method": provider_identity.verification_method,
            "relationship_evidence": relationship_evidence.to_trace_payload(),
        }
        _project_job_board_portfolio(trace, updates, portfolio)
        if route_evaluation_trace is not None:
            trace["route_evaluation"] = route_evaluation_trace
        relationship_verified = any(
            route.authorized for route in portfolio.route_evidence
        )
        execution = StageExecution(
            result=make_stage_result(
                self.name,
                "success" if relationship_verified else "partial",
                reason_code=(
                    None
                    if relationship_verified
                    else "COMPANY_IDENTITY_AMBIGUOUS"
                ),
                provider=discovered.board.provider,
                duration_ms=_elapsed_ms(started),
                input_count=len(pool.candidates),
                output_count=1,
                evidence=[
                    {
                        "field": (
                            "job_list_page_url"
                            if relationship_verified
                            else "candidate_job_board_url"
                        ),
                        "url": discovered.board.url,
                    },
                    {
                        "field": "candidate_source",
                        "value": selected.candidate.source_kind,
                    },
                ],
                detail=(
                    "Provider board selected from the merged candidate portfolio."
                    if relationship_verified
                    else (
                        "Provider candidates were retained for adapter verification, "
                        "but no route yet proves that a tenant recruits for the target "
                        "company."
                    )
                ),
            ),
            updates=updates,
            trace=trace,
        )
        return execution, trace, website_direct_execution

    def _stored_provider_candidates(
        self,
        context: PipelineContext,
    ) -> tuple[ProviderCandidate, ...]:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url:
            return ()
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return ()
        if record is None:
            return ()
        if _strict_entity_key(record.company_name) != _strict_entity_key(
            context.company.company_name
        ) or not _same_url(record.linkedin_company_url, linkedin_url):
            return ()
        candidates = []
        for evidence in record.provider_boards:
            try:
                candidates.append(
                    ProviderCandidate(
                        url=evidence.canonical_board_url,
                        source_kind="stored_verified_provider_board",
                        source_url=evidence.canonical_board_url,
                        company_name=(
                            context.hiring_entity_name
                            or context.company.company_name
                        ),
                        target_title=context.company.job_title,
                        target_location=context.company.job_location,
                        provider_hint=evidence.provider,
                    )
                )
            except (TypeError, ValueError):
                continue
        career = record.career
        website = record.website
        if (
            career is not None
            and website is not None
            and _same_url(career.website_url, website.url)
        ):
            adapter = self.provider_registry.adapter_for(career.url)
            board = adapter.identify_board(career.url) if adapter else None
            if adapter is not None and adapter.supports_listing and board is not None:
                try:
                    candidates.append(
                        ProviderCandidate(
                            url=career.url,
                            source_kind="stored_verified_career_provider",
                            source_url=career.evidence_url,
                            company_name=(
                                context.hiring_entity_name
                                or context.company.company_name
                            ),
                            target_title=context.company.job_title,
                            target_location=context.company.job_location,
                            provider_hint=adapter.name,
                        )
                    )
                except (TypeError, ValueError):
                    pass
        return tuple(candidates)

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
        company_discovery_evidence_store: CompanyDiscoveryEvidenceStore | None = None,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.company_discovery_evidence_store = company_discovery_evidence_store
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
        stored_inventory_identity = self._stored_inventory_identity(
            context,
            context.discovered_job_board,
            trace,
        )
        if stored_inventory_identity is not None:
            stored_hiring, stored_provider = stored_inventory_identity
            updates.update(
                {
                    "hiring_identity_evidence": stored_hiring,
                    "hiring_entity_name": stored_hiring.hiring_entity_name,
                    "provider_identity": stored_provider,
                }
            )
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
            if inventory_hiring is None and stored_inventory_identity is not None:
                inventory_hiring, provider_identity = stored_inventory_identity
            provider_identity = _promote_verified_native_provider_identity(
                provider_identity,
                opening_url,
                self.provider_registry,
                trace,
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
        accepted_exacts: list[dict[str, object]] = []
        stored_identity_updates: dict[str, object] = {}
        stored_canonical_inventory_complete = False
        match_discovered = getattr(self.service, "match_discovered_board", None)
        for position, discovered in enumerate(
            portfolio.boards[: self.max_job_board_attempts]
        ):
            board = discovered.board
            routes = portfolio.routes_for(discovered)
            relationship = _best_route_relationship(routes)
            route_authorized = _route_is_authorized(
                context,
                portfolio,
                discovered,
                relationship,
            )
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
                        "authorized": route_authorized,
                        "routes": _route_attempt_trace(routes),
                        "reason_code": reason_code,
                        "fetch_failure": failure,
                    }
                )
                if route_authorized:
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
                        "authorized": route_authorized,
                        "routes": _route_attempt_trace(routes),
                        "reason_code": reason_code,
                        "trace": exc.trace,
                    }
                )
                if route_authorized:
                    diagnostics.append((reason_code, None))
                continue

            if opening_url:
                inventory_hiring = _provider_inventory_hiring_evidence(
                    context,
                    trace,
                    opening_url,
                )
                route_hiring = (
                    _candidate_hiring_evidence(context, relationship)
                    if relationship is not None
                    else context.hiring_identity_evidence
                )
                effective_hiring = inventory_hiring or route_hiring
                stored_inventory_identity = self._stored_inventory_identity(
                    context,
                    discovered,
                    trace,
                )
                provider_identity = _provider_identity(
                    context,
                    job_list_url,
                    discovered,
                    self.provider_registry,
                    hiring_evidence=effective_hiring,
                    relationship_evidence=relationship,
                    allow_prior_identity=not bool(portfolio.route_evidence),
                )
                if (
                    not provider_identity.relationship_verified
                    and stored_inventory_identity is not None
                ):
                    effective_hiring, provider_identity = stored_inventory_identity
                provider_identity = _promote_verified_native_provider_identity(
                    provider_identity,
                    opening_url,
                    self.provider_registry,
                    trace,
                )
                opening_identity = _opening_identity(
                    context,
                    opening_url,
                    self.provider_registry,
                    trace,
                    provider_identity=provider_identity,
                    discovered_board=discovered,
                )
                selection_evidence = (
                    _opening_selection_evidence(opening_identity, trace)
                    if opening_identity is not None
                    else None
                )
                identity_issues = validate_opening_identity_chain(
                    hiring=effective_hiring,
                    provider=provider_identity,
                    opening=opening_identity,
                    open_position_url=opening_url,
                    job_list_page_url=job_list_url,
                )
                selection_issues, location_classification = (
                    validate_opening_selection(
                        selection=selection_evidence,
                        provider=provider_identity,
                        opening=opening_identity,
                        open_position_url=opening_url,
                        target_title=context.company.job_title,
                        target_location=context.company.job_location,
                    )
                )
                issues = list(dict.fromkeys([*identity_issues, *selection_issues]))
                if not portfolio.route_evidence:
                    # Schema-v1 and legacy in-memory portfolios had no route-local
                    # authority contract. Preserve their S6 behavior and leave the
                    # final identity decision to S7; new portfolios are checked here.
                    issues = []
                if (
                    portfolio.route_evidence
                    and not route_authorized
                    and inventory_hiring is None
                    and stored_inventory_identity is None
                ):
                    issues.append("HIRING_RELATIONSHIP_UNVERIFIED")
                identity_updates: dict[str, object] = {
                    "provider_identity": provider_identity,
                }
                if effective_hiring is not None:
                    identity_updates["hiring_identity_evidence"] = effective_hiring
                    identity_updates["hiring_entity_name"] = (
                        effective_hiring.hiring_entity_name
                    )
                if opening_identity is not None:
                    identity_updates["opening_identity"] = opening_identity
                    if selection_evidence is not None:
                        identity_updates["opening_selection_evidence"] = (
                            selection_evidence
                        )
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": job_list_url,
                        "status": (
                            "verified_exact" if not issues else "identity_rejected"
                        ),
                        "authorized": bool(
                            route_authorized
                            or inventory_hiring is not None
                            or stored_inventory_identity is not None
                        ),
                        "routes": _route_attempt_trace(routes),
                        "identity_issues": issues,
                        "location_classification": location_classification,
                        "trace": trace,
                    }
                )
                if issues:
                    continue
                accepted_exacts.append(
                    {
                        "opening_url": opening_url,
                        "job_list_url": job_list_url,
                        "discovered": discovered,
                        "provider": board.provider,
                        "updates": {
                        "job_list_page_url": job_list_url,
                        "discovered_job_board": discovered,
                        "provider": board.provider,
                        "open_position_url": opening_url,
                        **identity_updates,
                    },
                    }
                )
                continue

            diagnostic = diagnose_opening_availability(
                trace,
                context.company.source_trace,
            )
            stored_inventory_identity = self._stored_inventory_identity(
                context,
                discovered,
                trace,
            )
            provider_inventory_hiring = (
                _provider_inventory_board_hiring_evidence(context, trace)
            )
            provider_inventory_identity = None
            if provider_inventory_hiring is not None:
                candidate_provider_identity = _provider_identity(
                    context,
                    job_list_url,
                    discovered,
                    self.provider_registry,
                    hiring_evidence=provider_inventory_hiring,
                    relationship_evidence=relationship,
                    allow_prior_identity=not bool(portfolio.route_evidence),
                )
                if candidate_provider_identity.relationship_verified:
                    provider_inventory_identity = (
                        provider_inventory_hiring,
                        candidate_provider_identity,
                    )
            if stored_inventory_identity is not None:
                stored_canonical_inventory_complete = True
                route_authorized = True
            elif provider_inventory_identity is not None:
                stored_canonical_inventory_complete = True
                route_authorized = True
            if route_authorized:
                diagnostics.append((diagnostic.reason_code, diagnostic))
            durable_inventory_identity = (
                stored_inventory_identity or provider_inventory_identity
            )
            if durable_inventory_identity is not None and not stored_identity_updates:
                stored_hiring, stored_provider = durable_inventory_identity
                stored_identity_updates = {
                    "hiring_identity_evidence": stored_hiring,
                    "hiring_entity_name": stored_hiring.hiring_entity_name,
                    "provider_identity": stored_provider,
                    "discovered_job_board": discovered,
                    "provider": discovered.board.provider,
                }
            attempts.append(
                {
                    "position": position,
                    "provider": board.provider,
                    "board_url": job_list_url,
                    "status": (
                        diagnostic.disposition
                        if route_authorized
                        else "identity_rejected"
                    ),
                    "reason_code": (
                        diagnostic.reason_code
                        if route_authorized
                        else "COMPANY_IDENTITY_AMBIGUOUS"
                    ),
                    "authorized": route_authorized,
                    "routes": _route_attempt_trace(routes),
                    "trace": trace,
                }
            )

        if accepted_exacts:
            exact_identities = set()
            for item in accepted_exacts:
                identity = item["updates"].get("provider_identity")
                assert isinstance(identity, ProviderIdentity)
                exact_identities.add(
                    (
                        canonicalize_identity_url(str(item["opening_url"])),
                        identity.provider,
                        identity.tenant,
                        identity.canonical_board_url,
                    )
                )
            if len(exact_identities) > 1:
                trace = self._portfolio_trace(
                    portfolio,
                    attempts,
                    "ambiguous_verified_exacts",
                )
                return StageExecution(
                    result=make_stage_result(
                        self.name,
                        "partial",
                        reason_code="OPENING_IDENTITY_AMBIGUOUS",
                        duration_ms=_elapsed_ms(started),
                        input_count=len(attempts),
                        evidence=[
                            {
                                "type": "verified_exact_conflict",
                                "candidate_count": len(exact_identities),
                            }
                        ],
                        detail=(
                            "Multiple authorized routes produced different S7-valid "
                            "opening identities."
                        ),
                    ),
                    updates={"job_list_page_url": portfolio.primary.board.url},
                    trace=trace,
                )
            selected_exact = accepted_exacts[0]
            trace = self._portfolio_trace(portfolio, attempts, "verified_exact")
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "success",
                    provider=str(selected_exact["provider"]),
                    duration_ms=_elapsed_ms(started),
                    input_count=len(attempts),
                    output_count=1,
                    evidence=[
                        {
                            "field": "open_position_url",
                            "url": str(selected_exact["opening_url"]),
                        }
                    ],
                ),
                updates=dict(selected_exact["updates"]),
                trace=trace,
            )

        if portfolio.route_evidence:
            eligible_boards = tuple(
                discovered
                for discovered in portfolio.boards
                if any(
                    route.authorized
                    for route in portfolio.routes_for(discovered)
                )
            )
        else:
            eligible_boards = portfolio.boards
        authorized_attempt_count = sum(
            1 for attempt in attempts if attempt.get("authorized") is True
        )
        attempted_all = authorized_attempt_count >= len(eligible_boards)
        portfolio_complete = (
            portfolio.eligible_set_complete or stored_canonical_inventory_complete
        ) and attempted_all
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
        if not eligible_boards and not stored_canonical_inventory_complete:
            reason_code = "COMPANY_IDENTITY_AMBIGUOUS"
            detail = (
                "Provider candidates were discovered, but no route has independent "
                "evidence that its tenant recruits for the target company."
            )
        elif incomplete is not None:
            reason_code, diagnostic = incomplete
            detail = (
                diagnostic.detail
                if diagnostic is not None
                else "A verified job board could not be checked conclusively."
            )
        elif self._stored_career_provider_identity_conflict(
            context,
            portfolio,
            attempts,
        ):
            reason_code = "COMPANY_IDENTITY_AMBIGUOUS"
            detail = (
                "The stored ATS Career lead was revalidated against current complete "
                "provider inventory, but its tenant does not match the verified hiring "
                "entity and no first-party handoff establishes that relationship."
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
            if any(
                _opening_attempt_has_location_rejection(attempt)
                for attempt in attempts
            ):
                detail = (
                    "Every eligible verified job board was checked completely; matching "
                    "titles were present, but none matched the target location."
                )
            else:
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
            updates={
                "job_list_page_url": portfolio.primary.board.url,
                **stored_identity_updates,
            },
            trace=trace,
        )

    def _stored_career_provider_identity_conflict(
        self,
        context: PipelineContext,
        portfolio: JobBoardPortfolio,
        attempts: list[dict],
    ) -> bool:
        if len(attempts) != len(portfolio.boards) or not attempts:
            return False
        if any(
            not _trace_has_complete_native_inventory(attempt.get("trace", {}))
            for attempt in attempts
        ):
            return False
        s5_trace = context.trace.get("stages", {}).get(STAGE_JOB_BOARD_DISCOVERY)
        selected = s5_trace.get("selected") if isinstance(s5_trace, dict) else None
        if (
            not isinstance(selected, dict)
            or selected.get("source_kind") != "stored_verified_career_provider"
        ):
            return False
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url:
            return False
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return False
        if record is None or record.website is None or record.career is None:
            return False
        if (
            _strict_entity_key(record.company_name)
            != _strict_entity_key(context.company.company_name)
            or not _same_url(record.linkedin_company_url, linkedin_url)
            or not _same_url(record.career.website_url, record.website.url)
            or not _same_url(selected.get("url") or "", record.career.url)
        ):
            return False
        board = portfolio.primary.board
        return bool(
            board.identifier
            and not _stored_tenant_matches_hiring_entity(
                context.hiring_entity_name or context.company.company_name,
                board.identifier,
            )
        )
    def _stored_inventory_identity(
        self,
        context: PipelineContext,
        discovered: DiscoveredJobBoard | None,
        trace: dict,
    ) -> tuple[HiringIdentityEvidence, ProviderIdentity] | None:
        store = self.company_discovery_evidence_store
        linkedin_url = context.company.linkedin_company_url
        if store is None or not linkedin_url or discovered is None:
            return None
        s5_trace = context.trace.get("stages", {}).get(STAGE_JOB_BOARD_DISCOVERY)
        selected = s5_trace.get("selected") if isinstance(s5_trace, dict) else None
        if (
            not isinstance(selected, dict)
            or selected.get("source_kind") != "stored_verified_provider_board"
            or not _trace_has_complete_native_inventory(trace)
        ):
            return None
        board = discovered.board
        company_name = context.hiring_entity_name or context.company.company_name
        try:
            record = store.load(context.company.company_name, linkedin_url)
        except (OSError, TypeError, ValueError):
            return None
        if record is None:
            return None
        stored = next(
            (
                item
                for item in record.provider_boards
                if item.provider == board.provider
                and item.tenant == board.identifier
                and _same_url(item.canonical_board_url, board.url)
            ),
            None,
        )
        if stored is None:
            return None
        relationship = _stored_provider_relationship(
            record,
            stored,
            company_name,
            board.identifier,
        )
        if relationship is None:
            return None
        relationship_type, hiring_entity_name = relationship
        prior_hiring = context.hiring_identity_evidence
        if (
            prior_hiring is not None
            and prior_hiring.verified
            and _strict_entity_key(prior_hiring.hiring_entity_name)
            == _strict_entity_key(hiring_entity_name)
        ):
            hiring = prior_hiring
        elif (
            _strict_entity_key(context.company.company_name)
            == _strict_entity_key(hiring_entity_name)
        ):
            hiring = HiringIdentityEvidence(
                source_company_name=context.company.company_name,
                hiring_entity_name=hiring_entity_name,
                relationship_type=relationship_type,
                verification_method="stored_handoff_revalidated_provider_inventory",
                verified=True,
                evidence_url=stored.relationship_evidence_url,
            )
        else:
            return None
        provider = ProviderIdentity(
            hiring_entity_name=hiring.hiring_entity_name,
            provider=board.provider,
            tenant=board.identifier,
            canonical_board_url=board.url,
            evidence_url=stored.relationship_evidence_url,
            verification_method="stored_handoff_revalidated_provider_inventory",
            relationship_verified=True,
        )
        return hiring, provider

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


def _opening_attempt_has_location_rejection(attempt: dict) -> bool:
    trace = attempt.get("trace")
    if not isinstance(trace, dict):
        return False
    if isinstance(trace.get("location_unverified_candidate_rejected"), dict):
        return True
    rejected = trace.get("rejected_candidates")
    if isinstance(rejected, list) and any(
        isinstance(item, dict) and item.get("reason") == "location_identity_mismatch"
        for item in rejected
    ):
        return True
    provider_api = trace.get("provider_api")
    return bool(
        isinstance(provider_api, dict)
        and isinstance(provider_api.get("rejected_candidates"), list)
        and any(
            isinstance(item, dict)
            and item.get("reason") == "location_identity_mismatch"
            for item in provider_api["rejected_candidates"]
        )
    )


def _failed_execution(
    stage: str,
    reason_code: str,
    started: float,
    detail: str,
    trace: dict | None = None,
    evidence: list[dict] | None = None,
) -> StageExecution:
    return StageExecution(
        result=make_stage_result(
            stage,
            "failed",
            reason_code=reason_code,
            duration_ms=_elapsed_ms(started),
            input_count=1,
            evidence=evidence or [],
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


def _trace_has_unlinked_third_party_handoff(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("disposition") == "unlinked_third_party_recruiting_handoff":
            return True
        return any(_trace_has_unlinked_third_party_handoff(item) for item in value.values())
    if isinstance(value, list):
        return any(_trace_has_unlinked_third_party_handoff(item) for item in value)
    return False


def _unlinked_third_party_handoff_evidence(value: object) -> dict:
    if isinstance(value, dict):
        if value.get("disposition") == "unlinked_third_party_recruiting_handoff":
            return {
                "type": "availability_diagnostic",
                **value,
            }
        for item in value.values():
            evidence = _unlinked_third_party_handoff_evidence(item)
            if evidence:
                return evidence
    elif isinstance(value, list):
        for item in value:
            evidence = _unlinked_third_party_handoff_evidence(item)
            if evidence:
                return evidence
    return {}


def _verified_no_public_recruiting_surface(
    context: PipelineContext,
    candidate_trace: object,
) -> dict | None:
    """Close a bounded official-surface miss without treating one 404 as absence."""

    if context.company.external_apply_url or not context.company_website_url:
        return None
    website_stage = next(
        (
            result
            for result in context.stage_results
            if result.stage == "website_resolution"
        ),
        None,
    )
    career_stage = next(
        (
            result
            for result in context.stage_results
            if result.stage == STAGE_CAREER_DISCOVERY
        ),
        None,
    )
    if (
        website_stage is None
        or website_stage.status != "success"
        or career_stage is None
        or career_stage.status != "failed"
        or career_stage.reason_code != "CAREER_PAGE_NOT_FOUND"
        or career_stage.retryable
    ):
        return None
    stages_trace = context.trace.get("stages")
    career_trace = (
        stages_trace.get(STAGE_CAREER_DISCOVERY)
        if isinstance(stages_trace, dict)
        else None
    )
    if not isinstance(career_trace, dict):
        return None
    transport = career_trace.get("transport_budget")
    bundle = career_trace.get("bundle_navigation_discovery")
    sitemap = career_trace.get("sitemap_discovery")
    search = career_trace.get("search_discovery")
    candidate = (
        candidate_trace.get("candidate_discovery")
        if isinstance(candidate_trace, dict)
        else None
    )
    verification = (
        candidate_trace.get("candidate_verification")
        if isinstance(candidate_trace, dict)
        else None
    )
    homepage_url = career_trace.get("homepage_url")
    homepage_identity_verified = bool(
        isinstance(homepage_url, str)
        and homepage_url.rstrip("/").casefold()
        == context.company_website_url.rstrip("/").casefold()
    )
    transport_incomplete = bool(
        transport is not None
        and (
            not isinstance(transport, dict)
            or transport.get("exhausted") is True
            or int(transport.get("rejected") or 0) != 0
            or int(transport.get("dispatched") or 0) < 1
        )
    )
    if (
        career_trace.get("homepage_fetch_error") not in (None, "")
        or not homepage_identity_verified
        or transport_incomplete
        or not isinstance(bundle, dict)
        or bool(bundle.get("candidate_urls"))
        or not isinstance(sitemap, dict)
        or sitemap.get("skipped") is True
        or int(sitemap.get("candidate_count") or 0) != 0
        or not isinstance(sitemap.get("sitemaps_checked"), list)
        or not sitemap["sitemaps_checked"]
        or sitemap.get("fanout_limit_reached") is True
        or not isinstance(search, dict)
        or bool(search.get("candidates"))
        or search.get("stopped_reason") != "no_valid_candidates"
        or not _has_successful_empty_search_query(search.get("queries"))
        or not isinstance(candidate, dict)
        or not isinstance(candidate.get("pool"), dict)
        or int(candidate["pool"].get("candidate_count") or 0) != 0
        or not isinstance(verification, dict)
        or int(verification.get("verified_candidate_count") or 0) != 0
        or _has_unresolved_evidence_candidate(career_trace)
    ):
        return None
    return {
        "type": "availability_diagnostic",
        "disposition": "verified_no_public_recruiting_surface",
        "contract_version": "1",
        "website_url": context.company_website_url,
        "homepage_verified": True,
        "first_party_navigation_complete": True,
        "sitemap_discovery_complete": True,
        "bounded_search_complete": True,
        "provider_candidate_count": 0,
    }


def _has_successful_empty_search_query(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and any(
            isinstance(item, dict)
            and item.get("error") in (None, "")
            and not item.get("candidates")
            and int(item.get("result_count") or 0) >= 0
            for item in value
        )
    )


def _has_unresolved_evidence_candidate(career_trace: dict) -> bool:
    homepage_verification = career_trace.get(
        "homepage_career_surface_verification"
    )
    if (
        isinstance(homepage_verification, dict)
        and homepage_verification.get("verified") is not True
        and "identity mismatch"
        in str(homepage_verification.get("reason") or "").casefold()
    ):
        return True
    for failure in career_trace.get("candidate_fetch_errors") or []:
        if not isinstance(failure, dict) or failure.get("retryable") is not True:
            continue
        if failure.get("origin") not in {"blind_ats_probe", "subdomain_probe"}:
            return True
    return False


def _dynamic_inventory_failure_reason(trace: object) -> str | None:
    """Do not turn a discovered-but-unverified inventory endpoint into absence."""

    if not isinstance(trace, dict):
        return None
    probes = trace.get("content_payload_probes")
    if not isinstance(probes, list):
        return None
    failure_statuses = {
        "empty_inventory_unverified",
        "fetch_failed",
        "incomplete_or_invalid_payload",
        "invalid_inventory_payload",
        "inventory_fetch_failed",
        "inventory_redirect_rejected",
        "redirect_rejected",
        "unverified",
    }
    for probe in probes:
        if (
            not isinstance(probe, dict)
            or probe.get("method")
            not in {
                "first_party_declared_inventory",
                "first_party_dynamic_inventory",
            }
            or not isinstance(probe.get("endpoint_url"), str)
            or not probe["endpoint_url"].strip()
            or probe.get("inventory_complete") is True
            or not _trace_has_status(probe, failure_statuses)
        ):
            continue
        reason_code = _trace_reason_code(probe)
        return reason_code or "OPENING_DISCOVERY_INCOMPLETE"
    return None


def _trace_has_status(value: object, statuses: set[str]) -> bool:
    if isinstance(value, dict):
        if value.get("status") in statuses:
            return True
        return any(_trace_has_status(item, statuses) for item in value.values())
    if isinstance(value, list):
        return any(_trace_has_status(item, statuses) for item in value)
    return False


def _trace_reason_code(value: object) -> str | None:
    if isinstance(value, dict):
        reason_code = value.get("reason_code")
        if isinstance(reason_code, str) and reason_code in {
            "BOT_PROTECTION",
            "CAPTCHA_REQUIRED",
            "COMPANY_TIME_BUDGET_EXHAUSTED",
            "CONNECTION_FAILED",
            "DNS_FAILED",
            "FETCH_BUDGET_EXHAUSTED",
            "FETCH_FAILED",
            "HTTP_FORBIDDEN",
            "HTTP_NOT_FOUND",
            "INVALID_STRUCTURED_DATA",
            "LOGIN_REQUIRED",
            "NETWORK_TIMEOUT",
            "OFFLINE_FIXTURE_MISSING",
            "OFFLINE_TAPE_DIVERGENCE",
            "PARSING_FAILED",
            "PROVIDER_FETCH_FAILED",
            "RATE_LIMITED",
            "SERVER_ERROR",
        }:
            return reason_code
        for item in value.values():
            if (nested := _trace_reason_code(item)) is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            if (nested := _trace_reason_code(item)) is not None:
                return nested
    return None


def _upstream_stage_failed(context: PipelineContext, stage: str) -> bool:
    return any(
        result.stage == stage and result.status in {"failed", "unsupported"}
        for result in context.stage_results
    )


def _revalidated_stored_career_hiring_identity(
    context: PipelineContext,
    *,
    career_url: str,
    stored_career_url: str | None,
    stored_website_url: str | None,
) -> HiringIdentityEvidence | None:
    """Recover same-entity hiring identity only after current S4 revalidation."""

    if (
        context.company_website_url
        or context.hiring_identity_evidence is not None
    ):
        return None
    if stored_career_url and _same_url(career_url, stored_career_url):
        verification_method = "revalidated_stored_career"
    elif (
        stored_website_url
        and domain_of(career_url)
        and domain_of(career_url) == domain_of(stored_website_url)
    ):
        verification_method = "revalidated_stored_website_career"
    else:
        return None
    try:
        evidence_url = canonicalize_identity_url(career_url)
    except (TypeError, ValueError):
        return None
    return HiringIdentityEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=context.company.company_name,
        relationship_type="same_entity",
        verification_method=verification_method,
        verified=True,
        evidence_url=evidence_url,
    )


def _provisional_career_hiring_identity(
    context: PipelineContext,
    *,
    career_url: str,
    discovery_website_url: str,
    trace: dict,
) -> HiringIdentityEvidence | None:
    """Close a provisional official-site chain only with current S4 evidence."""

    provisional = context.provisional_website_evidence
    if (
        provisional is None
        or context.company_website_url
        or context.hiring_identity_evidence is not None
        or provisional.source_company_name != context.company.company_name
        or not _same_url(discovery_website_url, provisional.url)
    ):
        return None

    if _same_public_host(career_url, provisional.url):
        verification_method = "provisional_same_host_career"
    else:
        navigation = context.homepage_navigation_evidence
        selected = trace.get("selected") if isinstance(trace, dict) else None
        selected_url = selected.get("url") if isinstance(selected, dict) else None
        source_url = selected.get("source_url") if isinstance(selected, dict) else None
        if (
            navigation is None
            or not navigation.matches(provisional.url)
            or not isinstance(selected_url, str)
            or selected_url not in navigation.candidate_urls
            or not isinstance(source_url, str)
            or not _same_url(source_url, provisional.url)
        ):
            return None
        verification_method = "provisional_navigation_handoff"

    try:
        evidence_url = canonicalize_identity_url(career_url)
    except (TypeError, ValueError):
        return None
    return HiringIdentityEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=context.company.company_name,
        relationship_type="same_entity",
        verification_method=verification_method,
        verified=True,
        evidence_url=evidence_url,
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


def _trace_has_retryable_failure_for_url(trace: object, url: str) -> bool:
    """Conservatively retain stored evidence when a rejection includes transport failure."""

    try:
        target = canonicalize_identity_url(url)
    except (TypeError, ValueError):
        target = ""

    def walk(value: object) -> bool:
        if isinstance(value, dict):
            if value.get("retryable") is True:
                request = value.get("request_identity")
                request_urls = []
                if isinstance(request, dict):
                    request_urls.extend(
                        item
                        for item in (
                            request.get("url"),
                            request.get("normalized_url"),
                            request.get("requested_url"),
                        )
                        if isinstance(item, str)
                    )
                request_urls.extend(
                    item
                    for key, item in value.items()
                    if key in {"url", "source_url", "requested_url"}
                    and isinstance(item, str)
                )
                if not request_urls or not target:
                    return True
                for request_url in request_urls:
                    try:
                        if canonicalize_identity_url(request_url) == target:
                            return True
                    except (TypeError, ValueError):
                        continue
            return any(walk(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(walk(item) for item in value)
        return False

    return walk(trace)


def _durable_provider_evidence_source(
    context: PipelineContext,
    identity: ProviderIdentity,
    discovered: object = None,
):
    """Map only evidence that can safely survive a run into ADR-0028 storage."""

    if context.company.external_apply_url and _same_url(
        context.company.external_apply_url,
        identity.evidence_url,
    ):
        return "external_apply_handoff"
    if (
        isinstance(discovered, DiscoveredJobBoard)
        and discovered.detection_method
        in {
            "linked_url_evidence",
            "page_evidence",
            "url_evidence",
            "verified_declared_inventory",
            "verified_first_party_action",
        }
        and discovered.relationship_evidence_url
        and context.career_page_url
        and (
            _same_url(discovered.relationship_evidence_url, context.career_page_url)
            or _same_site(
                discovered.relationship_evidence_url,
                context.career_page_url,
            )
        )
    ):
        return "first_party_handoff"
    if identity.verification_method in {
        "identity_career_root",
        "verified_provider_career_page",
        "verified_first_party_provider_page",
        "verified_first_party_handoff",
        "verified_declared_inventory",
        "verified_first_party_action",
    }:
        return "first_party_handoff"
    if identity.verification_method == "provider_inventory":
        return "provider_page_identity"
    return None


def _provider_identity(
    context: PipelineContext,
    job_list_url: str,
    discovered: DiscoveredJobBoard | None,
    registry: ProviderRegistry,
    *,
    candidate: VerifiedProviderCandidate | None = None,
    hiring_evidence: HiringIdentityEvidence | None = None,
    relationship_evidence: HiringRelationshipEvidence | None = None,
    allow_prior_identity: bool = True,
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
        registry,
        discovered=discovered,
    )
    if (
        candidate is not None
        and relationship_evidence is not None
        and not relationship_evidence.verified
    ):
        # Candidate discovery may carry its source URL on the discovered-board
        # object. Only the relationship contract may promote that URL into a
        # first-party handoff; search and guessed routes cannot self-authorize.
        verified, method = False, "linked_url_only"
    if (
        candidate is not None
        and candidate.candidate.source_kind == "guessed_path"
        and method == "tenant_name_match"
    ):
        verified, method = False, "linked_url_only"
    effective_hiring = hiring_evidence or context.hiring_identity_evidence
    if (
        hiring_evidence is not None
        and hiring_evidence.verified
        and hiring_evidence.verification_method == "provider_inventory"
    ):
        verified, method = True, "provider_inventory"
    if not verified and relationship_evidence is not None:
        if candidate is not None:
            verified, method = _authorize_candidate_relationship(
                candidate,
                tenant,
                relationship_evidence,
            )
        else:
            verified = bool(
                relationship_evidence.verified
                and relationship_evidence.provider == provider
                and relationship_evidence.tenant == tenant
            )
            method = (
                relationship_evidence.evidence_type
                if verified
                else "linked_url_only"
            )
        if verified:
            evidence_url = relationship_evidence.evidence_url
    effective_name = (
        effective_hiring.hiring_entity_name
        if effective_hiring is not None
        else context.hiring_entity_name or context.company.company_name
    )
    prior = context.provider_identity
    if (
        allow_prior_identity
        and
        prior is not None
        and prior.relationship_verified
        and prior.provider == provider == "generic"
        and _strict_entity_key(prior.hiring_entity_name)
        == _strict_entity_key(effective_name)
        and _is_transient_generic_board_query_variant(
            prior.canonical_board_url,
            canonical_board,
            verified_first_party_url=context.career_page_url,
        )
    ):
        canonical_board = prior.canonical_board_url
        tenant = prior.tenant
        evidence_url = prior.evidence_url
        verified = True
        method = prior.verification_method
    if (
        allow_prior_identity
        and
        prior is not None
        and prior.relationship_verified
        and prior.provider == provider
        and prior.tenant == tenant
        and _same_url(prior.canonical_board_url, canonical_board)
        and _strict_entity_key(prior.hiring_entity_name)
        == _strict_entity_key(effective_name)
    ):
        verified = True
        method = prior.verification_method
        evidence_url = prior.evidence_url
    return ProviderIdentity(
        hiring_entity_name=effective_name,
        provider=provider,
        tenant=tenant,
        canonical_board_url=canonical_board,
        evidence_url=canonicalize_identity_url(evidence_url),
        verification_method=method,
        relationship_verified=verified,
    )


_TRANSIENT_GENERIC_BOARD_QUERY_KEYS = {
    "k",
    "keywords",
    "keyword",
    "location",
    "offset",
    "page",
    "q",
    "query",
    "search",
    "start",
}


def _is_transient_generic_board_query_variant(
    stable_board_url: str,
    candidate_url: str,
    *,
    verified_first_party_url: str | None = None,
) -> bool:
    stable = urlsplit(canonicalize_identity_url(stable_board_url))
    candidate = urlsplit(canonicalize_identity_url(candidate_url))
    if (
        stable.scheme != candidate.scheme
        or stable.netloc != candidate.netloc
        or stable.path != candidate.path
        or stable.fragment != candidate.fragment
    ):
        return False
    candidate_pairs = parse_qsl(candidate.query, keep_blank_values=True)
    transient_keys = set(_TRANSIENT_GENERIC_BOARD_QUERY_KEYS)
    candidate_keys = {key.casefold() for key, _value in candidate_pairs}
    if (
        verified_first_party_url
        and _same_site(candidate_url, verified_first_party_url)
        and candidate_keys & _TRANSIENT_GENERIC_BOARD_QUERY_KEYS
    ):
        # Some first-party search forms carry an organization filter alongside
        # the title query. The already verified same-site board remains the
        # identity root; the filter is request state, not a new tenant claim.
        transient_keys.add("orgids")
    transient = [
        pair
        for pair in candidate_pairs
        if pair[0].casefold() in transient_keys
    ]
    stable_candidate_pairs = [
        pair
        for pair in candidate_pairs
        if pair[0].casefold() not in transient_keys
    ]
    return bool(
        transient
        and sorted(parse_qsl(stable.query, keep_blank_values=True))
        == sorted(stable_candidate_pairs)
    )


def _authorize_provider_board(
    context: PipelineContext,
    provider: str,
    tenant: str,
    canonical_board: str,
    registry: ProviderRegistry,
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
        return True, "verified_declared_inventory"
    if (
        provider == "generic"
        and discovered is not None
        and discovered.detection_method == "verified_first_party_action"
        and context.career_page_url
        and _same_url(discovered.evidence_url, canonical_board)
        and discovered.relationship_evidence_url is not None
        and _same_url(
            discovered.relationship_evidence_url,
            context.career_page_url,
        )
    ):
        return True, "verified_first_party_handoff"
    if provider == "generic" and context.career_page_url and _same_site(
        context.career_page_url, canonical_board
    ):
        return True, "first_party_same_site"
    if (
        provider != "generic"
        and discovered is not None
        and discovered.detection_method == "url_evidence"
        and context.career_page_url
        and _same_url(discovered.evidence_url, canonical_board)
        and (
            _same_url(context.career_page_url, canonical_board)
            or _provider_url_identifies_board(
                context.career_page_url,
                provider,
                tenant,
                canonical_board,
                registry,
            )
        )
    ):
        return True, "verified_provider_career_page"
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
        and _verified_first_party_career_chain(
            context,
            discovered.relationship_evidence_url,
        )
    ):
        return True, "verified_first_party_handoff"
    return False, "linked_url_only"


def _verified_first_party_career_chain(
    context: PipelineContext,
    relationship_evidence_url: str | None,
) -> bool:
    """Accept only an observed first-party Career-to-provider handoff chain."""

    career_url = context.career_page_url
    if not career_url:
        return False
    if relationship_evidence_url and (
        _same_url(relationship_evidence_url, career_url)
        or _same_site(relationship_evidence_url, career_url)
    ):
        return True

    career_trace = context.trace.get("stages", {}).get(STAGE_CAREER_DISCOVERY)
    selected = career_trace.get("selected") if isinstance(career_trace, dict) else None
    if not isinstance(selected, dict):
        return False
    selected_url = selected.get("url")
    source_url = selected.get("source_url")
    origin = selected.get("origin")
    website_url = context.company_website_url
    return bool(
        isinstance(selected_url, str)
        and _same_url(selected_url, career_url)
        and isinstance(source_url, str)
        and website_url
        and _same_site(source_url, website_url)
        and origin in {"page_link", "verified_homepage_navigation"}
    )


def _provider_url_identifies_board(
    url: str,
    provider: str,
    tenant: str,
    canonical_board: str,
    registry: ProviderRegistry,
) -> bool:
    adapter = registry.adapter_for(url)
    if adapter is None or adapter.name != provider:
        return False
    identified = adapter.identify_board(url)
    if identified is None or identified.provider != provider:
        return False
    canonicalize_board = getattr(adapter, "canonicalize_board", None)
    if callable(canonicalize_board):
        identified = canonicalize_board(identified)
    return bool(
        (identified.identifier or tenant_locator(identified.url)) == tenant
        and _same_url(identified.url, canonical_board)
    )


def _relationship_wave_trace(
    evaluated: tuple[
        tuple[VerifiedProviderCandidate, HiringRelationshipEvidence],
        ...,
    ],
) -> dict:
    return {
        "status": (
            "verified"
            if any(relationship.verified for _, relationship in evaluated)
            else "rejected"
            if evaluated
            else "empty"
        ),
        "candidates": [
            {
                "url": item.candidate.url,
                "source_kind": item.candidate.source_kind,
                "verified": relationship.verified,
                "evidence_type": relationship.evidence_type,
            }
            for item, relationship in evaluated
        ],
    }


def _attach_legacy_route_trace(
    legacy_execution: StageExecution,
    candidate_trace: dict,
    provider_registry: ProviderRegistry,
) -> StageExecution:
    trace = dict(legacy_execution.trace)
    route_trace = _route_trace_with_legacy(
        candidate_trace.get("route_evaluation"),
        legacy_execution,
        provider_registry,
    )
    trace["route_evaluation"] = route_trace
    trace["candidate_route_probe"] = candidate_trace
    return StageExecution(
        result=legacy_execution.result,
        updates=dict(legacy_execution.updates),
        trace=trace,
        evidence_lineage=legacy_execution.evidence_lineage,
    )


def _merge_legacy_website_route(
    context: PipelineContext,
    candidate_execution: StageExecution,
    legacy_execution: StageExecution,
    provider_registry: ProviderRegistry,
) -> StageExecution:
    updates = dict(candidate_execution.updates)
    candidate_primary = updates.get("discovered_job_board")
    candidate_portfolio = updates.get("job_board_portfolio")
    boards = list(
        candidate_portfolio.boards
        if isinstance(candidate_portfolio, JobBoardPortfolio)
        else (candidate_primary,)
        if isinstance(candidate_primary, DiscoveredJobBoard)
        else ()
    )
    legacy_portfolio = legacy_execution.updates.get("job_board_portfolio")
    legacy_boards = list(
        legacy_portfolio.boards
        if isinstance(legacy_portfolio, JobBoardPortfolio)
        else ()
    )
    legacy_board = legacy_execution.updates.get("discovered_job_board")
    if isinstance(legacy_board, DiscoveredJobBoard):
        legacy_boards.insert(0, legacy_board)
    if not legacy_boards:
        legacy_boards.extend(
            _legacy_inventory_provider_boards(
                legacy_execution,
                provider_registry,
            )
        )
    if not legacy_boards:
        legacy_route_board = _legacy_route_board(
            legacy_execution,
            provider_registry,
        )
        if legacy_route_board is not None:
            legacy_boards.append(legacy_route_board)
    for legacy_candidate in legacy_boards:
        if not any(
            item.board.provider == legacy_candidate.board.provider
            and _same_url(item.board.url, legacy_candidate.board.url)
            for item in boards
        ):
            boards.append(legacy_candidate)

    candidate_identity = updates.get("provider_identity")
    legacy_identity = legacy_execution.updates.get("provider_identity")
    candidate_rank = (
        _tenant_entity_match_rank(
            context.hiring_entity_name or context.company.company_name,
            candidate_identity.tenant,
        )
        if isinstance(candidate_identity, ProviderIdentity)
        and candidate_identity.relationship_verified
        else None
    )
    legacy_rank = (
        _tenant_entity_match_rank(
            context.hiring_entity_name or context.company.company_name,
            legacy_identity.tenant,
        )
        if isinstance(legacy_identity, ProviderIdentity)
        and legacy_identity.relationship_verified
        else None
    )
    if isinstance(legacy_identity, ProviderIdentity) and (
        legacy_identity.relationship_verified
        and (
            not isinstance(candidate_identity, ProviderIdentity)
            or not candidate_identity.relationship_verified
            or (
                legacy_rank is not None
                and (candidate_rank is None or legacy_rank < candidate_rank)
            )
        )
    ):
        selected_legacy = next(
            (
                item
                for item in legacy_boards
                if item.board.provider == legacy_identity.provider
                and _same_url(
                    item.board.url,
                    legacy_identity.canonical_board_url,
                )
            ),
            legacy_boards[0] if legacy_boards else None,
        )
        if selected_legacy is not None:
            selected_identity = legacy_identity
            if (
                selected_legacy.board.provider != legacy_identity.provider
                or not _same_url(
                    selected_legacy.board.url,
                    legacy_identity.canonical_board_url,
                )
            ):
                derived_identity = _provider_identity(
                    context,
                    selected_legacy.board.url,
                    selected_legacy,
                    provider_registry,
                )
                if not derived_identity.relationship_verified:
                    selected_legacy = None
                else:
                    selected_identity = derived_identity
        if selected_legacy is not None:
            boards = [
                selected_legacy,
                *(item for item in boards if item is not selected_legacy),
            ]
            updates.update(
                {
                    "job_list_page_url": selected_legacy.board.url,
                    "provider": selected_legacy.board.provider,
                    "discovered_job_board": selected_legacy,
                    "provider_identity": selected_identity,
                }
            )
    boards = _deduplicate_public_board_identities(boards)
    route_evidence = list(
        candidate_portfolio.route_evidence
        if isinstance(candidate_portfolio, JobBoardPortfolio)
        else ()
    )
    route_evidence.extend(
        _legacy_route_evidence(
            context,
            legacy_execution,
            tuple(legacy_boards),
        )
    )
    route_evidence = _deduplicate_route_evidence(route_evidence)
    boards = _order_boards_by_route_authority(boards, route_evidence)
    portfolio = None
    if boards:
        portfolio = JobBoardPortfolio(
            boards=tuple(boards[:8]),
            eligible_set_complete=_merged_portfolio_is_complete(
                boards,
                route_evidence,
                (candidate_portfolio, legacy_portfolio),
                limit=8,
            ),
            route_evidence=tuple(
                route
                for route in route_evidence
                if any(
                    route.provider == board.board.provider
                    and _same_url(route.canonical_board_url, board.board.url)
                    for board in boards[:8]
                )
            ),
        )

    trace = dict(candidate_execution.trace)
    if portfolio is not None:
        _project_job_board_portfolio(trace, updates, portfolio)
        primary_routes = portfolio.routes_for(portfolio.primary)
        relationship = _best_route_relationship(primary_routes)
        hiring = (
            _candidate_hiring_evidence(context, relationship)
            if relationship is not None
            else context.hiring_identity_evidence
        )
        updates["provider_identity"] = _provider_identity(
            context,
            portfolio.primary.board.url,
            portfolio.primary,
            provider_registry,
            hiring_evidence=hiring,
            relationship_evidence=relationship,
            allow_prior_identity=False,
        )
        if hiring is not None and hiring != context.hiring_identity_evidence:
            updates["hiring_identity_evidence"] = hiring
            updates["hiring_entity_name"] = hiring.hiring_entity_name
    trace["route_evaluation"] = _route_trace_with_legacy(
        trace.get("route_evaluation"),
        legacy_execution,
        provider_registry,
    )
    trace["legacy_website_probe"] = {
        "status": legacy_execution.result.status,
        "reason_code": legacy_execution.result.reason_code,
        "trace": legacy_execution.trace,
    }
    result = candidate_execution.result
    if portfolio is not None:
        primary_routes = portfolio.routes_for(portfolio.primary)
        if (
            result.status != "success"
            and legacy_execution.result.status == "success"
            and any(
                route.authorized and route.route_kind == "website_career"
                for route in primary_routes
            )
        ):
            result = legacy_execution.result
        result = _project_job_board_stage_result(result, portfolio.primary)
    return StageExecution(
        result=result,
        updates=updates,
        trace=trace,
        evidence_lineage=candidate_execution.evidence_lineage,
    )


def _merged_portfolio_is_complete(
    boards: list[DiscoveredJobBoard],
    route_evidence: list[JobBoardRouteEvidence],
    source_portfolios: tuple[JobBoardPortfolio | None, ...],
    *,
    limit: int,
) -> bool:
    if not boards or len(boards) > limit:
        return False

    eligible = [
        board
        for board in boards
        if not route_evidence
        or any(
            route.authorized
            and route.provider == board.board.provider
            and _same_url(route.canonical_board_url, board.board.url)
            for route in route_evidence
        )
    ]
    if not eligible:
        return False

    complete_sources = tuple(
        portfolio
        for portfolio in source_portfolios
        if isinstance(portfolio, JobBoardPortfolio)
        and portfolio.eligible_set_complete
    )
    return bool(complete_sources) and all(
        any(
            source_board.board.provider == board.board.provider
            and _same_url(source_board.board.url, board.board.url)
            for portfolio in complete_sources
            for source_board in portfolio.boards
        )
        for board in eligible
    )


def _route_trace_with_legacy(
    route_trace: dict | None,
    legacy_execution: StageExecution,
    provider_registry: ProviderRegistry,
) -> dict:
    value = dict(route_trace or {"schema_version": "1.0", "mode": "exhaustive"})
    routes = {
        key: dict(payload)
        for key, payload in (value.get("routes") or {}).items()
        if isinstance(payload, dict)
    }
    website = dict(
        routes.get(
            "website_career",
            {
                "input_available": False,
                "candidate_count": 0,
                "provider_verified_count": 0,
                "relationship_verified_count": 0,
                "verified_relationship_boards": [],
            },
        )
    )
    legacy_board = legacy_execution.updates.get("discovered_job_board")
    if not isinstance(legacy_board, DiscoveredJobBoard):
        legacy_board = _legacy_inventory_provider_board(
            legacy_execution,
            provider_registry,
        )
    legacy_identity = legacy_execution.updates.get("provider_identity")
    legacy_verified = bool(
        isinstance(legacy_identity, ProviderIdentity)
        and legacy_identity.relationship_verified
    )
    website["input_available"] = bool(
        website.get("input_available") or legacy_execution.result.status != "not_run"
    )
    website["legacy_status"] = legacy_execution.result.status
    website["legacy_reason_code"] = legacy_execution.result.reason_code
    if isinstance(legacy_board, DiscoveredJobBoard) or isinstance(
        legacy_identity, ProviderIdentity
    ):
        website["provider_verified_count"] = max(
            int(website.get("provider_verified_count", 0)),
            1,
        )
    if legacy_verified:
        boards = list(website.get("verified_relationship_boards") or [])
        board_url = (
            legacy_board.board.url
            if isinstance(legacy_board, DiscoveredJobBoard)
            else legacy_identity.canonical_board_url
        )
        board_provider = (
            legacy_board.board.provider
            if isinstance(legacy_board, DiscoveredJobBoard)
            else legacy_identity.provider
        )
        board_tenant = (
            legacy_identity.tenant
            if not isinstance(legacy_board, DiscoveredJobBoard)
            or legacy_identity.provider == legacy_board.board.provider
            else legacy_board.board.identifier
        )
        if not any(
            isinstance(item, dict)
            and item.get("provider") == board_provider
            and _same_url(str(item.get("url") or ""), board_url)
            for item in boards
        ):
            boards.append(
                {
                    "url": board_url,
                    "provider": board_provider,
                    "tenant": board_tenant,
                    "candidate_url": (
                        legacy_board.evidence_url
                        if isinstance(legacy_board, DiscoveredJobBoard)
                        else legacy_identity.evidence_url
                    ),
                    "source_kind": "legacy_website_career",
                    "relationship_evidence_type": (
                        legacy_identity.verification_method
                        if not isinstance(legacy_board, DiscoveredJobBoard)
                        or legacy_identity.provider == legacy_board.board.provider
                        else "first_party_inventory_handoff"
                    ),
                }
            )
        website["verified_relationship_boards"] = boards
        website["relationship_verified_count"] = len(boards)
    routes["website_career"] = website
    value["routes"] = routes
    return value


def _legacy_inventory_provider_boards(
    legacy_execution: StageExecution,
    provider_registry: ProviderRegistry,
) -> tuple[DiscoveredJobBoard, ...]:
    if (
        legacy_execution.result.status != "success"
    ):
        return ()
    return _first_party_inventory_provider_boards(
        legacy_execution.trace,
        provider_registry,
    )


def _first_party_inventory_provider_boards(
    trace: dict,
    provider_registry: ProviderRegistry,
) -> tuple[DiscoveredJobBoard, ...]:
    inventory = trace.get("first_party_listing_inventory")
    if (
        not isinstance(inventory, dict)
        or inventory.get("status") != "verified"
        or not isinstance(inventory.get("candidates"), list)
    ):
        return ()
    discovered: list[tuple[JobBoard, str, str | None]] = []
    for candidate in inventory["candidates"]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("url"), str):
            continue
        adapter = provider_registry.adapter_for(candidate["url"])
        board = adapter.identify_board(candidate["url"]) if adapter is not None else None
        if adapter is None or board is None or not adapter.supports_listing:
            continue
        canonicalize_board = getattr(adapter, "canonicalize_board", None)
        if callable(canonicalize_board):
            board = canonicalize_board(board)
        source_url = candidate.get("source_url")
        discovered.append(
            (
                board,
                candidate["url"],
                source_url if isinstance(source_url, str) else None,
            )
        )
    unique: dict[tuple[str, str], DiscoveredJobBoard] = {}
    for board, candidate_url, source_url in discovered:
        identity = (board.provider, canonicalize_identity_url(board.url))
        relationship_url = source_url or candidate_url
        unique.setdefault(
            identity,
            DiscoveredJobBoard(
                board=board,
                detection_method="linked_url_evidence",
                evidence_url=board.url,
                relationship_evidence_url=(
                    relationship_url
                    if urlsplit(relationship_url).netloc.casefold()
                    != urlsplit(board.url).netloc.casefold()
                    else None
                ),
            ),
        )
    return tuple(unique.values())


def _legacy_inventory_provider_board(
    legacy_execution: StageExecution,
    provider_registry: ProviderRegistry,
) -> DiscoveredJobBoard | None:
    return next(
        iter(
            _legacy_inventory_provider_boards(
                legacy_execution,
                provider_registry,
            )
        ),
        None,
    )


def _legacy_route_board(
    legacy_execution: StageExecution,
    provider_registry: ProviderRegistry,
) -> DiscoveredJobBoard | None:
    if legacy_execution.result.status != "success":
        return None
    identity = legacy_execution.updates.get("provider_identity")
    job_list_url = legacy_execution.updates.get("job_list_page_url")
    if (
        not isinstance(identity, ProviderIdentity)
        or not isinstance(job_list_url, str)
        or not _same_url(job_list_url, identity.canonical_board_url)
    ):
        return None
    if identity.provider == "generic":
        if not identity.relationship_verified:
            return None
        board = JobBoard(
            url=identity.canonical_board_url,
            provider="generic",
            identifier=identity.tenant,
            replay_safe=False,
        )
        detection_method = "verified_first_party_action"
    else:
        adapter = provider_registry.adapter_for(identity.canonical_board_url)
        board = (
            adapter.identify_board(identity.canonical_board_url)
            if adapter is not None
            else None
        )
        if board is None or board.provider != identity.provider:
            return None
        canonicalize_board = getattr(adapter, "canonicalize_board", None)
        if callable(canonicalize_board):
            board = canonicalize_board(board)
        detection_method = "url_evidence"
    return DiscoveredJobBoard(
        board=board,
        detection_method=detection_method,
        evidence_url=board.url,
        relationship_evidence_url=(
            identity.evidence_url
            if not _same_url(identity.evidence_url, identity.canonical_board_url)
            else None
        ),
    )


def _legacy_route_evidence(
    context: PipelineContext,
    legacy_execution: StageExecution,
    boards: tuple[DiscoveredJobBoard, ...],
) -> tuple[JobBoardRouteEvidence, ...]:
    identity = legacy_execution.updates.get("provider_identity")
    hiring = legacy_execution.updates.get("hiring_identity_evidence")
    if not isinstance(hiring, HiringIdentityEvidence):
        hiring = context.hiring_identity_evidence
    routes: list[JobBoardRouteEvidence] = []
    for discovered in boards:
        board = discovered.board
        relationship: HiringRelationshipEvidence | None = None
        matching_identity = bool(
            isinstance(identity, ProviderIdentity)
            and identity.relationship_verified
            and identity.provider == board.provider
            and identity.tenant == (board.identifier or tenant_locator(board.url))
            and _same_url(identity.canonical_board_url, board.url)
        )
        relationship_url = (
            identity.evidence_url
            if matching_identity
            else discovered.relationship_evidence_url
        )
        first_party_bound = bool(
            relationship_url
            and context.career_page_url
            and (
                _same_url(relationship_url, context.career_page_url)
                or _same_site(relationship_url, context.career_page_url)
            )
        )
        if (
            isinstance(hiring, HiringIdentityEvidence)
            and hiring.verified
            and isinstance(relationship_url, str)
            and (matching_identity or first_party_bound)
        ):
            relationship = HiringRelationshipEvidence(
                source_company_name=context.company.company_name,
                hiring_entity_name=hiring.hiring_entity_name,
                provider=board.provider,
                tenant=board.identifier or tenant_locator(board.url),
                evidence_type=(
                    "first_party_inventory"
                    if board.provider == "generic"
                    else "first_party_handoff"
                ),
                evidence_url=canonicalize_identity_url(relationship_url),
                strength=100 if matching_identity else 90,
                verified=True,
            )
        routes.append(
            JobBoardRouteEvidence(
                provider=board.provider,
                canonical_board_url=board.url,
                route_kind="website_career",
                source_kind=(
                    "first_party_inventory"
                    if board.provider == "generic"
                    else "first_party_ats_link"
                ),
                hiring_relationship=relationship,
            )
        )
    return tuple(routes)


def _deduplicate_route_evidence(
    routes: list[JobBoardRouteEvidence],
) -> list[JobBoardRouteEvidence]:
    unique: dict[tuple[str, str, str, str, str], JobBoardRouteEvidence] = {}
    for route in routes:
        relationship = route.hiring_relationship
        identity = (
            route.provider,
            canonicalize_identity_url(route.canonical_board_url),
            route.route_kind,
            route.source_kind,
            relationship.evidence_url if relationship is not None else "",
        )
        unique.setdefault(identity, route)
    return list(unique.values())


def _order_boards_by_route_authority(
    boards: list[DiscoveredJobBoard],
    routes: list[JobBoardRouteEvidence],
) -> list[DiscoveredJobBoard]:
    route_priority = {
        "external_apply": 0,
        "website_career": 1,
        "provider_search": 2,
    }

    def rank(discovered: DiscoveredJobBoard) -> tuple[int, int, int, str]:
        matching = [
            route
            for route in routes
            if route.provider == discovered.board.provider
            and _same_url(route.canonical_board_url, discovered.board.url)
        ]
        if not matching:
            return (1, 0, 3, discovered.board.url.casefold())
        best = min(
            matching,
            key=lambda route: (
                not route.authorized,
                -(
                    route.hiring_relationship.strength
                    if route.hiring_relationship is not None
                    else 0
                ),
                route_priority[route.route_kind],
            ),
        )
        strength = (
            best.hiring_relationship.strength
            if best.hiring_relationship is not None
            else 0
        )
        return (
            0 if best.authorized else 1,
            -strength,
            route_priority[best.route_kind],
            discovered.board.url.casefold(),
        )

    return sorted(boards, key=rank)


def _best_route_relationship(
    routes: tuple[JobBoardRouteEvidence, ...],
) -> HiringRelationshipEvidence | None:
    relationships = [
        route.hiring_relationship
        for route in routes
        if route.hiring_relationship is not None
    ]
    if not relationships:
        return None
    return min(
        relationships,
        key=lambda relationship: (
            not relationship.verified,
            -relationship.strength,
            relationship.evidence_url.casefold(),
        ),
    )


def _route_is_authorized(
    context: PipelineContext,
    portfolio: JobBoardPortfolio,
    discovered: DiscoveredJobBoard,
    relationship: HiringRelationshipEvidence | None,
) -> bool:
    if relationship is not None and relationship.verified:
        return True
    if not portfolio.route_evidence:
        return True
    return False


def _route_attempt_trace(
    routes: tuple[JobBoardRouteEvidence, ...],
) -> list[dict]:
    return [
        {
            "route_kind": route.route_kind,
            "source_kind": route.source_kind,
            "authorized": route.authorized,
            "relationship_evidence_type": (
                route.hiring_relationship.evidence_type
                if route.hiring_relationship is not None
                else None
            ),
        }
        for route in routes
    ]


def _candidate_route_trace(
    context: PipelineContext,
    direct_trace: dict,
    search_trace: dict,
    direct_built,
    search_built,
    direct_evaluated: tuple[
        tuple[VerifiedProviderCandidate, HiringRelationshipEvidence], ...
    ],
    search_evaluated: tuple[
        tuple[VerifiedProviderCandidate, HiringRelationshipEvidence], ...
    ],
) -> dict:
    """Publish benchmark attribution without turning discovery into success evidence."""

    direct_source_counts = {
        str((source.get("trace") or {}).get("source")): int(
            source.get("candidate_count", 0)
        )
        for source in direct_trace.get("sources", [])
        if isinstance(source, dict) and source.get("status") == "success"
    }
    search_source_counts = {
        str((source.get("trace") or {}).get("source")): int(
            source.get("candidate_count", 0)
        )
        for source in search_trace.get("sources", [])
        if isinstance(source, dict) and source.get("status") == "success"
    }

    direct_verified = tuple(getattr(direct_built, "verified", ()))
    search_verified = tuple(
        getattr(search_built, "verified", ()) if search_built is not None else ()
    )
    direct_relationships = {
        item.candidate.url: relationship
        for item, relationship in direct_evaluated
    }
    search_relationships = {
        item.candidate.url: relationship
        for item, relationship in search_evaluated
    }

    def route_payload(
        *,
        input_available: bool,
        candidate_count: int,
        verified_candidates: tuple[VerifiedProviderCandidate, ...],
        relationships: dict[str, HiringRelationshipEvidence],
        source_kinds: set[str],
    ) -> dict:
        matching = tuple(
            item
            for item in verified_candidates
            if item.candidate.source_kind in source_kinds
        )
        boards = []
        for item in matching:
            relationship = relationships.get(item.candidate.url)
            if relationship is None or not relationship.verified:
                continue
            board = item.discovered_board.board
            boards.append(
                {
                    "url": board.url,
                    "provider": board.provider,
                    "tenant": relationship.tenant,
                    "candidate_url": item.candidate.url,
                    "source_kind": item.candidate.source_kind,
                    "relationship_evidence_type": relationship.evidence_type,
                }
            )
        return {
            "input_available": input_available,
            "candidate_count": candidate_count,
            "provider_verified_count": len(matching),
            "relationship_verified_count": len(boards),
            "verified_relationship_boards": boards,
        }

    return {
        "schema_version": "1.0",
        "mode": "exhaustive",
        "routes": {
            "external_apply": route_payload(
                input_available=bool(context.company.external_apply_url),
                candidate_count=direct_source_counts.get("external_apply", 0),
                verified_candidates=direct_verified,
                relationships=direct_relationships,
                source_kinds={"external_apply"},
            ),
            "website_career": route_payload(
                input_available=bool(
                    context.company_website_url
                    or context.career_page_url
                    or context.provisional_website_evidence
                ),
                candidate_count=direct_source_counts.get("website_career", 0),
                verified_candidates=direct_verified,
                relationships=direct_relationships,
                source_kinds={"first_party_ats_link"},
            ),
            "provider_search": route_payload(
                input_available=True,
                candidate_count=search_source_counts.get(
                    "provider_targeted_search", 0
                ),
                verified_candidates=search_verified,
                relationships=search_relationships,
                source_kinds={
                    "targeted_opening_search",
                    "targeted_board_search",
                    "verified_tenant_probe",
                    "guessed_path",
                },
            ),
        },
    }


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
        and _candidate_has_first_party_provenance(context, candidate)
    ):
        evidence_type = "first_party_handoff"
        strength = 85
    elif (
        candidate.provider_employer_evidence is not None
        and provider_employer_matches_company(
            company_name,
            candidate.provider_employer_evidence,
        )
        and _provider_employer_evidence_matches_board(
            selected,
            candidate.provider_employer_evidence.opening_url,
        )
    ):
        evidence_type = "provider_published_employer"
        evidence_url = candidate.provider_employer_evidence.evidence_url
        strength = 96
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


def _coordinator_source_input(context: PipelineContext) -> CandidateDiscoveryInput:
    source_trace = context.company.source_trace
    posting = (
        source_trace.get("linkedin_posting")
        if isinstance(source_trace, dict)
        and isinstance(source_trace.get("linkedin_posting"), dict)
        else {}
    )
    job_id = _coordinator_linkedin_job_id(context.company.linkedin_job_url)
    job_url = (
        f"https://www.linkedin.com/jobs/view/{job_id}"
        if job_id is not None
        else None
    )
    company_url = _optional_coordinator_linkedin_url(
        context.company.linkedin_company_url,
        "LinkedIn company URL",
    )
    external_apply_url = context.company.external_apply_url
    apply_mode = str(posting.get("apply_mode") or "unknown").casefold()
    if external_apply_url:
        observation = ExternalApplyObservation.OBSERVED
    elif apply_mode == "native":
        observation = ExternalApplyObservation.OBSERVED_ABSENT
    else:
        observation = ExternalApplyObservation.NOT_OBSERVED
    provenance = _coordinator_identifier(
        posting.get("evidence_source"),
        fallback="normalized_input",
    )
    return CandidateDiscoveryInput(
        source_company_name=context.company.company_name,
        target_title=context.company.job_title,
        target_location=context.company.job_location,
        linkedin_job_url=job_url,
        linkedin_job_id=job_id,
        linkedin_company_url=company_url,
        source=_coordinator_identifier(context.company.source, fallback="input"),
        source_evidence_provenance=provenance,
        external_apply_observation=observation,
        external_apply_url=external_apply_url if observation is ExternalApplyObservation.OBSERVED else None,
    )


def _coordinator_website_input(
    context: PipelineContext,
) -> WebsiteCareerRouteInput | None:
    provisional = context.provisional_website_evidence
    if (
        provisional is not None
        and provisional.source_company_name != context.company.company_name
    ):
        provisional = None
    website = (
        context.company_website_url
        or (provisional.url if provisional is not None else None)
    )
    career = context.career_page_url
    if website is None and career is None:
        return None
    evidence_urls = tuple(dict.fromkeys(url for url in (website, career) if url))
    return WebsiteCareerRouteInput(
        company_website_url=website,
        career_page_url=career,
        evidence_scope=(
            "verified_website_career"
            if context.company_website_url
            else "provisional_official_website"
        ),
        evidence_urls=evidence_urls,
    )


def _coordinator_website_suppression(
    context: PipelineContext,
    website_input: WebsiteCareerRouteInput | None,
) -> WebsiteCareerRouteSuppression | None:
    if (
        website_input is None
        or not _upstream_stage_failed(context, STAGE_HIRING_IDENTITY_RESOLUTION)
        or website_input.company_website_url is None
    ):
        return None
    result = next(
        (
            item
            for item in context.stage_results
            if item.stage == STAGE_HIRING_IDENTITY_RESOLUTION
        ),
        None,
    )
    return WebsiteCareerRouteSuppression(
        rejected_url=website_input.company_website_url,
        evidence_scope=website_input.evidence_scope,
        reason_code=_coordinator_identifier(
            result.reason_code if result is not None else None,
            fallback="identity_rejected",
        ),
    )


def _coordinator_linkedin_job_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or (host != "linkedin.com" and not host.endswith(".linkedin.com"))
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    identifiers = re.findall(r"(?:^|[-/])([1-9][0-9]{2,24})(?:/|$)", parsed.path)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in {"currentjobid", "jobid"} and re.fullmatch(r"[1-9][0-9]{2,24}", value):
            identifiers.append(value)
    return identifiers[-1] if identifiers else None


def _optional_coordinator_linkedin_url(
    value: str | None,
    label: str,
) -> str | None:
    if not value:
        return None
    try:
        return canonicalize_linkedin_evidence_url(value, label)
    except (TypeError, ValueError):
        return None


def _coordinator_identifier(value: object, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").casefold()).strip("_")
    if not normalized or not normalized[0].isalpha():
        return fallback
    return normalized[:64]


def _coordinator_route_trace(result) -> dict:
    return {
        "status": result.status.value,
        "candidate_count": len(result.candidates),
        "producer": result.provenance.producer,
        "request_count": result.provenance.request_count,
        "query_count": result.provenance.query_count,
        "truncated": result.provenance.truncated,
        "diagnostics": dict(result.provenance.trace),
        **(
            {
                "failure": {
                    "reason_code": result.failure.reason_code,
                    "retryable": result.failure.retryable,
                    "error_type": result.failure.error_type,
                }
            }
            if result.failure is not None
            else {}
        ),
        **(
            {
                "suppression": {
                    "reason_code": result.suppression.reason_code,
                    "evidence_scope": result.suppression.evidence_scope,
                    "rejected_url": result.suppression.rejected_url,
                }
            }
            if result.suppression is not None
            else {}
        ),
    }


def _candidate_route_evidence(
    selected: VerifiedProviderCandidate,
    relationship: HiringRelationshipEvidence | None,
    *,
    route_kind: str | None = None,
) -> JobBoardRouteEvidence:
    source_kind = selected.candidate.source_kind
    if route_kind is None:
        if source_kind == "external_apply":
            route_kind = "external_apply"
        elif source_kind == "first_party_ats_link":
            route_kind = "website_career"
        else:
            route_kind = "provider_search"
    board = selected.discovered_board.board
    return JobBoardRouteEvidence(
        provider=board.provider,
        canonical_board_url=board.url,
        route_kind=route_kind,
        source_kind=source_kind,
        hiring_relationship=relationship,
    )


def _provider_employer_evidence_matches_board(
    selected: VerifiedProviderCandidate,
    opening_url: str,
) -> bool:
    board = selected.discovered_board.board
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_for(opening_url)
    if selected.discovered_board.detection_method == "provider_opening_bootstrap":
        evidence = selected.candidate.provider_employer_evidence
        return bool(
            evidence is not None
            and adapter is not None
            and adapter.name == board.provider
            and _same_url(opening_url, evidence.opening_url)
            and _same_url(selected.candidate.url, evidence.evidence_url)
        )
    opening_board = adapter.identify_board(opening_url) if adapter is not None else None
    if opening_board is None:
        return False
    canonicalize_board = getattr(adapter, "canonicalize_board", None)
    if callable(canonicalize_board):
        opening_board = canonicalize_board(opening_board)
    return (
        opening_board.provider == board.provider
        and opening_board.identifier == board.identifier
        and opening_board.url.rstrip("/").casefold()
        == board.url.rstrip("/").casefold()
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


def _candidate_has_first_party_provenance(
    context: PipelineContext,
    candidate: ProviderCandidate,
) -> bool:
    provisional_url = (
        context.provisional_website_evidence.url
        if context.provisional_website_evidence is not None
        and context.provisional_website_evidence.source_company_name
        == context.company.company_name
        else None
    )
    return any(
        first_party_url and _same_url(candidate.source_url, first_party_url)
        for first_party_url in (
            context.career_page_url,
            context.company_website_url,
            provisional_url,
        )
    )


def _tenant_matches_hiring_entity(hiring_entity_name: str, tenant: str) -> bool:
    """Match stable entity aliases without treating a shared brand token as identity."""

    return _tenant_entity_match_rank(hiring_entity_name, tenant) is not None


def _stored_tenant_matches_hiring_entity(
    hiring_entity_name: str,
    tenant: str,
) -> bool:
    """Allow a verified stored handoff to retain a parent/brand tenant segment."""

    if _tenant_matches_hiring_entity(hiring_entity_name, tenant):
        return True
    compact = _strict_entity_key(hiring_entity_name)
    if not compact:
        return False
    tenant_parts = [
        _strict_entity_key(part)
        for part in re.split(r"[/|:]", tenant)
        if _strict_entity_key(part)
    ]
    return len(tenant_parts) >= 2 and compact in tenant_parts


def _stored_provider_relationship(
    record: VerifiedCompanyDiscoveryEvidence,
    stored: VerifiedProviderBoardEvidence,
    company_name: str,
    tenant: str,
) -> tuple[str, str] | None:
    if stored.source == "first_party_handoff":
        if (
            record.website is None
            or record.career is None
            or not _same_url(record.career.website_url, record.website.url)
            or not _same_url(stored.relationship_evidence_url, record.career.url)
        ):
            return None
        # The observed first-party handoff authorizes an opaque provider tenant
        # without projecting that locator into the hiring-entity name.
        return "same_entity", company_name
    if (
        stored.source == "external_apply_handoff"
        and stored.verification_method == "linkedin_external_apply"
        and _stored_tenant_matches_hiring_entity(company_name, tenant)
    ):
        return "same_entity", company_name
    if (
        stored.source == "provider_page_identity"
        and stored.verification_method == "provider_inventory"
        and _stored_tenant_matches_hiring_entity(company_name, tenant)
    ):
        return "same_entity", company_name
    return None


def _tenant_entity_match_rank(
    hiring_entity_name: str,
    tenant: str,
) -> int | None:
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
    entity_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", hiring_entity_name.casefold())
        if token not in ignored
    ]
    tenant_key = _strict_entity_key(tenant)
    if not entity_tokens or not tenant_key:
        return None
    compact = "".join(entity_tokens)
    acronym = "".join(token[0] for token in entity_tokens)
    tenant_suffixes = (
        "careers",
        "co",
        "company",
        "corp",
        "corporation",
        "global",
        "group",
        "holdings",
        "i",
        "inc",
        "jobs",
        "limited",
        "llc",
        "ltd",
        "plc",
    )
    if tenant_key == compact:
        return 0
    if re.fullmatch(re.escape(compact) + r"[0-9]{1,3}", tenant_key):
        return 1
    tenant_parts = [
        _strict_entity_key(part)
        for part in re.split(r"[/|:]", tenant)
        if _strict_entity_key(part)
    ]
    if len(tenant_parts) >= 2 and all(part == compact for part in tenant_parts):
        return 1
    if any(tenant_key == f"{compact}{suffix}" for suffix in tenant_suffixes):
        return 1
    if len(acronym) >= 2 and tenant_key == acronym:
        return 2
    if len(acronym) >= 2 and any(
        tenant_key == f"{acronym}{suffix}" for suffix in tenant_suffixes
    ):
        return 3
    return None


def _trace_has_complete_native_inventory(trace: object) -> bool:
    if not isinstance(trace, dict):
        return False
    provider_api = trace.get("provider_api")
    if not isinstance(provider_api, dict):
        return False
    inventory = provider_api.get("inventory")
    adapter_trace = provider_api.get("adapter_trace")
    return bool(
        isinstance(inventory, dict)
        and inventory.get("source") == "native_adapter"
        and inventory.get("complete") is True
        and inventory.get("status")
        in {"verified", "verified_filtered_empty", "verified_empty"}
        and isinstance(adapter_trace, dict)
        and adapter_trace.get("tenant_identity_conflict") is not True
        and not adapter_trace.get("errors")
    )


def _project_job_board_portfolio(
    trace: dict,
    updates: dict[str, object],
    portfolio: JobBoardPortfolio,
) -> tuple[str, str]:
    """Project the final typed S5 portfolio into every public stage surface."""

    primary = portfolio.primary
    board = primary.board
    conflicts = _job_board_projection_conflicts(trace, board.url, board.provider)
    previous_projection = trace.get("job_board_portfolio_projection")
    if isinstance(previous_projection, dict):
        previous_fields = previous_projection.get("fields")
        if isinstance(previous_fields, list):
            conflicts = [
                *(
                    field
                    for field in previous_fields
                    if isinstance(field, str)
                ),
                *conflicts,
            ]
    conflicts = list(dict.fromkeys(conflicts))

    durable_portfolio = portfolio.replay_safe_projection()
    checkpoint_payload = (
        durable_portfolio.to_checkpoint_payload()
        if durable_portfolio is not None
        else None
    )
    detection = trace.get("provider_detection")
    detection_matches_primary = (
        isinstance(detection, dict)
        and detection.get("provider") == board.provider
        and isinstance(detection.get("url"), str)
        and _same_url(detection["url"], board.url)
    )
    summary = {
        "eligible_count": len(portfolio.boards),
        "eligible_set_complete": portfolio.eligible_set_complete,
        "primary_provider": board.provider,
        "primary_url": board.url,
    }
    if checkpoint_payload is not None:
        summary["checkpoint_payload"] = checkpoint_payload

    trace["job_list_page_url"] = board.url
    trace["provider"] = board.provider
    trace["provider_detection"] = {
        "method": (
            detection.get("method")
            if detection_matches_primary
            else primary.detection_method
        ),
        "provider": board.provider,
        "url": board.url,
        "evidence_url": (
            detection.get("evidence_url")
            if detection_matches_primary and detection.get("evidence_url")
            else primary.evidence_url
        ),
    }
    trace["job_board_portfolio"] = summary
    if conflicts:
        trace["job_board_portfolio_projection"] = {
            "status": "superseded_conflict",
            "fields": conflicts,
            "resolution": "final_typed_portfolio",
        }
    else:
        trace.pop("job_board_portfolio_projection", None)

    updates.update(
        {
            "job_list_page_url": board.url,
            "provider": board.provider,
            "discovered_job_board": primary,
        }
    )
    if (
        len(portfolio.boards) > 1
        or not portfolio.eligible_set_complete
        or portfolio.route_evidence
    ):
        updates["job_board_portfolio"] = portfolio
    else:
        updates.pop("job_board_portfolio", None)
    return board.url, board.provider


def _project_job_board_stage_result(
    result: StageResult,
    primary: DiscoveredJobBoard,
) -> StageResult:
    evidence = [
        (
            {**item, "url": primary.board.url}
            if item.get("field") == "job_list_page_url"
            else item
        )
        for item in result.evidence
    ]
    return replace(
        result,
        provider=primary.board.provider,
        evidence=evidence,
    )


def _job_board_projection_conflicts(
    trace: dict,
    primary_url: str,
    primary_provider: str,
) -> list[str]:
    expected = {
        "job_list_page_url": primary_url,
        "provider": primary_provider,
        "provider_detection.url": primary_url,
        "provider_detection.provider": primary_provider,
        "job_board_portfolio.primary_url": primary_url,
        "job_board_portfolio.primary_provider": primary_provider,
    }
    detection = trace.get("provider_detection")
    summary = trace.get("job_board_portfolio")
    observed = {
        "job_list_page_url": trace.get("job_list_page_url"),
        "provider": trace.get("provider"),
        "provider_detection.url": (
            detection.get("url") if isinstance(detection, dict) else None
        ),
        "provider_detection.provider": (
            detection.get("provider") if isinstance(detection, dict) else None
        ),
        "job_board_portfolio.primary_url": (
            summary.get("primary_url") if isinstance(summary, dict) else None
        ),
        "job_board_portfolio.primary_provider": (
            summary.get("primary_provider") if isinstance(summary, dict) else None
        ),
    }
    return [
        field
        for field, value in observed.items()
        if value not in (None, "") and value != expected[field]
    ]


def _rank_first_party_portfolio(
    context: PipelineContext,
    portfolio: JobBoardPortfolio,
) -> JobBoardPortfolio:
    def rank(discovered: DiscoveredJobBoard) -> tuple[int, str]:
        tenant = discovered.board.identifier or tenant_locator(discovered.board.url)
        alias_rank = _tenant_entity_match_rank(
            context.hiring_entity_name or context.company.company_name,
            tenant,
        )
        if alias_rank is not None:
            return (alias_rank, discovered.board.url.casefold())
        if discovered.relationship_evidence_url and any(
            first_party_url
            and _same_url(discovered.relationship_evidence_url, first_party_url)
            for first_party_url in (
                context.career_page_url,
                context.company_website_url,
            )
        ):
            return (10, discovered.board.url.casefold())
        return (20, discovered.board.url.casefold())

    return JobBoardPortfolio(
        boards=tuple(sorted(portfolio.boards, key=rank)),
        eligible_set_complete=portfolio.eligible_set_complete,
        route_evidence=portfolio.route_evidence,
    )


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
    if not isinstance(organization, str) or not organization.strip():
        organization = _provider_employer_name_for_opening(
            provider_api,
            opening_url,
        )
    expected = context.hiring_entity_name or context.company.company_name
    if (
        not isinstance(organization, str)
        or not _strict_entity_key(organization)
        or _strict_entity_key(organization) != _strict_entity_key(expected)
    ):
        return None
    try:
        evidence_url = canonicalize_identity_url(opening_url)
    except ValueError:
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
        evidence_url=evidence_url,
    )


def _provider_inventory_board_hiring_evidence(
    context: PipelineContext,
    trace: dict,
) -> HiringIdentityEvidence | None:
    """Authorize a board only from complete, internally consistent provider inventory."""

    if not isinstance(trace, dict):
        return None
    provider_api = trace.get("provider_api")
    inventory = provider_api.get("inventory") if isinstance(provider_api, dict) else None
    evidence = (
        provider_api.get("employer_evidence")
        if isinstance(provider_api, dict)
        else None
    )
    candidate_count = (
        inventory.get("candidate_count")
        if isinstance(inventory, dict)
        else None
    )
    if (
        not isinstance(inventory, dict)
        or inventory.get("source") != "native_adapter"
        or inventory.get("complete") is not True
        or not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count <= 0
        or not isinstance(evidence, list)
        or len(evidence) != candidate_count
    ):
        return None

    expected = context.hiring_entity_name or context.company.company_name
    expected_key = _strict_entity_key(expected)
    opening_urls: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            return None
        employer_name = item.get("employer_name")
        opening_url = item.get("opening_url")
        if (
            not isinstance(employer_name, str)
            or _strict_entity_key(employer_name) != expected_key
            or not isinstance(opening_url, str)
        ):
            return None
        try:
            opening_identity = canonicalize_identity_url(opening_url)
        except ValueError:
            return None
        if opening_identity in opening_urls:
            return None
        opening_urls.add(opening_identity)

    relationship_type = (
        context.hiring_identity_evidence.relationship_type
        if context.hiring_identity_evidence is not None
        else "same_entity"
    )
    first_evidence_url = evidence[0].get("evidence_url")
    if not isinstance(first_evidence_url, str):
        return None
    try:
        evidence_url = canonicalize_identity_url(first_evidence_url)
    except ValueError:
        return None
    return HiringIdentityEvidence(
        source_company_name=context.company.company_name,
        hiring_entity_name=expected,
        relationship_type=relationship_type,
        verification_method="provider_inventory",
        verified=True,
        evidence_url=evidence_url,
    )


def _provider_employer_name_for_opening(
    provider_api: dict,
    opening_url: str,
) -> str | None:
    evidence = provider_api.get("employer_evidence")
    if not isinstance(evidence, list):
        return None
    matching_names = {
        item.get("employer_name").strip()
        for item in evidence
        if (
            isinstance(item, dict)
            and isinstance(item.get("employer_name"), str)
            and item["employer_name"].strip()
            and isinstance(item.get("opening_url"), str)
            and _same_url(item["opening_url"], opening_url)
        )
    }
    return next(iter(matching_names)) if len(matching_names) == 1 else None


def _promote_verified_native_provider_identity(
    current: ProviderIdentity,
    opening_url: str,
    registry: ProviderRegistry,
    match_trace: dict | None,
) -> ProviderIdentity:
    """Carry an existing relationship across a fully verified native result."""

    if (
        current.provider != "generic"
        or not current.relationship_verified
        or not isinstance(match_trace, dict)
    ):
        return current
    provider_api = match_trace.get("provider_api")
    if not isinstance(provider_api, dict):
        return current
    inventory = provider_api.get("inventory")
    adapter_trace = provider_api.get("adapter_trace")
    candidates = provider_api.get("candidates")
    selected = match_trace.get("selected")
    if (
        provider_api.get("provider") != provider_api.get("adapter")
        or not isinstance(provider_api.get("provider"), str)
        or not isinstance(inventory, dict)
        or inventory.get("source") != "native_adapter"
        or inventory.get("complete") is not True
        or not isinstance(adapter_trace, dict)
        or not isinstance(candidates, list)
        or not isinstance(selected, dict)
    ):
        return current

    selected_url = selected.get("url")
    if not isinstance(selected_url, str) or not _same_url(selected_url, opening_url):
        return current
    candidate_urls = [
        item.get("url")
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ]
    detail_urls = adapter_trace.get("detail_verified_opening_urls")
    inventory_urls = adapter_trace.get("inventory_verified_opening_urls")
    inventory_evidence = provider_api.get("employer_evidence")
    selected_has_inventory_evidence = bool(
        isinstance(inventory_evidence, list)
        and any(
            isinstance(item, dict)
            and isinstance(item.get("opening_url"), str)
            and _same_url(item["opening_url"], selected_url)
            and isinstance(item.get("employer_name"), str)
            and _strict_entity_key(item["employer_name"])
            == _strict_entity_key(current.hiring_entity_name)
            for item in inventory_evidence
        )
    )
    selected_is_detail_verified = bool(
        isinstance(detail_urls, list)
        and any(
            isinstance(url, str) and _same_url(selected_url, url)
            for url in detail_urls
        )
    )
    selected_is_inventory_verified = bool(
        isinstance(inventory_urls, list)
        and any(
            isinstance(url, str) and _same_url(selected_url, url)
            for url in inventory_urls
        )
        and selected_has_inventory_evidence
    )
    if (
        not any(_same_url(selected_url, url) for url in candidate_urls)
        or not (selected_is_detail_verified or selected_is_inventory_verified)
    ):
        return current

    board_identity = adapter_trace.get("board_identity")
    if not isinstance(board_identity, dict):
        return current
    provider = board_identity.get("provider")
    board_url = board_identity.get("url")
    tenant = board_identity.get("identifier")
    if (
        not isinstance(provider, str)
        or provider == "generic"
        or not isinstance(board_url, str)
        or not isinstance(tenant, str)
        or not tenant
    ):
        return current
    adapter = registry.adapter_named(provider)
    if adapter is None or provider_api.get("provider") != provider:
        return current
    try:
        canonical_board = canonicalize_identity_url(board_url)
    except ValueError:
        return current
    identified_board = adapter.identify_board(board_url)
    opening_board = adapter.identify_board(selected_url)
    if identified_board is not None:
        if (
            opening_board is None
            or identified_board.provider != provider
            or opening_board.provider != provider
            or identified_board.identifier != tenant
            or opening_board.identifier != tenant
            or not _same_url(identified_board.url, canonical_board)
            or not _same_url(opening_board.url, canonical_board)
        ):
            return current
    else:
        custom_prefix = "custom:"
        custom_tenant = tenant.removeprefix(custom_prefix)
        detection = provider_api.get("provider_detection")
        page_identity_is_verified = bool(
            callable(getattr(adapter, "identify_board_from_page", None))
            and isinstance(detection, dict)
            and detection.get("method") == "page_evidence"
            and detection.get("provider") == provider
            and isinstance(detection.get("url"), str)
            and _same_url(detection["url"], canonical_board)
        )
        runtime_inventory_identity = bool(
            board_identity.get("runtime_only") is True
            and selected_is_inventory_verified
            and page_identity_is_verified
        )
        custom_page_identity = bool(
            tenant.startswith(custom_prefix)
            and custom_tenant
            and page_identity_is_verified
        )
        if not (runtime_inventory_identity or custom_page_identity):
            return current
        if opening_board is not None:
            if (
                opening_board.provider != provider
                or not isinstance(opening_board.identifier, str)
                or (
                    custom_page_identity
                    and opening_board.identifier.casefold()
                    != custom_tenant.casefold()
                )
                or (
                    runtime_inventory_identity
                    and opening_board.identifier.casefold() != tenant.casefold()
                )
            ):
                return current
        elif custom_page_identity and not runtime_inventory_identity and not _same_site(
            canonical_board,
            selected_url,
        ):
            return current

    return ProviderIdentity(
        hiring_entity_name=current.hiring_entity_name,
        provider=provider,
        tenant=tenant,
        canonical_board_url=canonical_board,
        evidence_url=current.evidence_url,
        verification_method=current.verification_method,
        relationship_verified=True,
    )


def _opening_identity(
    context: PipelineContext,
    opening_url: str,
    registry: ProviderRegistry,
    match_trace: dict | None = None,
    *,
    provider_identity: ProviderIdentity | None = None,
    discovered_board: DiscoveredJobBoard | None = None,
) -> OpeningIdentity | None:
    provider_identity = provider_identity or context.provider_identity
    discovered_board = discovered_board or context.discovered_job_board
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
                discovered_board,
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
                discovered_board,
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
                discovered_board,
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
    verified_site_search = trace.get("verified_site_search")
    verified_pages = (
        verified_site_search.get("verified_pages")
        if isinstance(verified_site_search, dict)
        else None
    )
    verified_selected_pages = [
        item
        for item in verified_pages
        if isinstance(item, dict)
        and isinstance(item.get("url"), str)
        and _same_url(item["url"], selected_url)
    ] if isinstance(verified_pages, list) else []
    if not inventory and verified_selected_pages:
        inventory = {
            "complete": False,
            "scope": "title_filtered",
            "candidate_count": len(verified_selected_pages),
        }
    scope = inventory.get("scope")
    if scope not in {"full", "title_filtered"}:
        scope = "unknown"
    complete = inventory.get("complete")
    candidate_count = inventory.get("candidate_count")
    candidates = trace.get("candidates")
    selected_in_candidates = [
        item
        for item in candidates
        if isinstance(item, dict)
        and isinstance(item.get("url"), str)
        and _same_url(item["url"], selected_url)
    ] if isinstance(candidates, list) else []
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
        candidate_count = len(candidates) if isinstance(candidates, list) else 0
    elif candidate_count < 1 and selected_in_candidates:
        candidate_count = len(candidates)
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


def _deduplicate_public_board_identities(
    boards: list[DiscoveredJobBoard],
) -> list[DiscoveredJobBoard]:
    """Preserve portfolio rank while collapsing equivalent route evidence."""
    deduplicated: list[DiscoveredJobBoard] = []
    identities: set[tuple[str, str]] = set()
    for discovered in boards:
        try:
            identity = (
                discovered.board.provider.casefold(),
                canonicalize_identity_url(discovered.board.url),
            )
        except (AttributeError, TypeError, ValueError):
            deduplicated.append(discovered)
            continue
        if identity in identities:
            continue
        identities.add(identity)
        deduplicated.append(discovered)
    return deduplicated


def _same_site(left: str, right: str) -> bool:
    try:
        left_host = urlsplit(canonicalize_identity_url(left)).hostname or ""
        right_host = urlsplit(canonicalize_identity_url(right)).hostname or ""
        return _site_key(left_host) == _site_key(right_host)
    except ValueError:
        return False


def _same_public_host(left: str, right: str) -> bool:
    """Use exact host identity for provisional evidence; only `www` is equivalent."""

    try:
        left_host = (urlsplit(canonicalize_identity_url(left)).hostname or "").casefold()
        right_host = (urlsplit(canonicalize_identity_url(right)).hostname or "").casefold()
    except (TypeError, ValueError):
        return False
    return bool(
        left_host
        and right_host
        and left_host.removeprefix("www.") == right_host.removeprefix("www.")
    )


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
