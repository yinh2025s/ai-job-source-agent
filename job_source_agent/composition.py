from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .application_runner import ApplicationRunner
from .candidate_portfolio import CompositeCandidateDiscovery
from .candidate_discovery_coordinator import (
    CandidateDiscoveryCoordinator,
    CandidateDiscoveryInput,
    RouteFailure,
    RouteProducerOutput,
    RouteProvenance,
    CandidateDiscoveryRouteStatus,
    WebsiteCareerRouteInput,
)
from .career_search import CareerSearchResolver
from .career_surface_discovery import CareerSurfaceCandidateDiscovery
from .career_transport_budget import CareerTransportBudgetFetcher
from .company_identity import CompanyIdentityResolver
from .contracts import EvidenceCaptureCoordinator, FetchClient
from .identity_evidence import FilesystemLinkedInWebsiteEvidenceStore
from .company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)
from .direct_candidate_discovery import (
    ExternalApplyDiscovery,
    WebsiteCareerDiscovery,
)
from .page_cache import PageCacheFetcher
from .pipeline import JobSourceAgent
from .pipeline_application import PipelineApplication
from .posting_identity import LinkedInPostingIdentityProbe
from .providers import ProviderRegistry, build_default_provider_registry
from .provider_search_discovery import ProviderSearchCandidateDiscovery
from .provider_candidates import MAX_PROVIDER_CANDIDATES
from .provider_candidates import CandidateDiscoveryRequest, ProviderCandidatePool
from .models import STAGE_CAREER_DISCOVERY
from .public_domain_registry import CisaPublicDomainCandidateSource
from .rendered_fetcher import RenderedFetcher, SmartRenderedFetcher
from .retrying_fetcher import RetryingFetcher
from .run_configuration import AgentConfig, DeterministicRunConfig
from .search_backend import SearchBackend
from .snapshot import SnapshottingFetcher
from .snapshot_capture import SnapshotCaptureCoordinator
from .stage_checkpoint import FilesystemCheckpointStore
from .stages import (
    CareerDiscoveryStage,
    HiringIdentityResolutionStage,
    InputDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    ResultValidationStage,
    WebsiteResolutionStage,
)
from .web import Fetcher
from .website_resolver import CompanyWebsiteResolver


LINKEDIN_EVIDENCE_CACHE_FILENAME = "linkedin-website-evidence.json"


@dataclass(frozen=True)
class FetcherConfig:
    fixtures_dir: str | Path | None = None
    offline: bool = False
    timeout: float = 8
    render_mode: str = "none"
    render_budget: int = 3
    capture_screenshot: bool = False
    retries: int = 0
    retry_base_delay: float = 0.25
    retry_deadline: float | None = None
    snapshot_dir: str | Path | None = None


@dataclass
class ApplicationComponents:
    fetcher: FetchClient
    provider_registry: ProviderRegistry
    agent: JobSourceAgent
    pipeline: PipelineApplication


def build_fetcher(
    config: FetcherConfig,
    *,
    capture_coordinator: EvidenceCaptureCoordinator | None = None,
) -> FetchClient:
    common = {
        "fixtures_dir": config.fixtures_dir,
        "offline": config.offline,
        "timeout": config.timeout,
    }
    if config.render_mode == "always":
        fetcher: FetchClient = RenderedFetcher(
            **common,
            capture_screenshot=config.capture_screenshot,
        )
    elif config.render_mode == "smart":
        fetcher = SmartRenderedFetcher(
            **common,
            render_budget=config.render_budget,
            capture_screenshot=config.capture_screenshot,
        )
    elif config.render_mode == "none":
        fetcher = Fetcher(**common)
    else:
        raise ValueError(f"Unsupported render mode: {config.render_mode}")

    fetcher = CareerTransportBudgetFetcher(fetcher)
    if config.retries > 0 or config.retry_deadline is not None:
        fetcher = RetryingFetcher(
            fetcher,
            max_retries=config.retries,
            base_delay=config.retry_base_delay,
            deadline=config.retry_deadline,
        )
    if config.snapshot_dir and capture_coordinator is not None:
        fetcher = PageCacheFetcher(fetcher)
        return SnapshottingFetcher(
            fetcher,
            config.snapshot_dir,
            coordinator=capture_coordinator,
        )
    if config.snapshot_dir:
        fetcher = SnapshottingFetcher(fetcher, config.snapshot_dir)
    return PageCacheFetcher(fetcher)


def build_agent(
    fetcher: FetchClient,
    config: AgentConfig | None = None,
    provider_registry: ProviderRegistry | None = None,
    *,
    run_configuration: DeterministicRunConfig | None = None,
    search_backend: SearchBackend | None = None,
) -> JobSourceAgent:
    settings = config or AgentConfig()
    registry = provider_registry or build_default_provider_registry()
    return JobSourceAgent(
        fetcher,
        provider_registry=registry,
        max_candidates=settings.max_candidates,
        max_job_pages=settings.max_job_pages,
        max_job_board_attempts=settings.max_job_board_attempts,
        max_career_candidate_fetches=settings.max_career_candidate_fetches,
        max_career_discovery_transport_calls=(
            settings.max_career_discovery_transport_calls
        ),
        max_career_search_queries=settings.max_career_search_queries,
        max_ats_board_fetches=settings.max_ats_board_fetches,
        enable_sitemap_discovery=settings.enable_sitemap_discovery,
        enable_career_search=settings.enable_career_search,
        enable_parallel_candidate_discovery=(
            settings.enable_parallel_candidate_discovery
        ),
        evaluate_all_candidate_routes=settings.evaluate_all_candidate_routes,
        candidate_discovery_engine=settings.candidate_discovery_engine,
        provider_search_reserve_seconds=settings.provider_search_reserve_seconds,
        career_search_timeout=settings.career_search_timeout,
        search_backend=search_backend,
        run_configuration=run_configuration,
    )


def build_application(
    fetcher_config: FetcherConfig,
    agent_config: AgentConfig | None = None,
    provider_registry: ProviderRegistry | None = None,
    checkpoint_dir: str | Path | None = None,
    website_overrides: str | Path | None = None,
    linkedin_evidence_cache_path: str | Path | None = None,
    company_discovery_evidence_path: str | Path | None = None,
    run_configuration: DeterministicRunConfig | None = None,
    search_backend: SearchBackend | None = None,
) -> ApplicationComponents:
    capture_coordinator = (
        SnapshotCaptureCoordinator() if fetcher_config.snapshot_dir else None
    )
    fetcher = build_fetcher(
        fetcher_config,
        capture_coordinator=capture_coordinator,
    )
    return build_application_from_fetcher(
        fetcher,
        agent_config,
        provider_registry,
        checkpoint_dir=checkpoint_dir,
        website_overrides=website_overrides,
        linkedin_evidence_cache_path=linkedin_evidence_cache_path,
        company_discovery_evidence_path=company_discovery_evidence_path,
        run_configuration=run_configuration,
        search_backend=search_backend,
        capture_coordinator=capture_coordinator,
    )


def build_application_from_fetcher(
    fetcher: FetchClient,
    agent_config: AgentConfig | None = None,
    provider_registry: ProviderRegistry | None = None,
    *,
    checkpoint_dir: str | Path | None = None,
    website_overrides: str | Path | None = None,
    linkedin_evidence_cache_path: str | Path | None = None,
    company_discovery_evidence_path: str | Path | None = None,
    run_configuration: DeterministicRunConfig | None = None,
    capture_coordinator: EvidenceCaptureCoordinator | None = None,
    search_backend: SearchBackend | None = None,
) -> ApplicationComponents:
    """Assemble the product pipeline around an injected fetch boundary."""

    registry = provider_registry or build_default_provider_registry()
    settings = agent_config or (
        run_configuration.to_agent_config()
        if run_configuration is not None
        else AgentConfig()
    )
    deterministic_settings = run_configuration or DeterministicRunConfig.from_agent_config(
        settings
    )
    if deterministic_settings.to_agent_config() != DeterministicRunConfig.from_agent_config(
        settings
    ).to_agent_config():
        raise ValueError("run_configuration does not match agent_config")
    backend_configuration = (
        {
            "search_backend_kind": "legacy",
            "search_backend_contract_version": "1",
            "search_backend_profile_digest": None,
        }
        if search_backend is None
        else search_backend.public_configuration()
    )
    configured_backend = {
        "search_backend_kind": settings.search_backend_kind,
        "search_backend_contract_version": settings.search_backend_contract_version,
        "search_backend_profile_digest": settings.search_backend_profile_digest,
    }
    if backend_configuration != configured_backend:
        raise ValueError("search_backend does not match agent_config")
    if settings.candidate_discovery_engine == "coordinator_v2":
        configure_stage_reservations = getattr(
            fetcher,
            "configure_stage_reservations",
            None,
        )
        if not callable(configure_stage_reservations):
            fetcher = RetryingFetcher(fetcher, max_retries=0)
            configure_stage_reservations = fetcher.configure_stage_reservations
        configure_stage_reservations(
            {
                STAGE_CAREER_DISCOVERY: settings.provider_search_reserve_seconds,
            }
        )
    agent = build_agent(
        fetcher,
        settings,
        registry,
        run_configuration=deterministic_settings,
        search_backend=search_backend,
    )
    evidence_cache_path = linkedin_evidence_cache_path
    if evidence_cache_path is None and checkpoint_dir is not None:
        evidence_cache_path = Path(checkpoint_dir) / LINKEDIN_EVIDENCE_CACHE_FILENAME
    website_resolver = CompanyWebsiteResolver(
        fetcher,
        overrides_path=website_overrides,
        linkedin_evidence_store=(
            FilesystemLinkedInWebsiteEvidenceStore(evidence_cache_path)
            if evidence_cache_path is not None
            else None
        ),
        public_domain_source=CisaPublicDomainCandidateSource(fetcher),
    )
    company_discovery_store = (
        FilesystemCompanyDiscoveryEvidenceStore(company_discovery_evidence_path)
        if company_discovery_evidence_path is not None
        else None
    )
    external_apply_discovery = ExternalApplyDiscovery(registry)
    website_career_discovery = WebsiteCareerDiscovery(registry)
    career_surface_discovery = CareerSurfaceCandidateDiscovery(
        CareerSearchResolver(
            fetcher,
            max_results=2,
            max_queries=min(settings.max_career_search_queries, 2),
            max_source_fetches=2,
            search_backend=search_backend,
        ),
        agent,
        provider_registry=registry,
        max_surface_candidates=2,
        max_candidates=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
    )
    provider_search_discovery = ProviderSearchCandidateDiscovery(
        CareerSearchResolver(
            fetcher,
            max_results=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
            max_queries=settings.max_career_search_queries,
            # Preserve one RSS request per provider query and reserve two
            # bounded secondary-source rescues when every primary bucket drifts.
            max_source_fetches=settings.max_career_search_queries + 2,
            search_backend=search_backend,
        ),
        provider_registry=registry,
        max_candidates=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
    )
    candidate_discovery = CompositeCandidateDiscovery(
        (
            external_apply_discovery,
            website_career_discovery,
            career_surface_discovery,
            provider_search_discovery,
        ),
        limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
    )
    candidate_coordinator = CandidateDiscoveryCoordinator(
        external_apply=lambda source: _run_candidate_route(
            (external_apply_discovery,),
            _candidate_request(source, include_external_apply=True),
            producer="external_apply",
            limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
        ),
        provider_search=lambda source: _run_candidate_route(
            (provider_search_discovery,),
            _candidate_request(source),
            producer="provider_search",
            limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
        ),
        website_career=lambda source, route: _run_candidate_route(
            (website_career_discovery, career_surface_discovery),
            _candidate_request(source, route),
            producer="website_career",
            limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
        ),
        candidate_limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
    )
    runner = ApplicationRunner(
        (
            InputDiscoveryStage(),
            WebsiteResolutionStage(
                website_resolver,
                identity_hint_resolver=CompanyIdentityResolver(),
                company_discovery_evidence_store=company_discovery_store,
            ),
            HiringIdentityResolutionStage(
                CompanyIdentityResolver(
                    posting_probe=LinkedInPostingIdentityProbe(fetcher),
                    website_resolver=website_resolver,
                )
            ),
            CareerDiscoveryStage(
                agent,
                company_discovery_evidence_store=company_discovery_store,
                provider_registry=registry,
                enable_parallel_candidate_discovery=(
                    settings.enable_parallel_candidate_discovery
                ),
            ),
            JobBoardDiscoveryStage(
                agent,
                registry,
                candidate_discovery=candidate_discovery,
                candidate_coordinator=candidate_coordinator,
                enable_parallel_candidate_discovery=(
                    settings.enable_parallel_candidate_discovery
                ),
                evaluate_all_candidate_routes=(
                    settings.evaluate_all_candidate_routes
                ),
                candidate_discovery_engine=settings.candidate_discovery_engine,
                company_discovery_evidence_store=company_discovery_store,
                candidate_fetcher=fetcher,
            ),
            OpeningMatchStage(
                agent,
                registry,
                max_job_board_attempts=settings.max_job_board_attempts,
                company_discovery_evidence_store=company_discovery_store,
            ),
            ResultValidationStage(),
        ),
        checkpoint_store=(
            FilesystemCheckpointStore(checkpoint_dir) if checkpoint_dir is not None else None
        ),
        capture_coordinator=capture_coordinator,
        stage_budget_controller=fetcher,
    )
    return ApplicationComponents(
        fetcher=fetcher,
        provider_registry=registry,
        agent=agent,
        pipeline=PipelineApplication(
            runner,
            run_configuration=deterministic_settings,
        ),
    )


def _candidate_request(
    source: CandidateDiscoveryInput,
    website: WebsiteCareerRouteInput | None = None,
    *,
    include_external_apply: bool = False,
) -> CandidateDiscoveryRequest:
    return CandidateDiscoveryRequest(
        company_name=source.source_company_name,
        target_title=source.target_title,
        target_location=source.target_location,
        company_website_url=(website.company_website_url if website is not None else None),
        career_page_url=(website.career_page_url if website is not None else None),
        external_apply_url=(
            source.external_apply_url
            if include_external_apply
            else None
        ),
        linkedin_company_url=source.linkedin_company_url,
    )


def _run_candidate_route(
    discoveries,
    request: CandidateDiscoveryRequest,
    *,
    producer: str,
    limit: int,
) -> RouteProducerOutput:
    discoveries = tuple(discoveries)
    candidates = []
    failures = 0
    query_count = 0
    request_count = 0
    raw_result_count = 0
    accepted_search_result_count = 0
    skipped_candidate_count = 0
    query_error_count = 0
    tenant_probe_attempt_count = 0
    tenant_probe_statuses: set[str] = set()
    tenant_probe_reasons: set[str] = set()
    fetch_budget_unavailable = False
    stopped_reasons: set[str] = set()
    truncated = False
    for discovery in discoveries:
        try:
            result = discovery.discover(request)
        except Exception:
            failures += 1
            continue
        candidates.extend(result.candidates)
        trace = result.trace if isinstance(result.trace, dict) else {}
        search_trace = trace.get("search") if isinstance(trace.get("search"), dict) else trace
        queries = search_trace.get("queries") if isinstance(search_trace, dict) else None
        if isinstance(queries, list):
            query_count += len(queries)
            request_count += len(queries)
            raw_result_count += sum(
                int(query.get("result_count", 0))
                for query in queries
                if isinstance(query, dict)
                and isinstance(query.get("result_count", 0), int)
            )
            accepted_search_result_count += sum(
                len(query.get("candidates", ()))
                for query in queries
                if isinstance(query, dict)
                and isinstance(query.get("candidates"), list)
            )
            query_error_count += sum(
                1
                for query in queries
                if isinstance(query, dict) and query.get("error")
            )
        skipped = trace.get("skipped_candidate_count")
        if isinstance(skipped, int) and not isinstance(skipped, bool):
            skipped_candidate_count += max(0, skipped)
        tenant_probe = trace.get("tenant_probe_fallback")
        if isinstance(tenant_probe, dict):
            attempts = tenant_probe.get("attempts")
            if isinstance(attempts, list):
                tenant_probe_attempt_count += len(attempts)
            for key, destination in (
                ("status", tenant_probe_statuses),
                ("reason", tenant_probe_reasons),
            ):
                value = tenant_probe.get(key)
                if isinstance(value, str) and value:
                    destination.add(value)
        fetch_budget_unavailable = fetch_budget_unavailable or bool(
            search_trace.get("fetch_budget_unavailable")
            if isinstance(search_trace, dict)
            else False
        )
        stopped_reason = (
            search_trace.get("stopped_reason")
            if isinstance(search_trace, dict)
            else None
        )
        if isinstance(stopped_reason, str) and stopped_reason:
            stopped_reasons.add(stopped_reason)
        truncated = truncated or bool(trace.get("truncated"))

    if failures == len(discoveries):
        return RouteProducerOutput(
            (),
            RouteProvenance(producer, request_count=request_count, query_count=query_count),
            status=CandidateDiscoveryRouteStatus.FAILED,
            failure=RouteFailure("producer_exception", retryable=False),
        )
    pool = ProviderCandidatePool.build(candidates, limit=limit)
    request_count += tenant_probe_attempt_count
    provenance = RouteProvenance(
        producer,
        trace=(
            ("partial_failure_count", str(failures)),
            ("raw_result_count", str(raw_result_count)),
            ("accepted_search_result_count", str(accepted_search_result_count)),
            ("skipped_candidate_count", str(skipped_candidate_count)),
            ("query_error_count", str(query_error_count)),
            ("fetch_budget_unavailable", str(fetch_budget_unavailable).lower()),
            ("tenant_probe_attempt_count", str(tenant_probe_attempt_count)),
            ("tenant_probe_statuses", ",".join(sorted(tenant_probe_statuses)) or "none"),
            ("tenant_probe_reasons", ",".join(sorted(tenant_probe_reasons)) or "none"),
            ("stopped_reasons", ",".join(sorted(stopped_reasons)) or "none"),
        ),
        request_count=request_count,
        query_count=query_count,
        truncated=truncated or pool.truncated,
    )
    if not pool.candidates and "deadline_exhausted" in stopped_reasons:
        return RouteProducerOutput(
            (),
            provenance,
            status=CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
            failure=RouteFailure("fetch_budget_exhausted", retryable=True),
        )
    return RouteProducerOutput(
        pool.candidates,
        provenance,
    )
