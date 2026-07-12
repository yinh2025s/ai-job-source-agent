from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .application_runner import ApplicationRunner
from .company_identity import CompanyIdentityResolver
from .contracts import FetchClient
from .pipeline import JobSourceAgent
from .pipeline_application import PipelineApplication
from .providers import ProviderRegistry, build_default_provider_registry
from .rendered_fetcher import RenderedFetcher, SmartRenderedFetcher
from .retrying_fetcher import RetryingFetcher
from .snapshot import SnapshottingFetcher
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
    snapshot_dir: str | Path | None = None


@dataclass(frozen=True)
class AgentConfig:
    max_candidates: int = 12
    max_job_pages: int = 8
    max_career_candidate_fetches: int | None = None
    max_career_search_queries: int = 5
    max_ats_board_fetches: int = 5
    enable_sitemap_discovery: bool = True
    enable_career_search: bool = True
    career_search_timeout: float | None = None


@dataclass
class ApplicationComponents:
    fetcher: FetchClient
    provider_registry: ProviderRegistry
    agent: JobSourceAgent
    pipeline: PipelineApplication


def build_fetcher(config: FetcherConfig) -> FetchClient:
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

    if config.retries > 0:
        fetcher = RetryingFetcher(
            fetcher,
            max_retries=config.retries,
            base_delay=config.retry_base_delay,
        )
    if config.snapshot_dir:
        fetcher = SnapshottingFetcher(fetcher, config.snapshot_dir)
    return fetcher


def build_agent(
    fetcher: FetchClient,
    config: AgentConfig | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> JobSourceAgent:
    settings = config or AgentConfig()
    registry = provider_registry or build_default_provider_registry()
    return JobSourceAgent(
        fetcher,
        provider_registry=registry,
        max_candidates=settings.max_candidates,
        max_job_pages=settings.max_job_pages,
        max_career_candidate_fetches=settings.max_career_candidate_fetches,
        max_career_search_queries=settings.max_career_search_queries,
        max_ats_board_fetches=settings.max_ats_board_fetches,
        enable_sitemap_discovery=settings.enable_sitemap_discovery,
        enable_career_search=settings.enable_career_search,
        career_search_timeout=settings.career_search_timeout,
    )


def build_application(
    fetcher_config: FetcherConfig,
    agent_config: AgentConfig | None = None,
    provider_registry: ProviderRegistry | None = None,
    checkpoint_dir: str | Path | None = None,
    website_overrides: str | Path | None = None,
) -> ApplicationComponents:
    registry = provider_registry or build_default_provider_registry()
    fetcher = build_fetcher(fetcher_config)
    agent = build_agent(fetcher, agent_config, registry)
    runner = ApplicationRunner(
        (
            InputDiscoveryStage(),
            WebsiteResolutionStage(
                CompanyWebsiteResolver(fetcher, overrides_path=website_overrides)
            ),
            HiringIdentityResolutionStage(CompanyIdentityResolver()),
            CareerDiscoveryStage(agent),
            JobBoardDiscoveryStage(agent, registry),
            OpeningMatchStage(agent, registry),
            ResultValidationStage(),
        ),
        checkpoint_store=(
            FilesystemCheckpointStore(checkpoint_dir) if checkpoint_dir is not None else None
        ),
    )
    return ApplicationComponents(
        fetcher=fetcher,
        provider_registry=registry,
        agent=agent,
        pipeline=PipelineApplication(runner),
    )
