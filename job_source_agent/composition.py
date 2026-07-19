from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .application_runner import ApplicationRunner
from .candidate_portfolio import CompositeCandidateDiscovery
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
from .rendered_fetcher import RenderedFetcher, SmartRenderedFetcher
from .retrying_fetcher import RetryingFetcher
from .run_configuration import AgentConfig, DeterministicRunConfig
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
        career_search_timeout=settings.career_search_timeout,
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
    agent = build_agent(
        fetcher,
        settings,
        registry,
        run_configuration=deterministic_settings,
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
    )
    company_discovery_store = (
        FilesystemCompanyDiscoveryEvidenceStore(company_discovery_evidence_path)
        if company_discovery_evidence_path is not None
        else None
    )
    candidate_discovery = CompositeCandidateDiscovery(
        (
            ExternalApplyDiscovery(registry),
            WebsiteCareerDiscovery(registry),
            CareerSurfaceCandidateDiscovery(
                CareerSearchResolver(
                    fetcher,
                    max_results=2,
                    max_queries=min(settings.max_career_search_queries, 2),
                    max_source_fetches=2,
                ),
                agent,
                provider_registry=registry,
                max_surface_candidates=2,
                max_candidates=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
            ),
            ProviderSearchCandidateDiscovery(
                CareerSearchResolver(
                    fetcher,
                    max_results=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
                    max_queries=settings.max_career_search_queries,
                    # Candidate search must finalize before the opening phase.
                    # Four source dispatches cover the primary/secondary search
                    # pair without allowing an empty search to consume the
                    # company-level discovery window.
                    max_source_fetches=2,
                ),
                provider_registry=registry,
                max_candidates=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
            ),
        ),
        limit=min(settings.max_candidates, MAX_PROVIDER_CANDIDATES),
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
            ),
            JobBoardDiscoveryStage(
                agent,
                registry,
                candidate_discovery=candidate_discovery,
                enable_parallel_candidate_discovery=(
                    settings.enable_parallel_candidate_discovery
                ),
                evaluate_all_candidate_routes=(
                    settings.evaluate_all_candidate_routes
                ),
                company_discovery_evidence_store=company_discovery_store,
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
