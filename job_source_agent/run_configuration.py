from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


RUN_CONFIGURATION_SCHEMA_VERSION = "1.8"
BATCH_EXECUTION_SCHEMA_VERSION = "1.1"
_LEGACY_RUN_CONFIGURATION_SCHEMA_VERSION = "1.0"
_TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION = "1.1"
_JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION = "1.2"
_PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION = "1.3"
_ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION = "1.4"
_STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION = "1.5"
_CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION = "1.6"
_PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION = "1.7"
_CANDIDATE_DISCOVERY_ENGINES = {"stage_v1", "coordinator_v2"}
_SEARCH_BACKEND_KINDS = {"legacy", "searxng"}
_MAX_BUDGET = 1_000
_MAX_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class AgentConfig:
    max_candidates: int = 12
    max_job_pages: int = 8
    max_job_board_attempts: int = 3
    max_career_candidate_fetches: int | None = None
    max_career_search_queries: int = 5
    max_ats_board_fetches: int = 5
    enable_sitemap_discovery: bool = True
    enable_career_search: bool = True
    career_search_timeout: float | None = None
    max_career_discovery_transport_calls: int | None = None
    enable_parallel_candidate_discovery: bool = False
    evaluate_all_candidate_routes: bool = False
    candidate_discovery_engine: str = "stage_v1"
    provider_search_reserve_seconds: float = 10.0
    search_backend_kind: str = "legacy"
    search_backend_contract_version: str = "1"
    search_backend_profile_digest: str | None = None


@dataclass(frozen=True)
class DeterministicRunConfig:
    """Versioned, privacy-safe settings that determine pipeline behavior."""

    max_candidates: int
    max_job_pages: int
    max_job_board_attempts: int
    max_career_candidate_fetches: int
    max_career_search_queries: int
    max_ats_board_fetches: int
    enable_sitemap_discovery: bool
    enable_career_search: bool
    career_search_timeout: float | None
    max_career_discovery_transport_calls: int | None = None
    enable_parallel_candidate_discovery: bool = False
    evaluate_all_candidate_routes: bool = False
    candidate_discovery_engine: str = "stage_v1"
    provider_search_reserve_seconds: float = 10.0
    search_backend_kind: str = "legacy"
    search_backend_contract_version: str = "1"
    search_backend_profile_digest: str | None = None
    _schema_version: str = field(
        default=RUN_CONFIGURATION_SCHEMA_VERSION,
        repr=False,
    )

    @classmethod
    def from_agent_config(cls, config: AgentConfig) -> DeterministicRunConfig:
        return cls.from_payload(
            {
                "schema_version": RUN_CONFIGURATION_SCHEMA_VERSION,
                "agent": {
                    **asdict(config),
                    "max_career_candidate_fetches": (
                        config.max_candidates
                        if config.max_career_candidate_fetches is None
                        else config.max_career_candidate_fetches
                    ),
                },
            }
        )

    @classmethod
    def from_payload(cls, payload: Any) -> DeterministicRunConfig:
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "agent"}:
            raise ValueError("Run configuration must contain only schema_version and agent")
        schema_version = payload["schema_version"]
        if schema_version not in {
            _LEGACY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION,
            _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            raise ValueError("Run configuration schema version is incompatible")
        agent = payload["agent"]
        expected_fields = {
            "max_candidates",
            "max_job_pages",
            "max_career_candidate_fetches",
            "max_career_search_queries",
            "max_ats_board_fetches",
            "enable_sitemap_discovery",
            "enable_career_search",
            "career_search_timeout",
        }
        if schema_version in {
            _TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION,
            _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("max_career_discovery_transport_calls")
        if schema_version in {
            _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("max_job_board_attempts")
        if schema_version in {
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("enable_parallel_candidate_discovery")
        if schema_version in {
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("evaluate_all_candidate_routes")
        if schema_version in {
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("candidate_discovery_engine")
        if schema_version in {
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("provider_search_reserve_seconds")
        if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION:
            expected_fields.update(
                {
                    "search_backend_kind",
                    "search_backend_contract_version",
                    "search_backend_profile_digest",
                }
            )
        if not isinstance(agent, dict) or set(agent) != expected_fields:
            raise ValueError("Run configuration agent fields are incomplete or unsupported")

        max_candidates = _bounded_integer(agent["max_candidates"], "max_candidates", minimum=1)
        max_job_pages = _bounded_integer(agent["max_job_pages"], "max_job_pages", minimum=1)
        max_job_board_attempts = (
            _bounded_integer(
                agent["max_job_board_attempts"],
                "max_job_board_attempts",
                minimum=1,
                maximum=8,
            )
            if schema_version
            in {
                _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
                _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
                _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else 1
        )
        max_career_candidate_fetches = _bounded_integer(
            agent["max_career_candidate_fetches"],
            "max_career_candidate_fetches",
            minimum=0,
        )
        max_career_discovery_transport_calls = (
            _optional_bounded_integer(
                agent["max_career_discovery_transport_calls"],
                "max_career_discovery_transport_calls",
                minimum=0,
            )
            if schema_version
            in {
                _TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION,
                _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
                _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
                _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else None
        )
        max_career_search_queries = _bounded_integer(
            agent["max_career_search_queries"],
            "max_career_search_queries",
            minimum=0,
        )
        max_ats_board_fetches = _bounded_integer(
            agent["max_ats_board_fetches"],
            "max_ats_board_fetches",
            minimum=0,
        )
        enable_sitemap_discovery = _boolean(
            agent["enable_sitemap_discovery"], "enable_sitemap_discovery"
        )
        enable_career_search = _boolean(agent["enable_career_search"], "enable_career_search")
        enable_parallel_candidate_discovery = (
            _boolean(
                agent["enable_parallel_candidate_discovery"],
                "enable_parallel_candidate_discovery",
            )
            if schema_version in {
                _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
                _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
                _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else False
        )
        evaluate_all_candidate_routes = (
            _boolean(
                agent["evaluate_all_candidate_routes"],
                "evaluate_all_candidate_routes",
            )
            if schema_version
            in {
                _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
                _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else False
        )
        if evaluate_all_candidate_routes and not enable_parallel_candidate_discovery:
            raise ValueError(
                "Candidate route evaluation requires parallel candidate discovery"
            )
        candidate_discovery_engine = (
            agent["candidate_discovery_engine"]
            if schema_version in {
                _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else "stage_v1"
        )
        if candidate_discovery_engine not in _CANDIDATE_DISCOVERY_ENGINES:
            raise ValueError("Run configuration candidate discovery engine is unsupported")
        if (
            candidate_discovery_engine == "coordinator_v2"
            and not enable_parallel_candidate_discovery
        ):
            raise ValueError(
                "Coordinator candidate discovery requires parallel candidate discovery"
            )
        career_search_timeout = _optional_timeout(agent["career_search_timeout"])
        provider_search_reserve_seconds = (
            _bounded_number(
                agent["provider_search_reserve_seconds"],
                "provider_search_reserve_seconds",
                minimum=0,
                maximum=60,
                inclusive_minimum=True,
            )
            if schema_version
            in {
                _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
                RUN_CONFIGURATION_SCHEMA_VERSION,
            }
            else 10.0
        )
        search_backend_kind = (
            agent["search_backend_kind"]
            if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION
            else "legacy"
        )
        if search_backend_kind not in _SEARCH_BACKEND_KINDS:
            raise ValueError("Run configuration search backend is unsupported")
        search_backend_contract_version = (
            agent["search_backend_contract_version"]
            if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION
            else "1"
        )
        if search_backend_contract_version != "1":
            raise ValueError("Run configuration search backend contract is unsupported")
        search_backend_profile_digest = (
            agent["search_backend_profile_digest"]
            if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION
            else None
        )
        if search_backend_profile_digest is not None and (
            not isinstance(search_backend_profile_digest, str)
            or len(search_backend_profile_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in search_backend_profile_digest
            )
        ):
            raise ValueError("Run configuration search backend profile digest is invalid")
        if search_backend_kind == "legacy" and search_backend_profile_digest is not None:
            raise ValueError("Legacy search backend cannot have a profile digest")
        if search_backend_kind != "legacy" and search_backend_profile_digest is None:
            raise ValueError("Configured search backend requires a profile digest")
        return cls(
            max_candidates=max_candidates,
            max_job_pages=max_job_pages,
            max_job_board_attempts=max_job_board_attempts,
            max_career_candidate_fetches=max_career_candidate_fetches,
            max_career_discovery_transport_calls=max_career_discovery_transport_calls,
            max_career_search_queries=max_career_search_queries,
            max_ats_board_fetches=max_ats_board_fetches,
            enable_sitemap_discovery=enable_sitemap_discovery,
            enable_career_search=enable_career_search,
            career_search_timeout=career_search_timeout,
            enable_parallel_candidate_discovery=enable_parallel_candidate_discovery,
            evaluate_all_candidate_routes=evaluate_all_candidate_routes,
            candidate_discovery_engine=candidate_discovery_engine,
            provider_search_reserve_seconds=provider_search_reserve_seconds,
            search_backend_kind=search_backend_kind,
            search_backend_contract_version=search_backend_contract_version,
            search_backend_profile_digest=search_backend_profile_digest,
            _schema_version=schema_version,
        )

    def to_payload(self) -> dict[str, Any]:
        agent = {
            "max_candidates": self.max_candidates,
            "max_job_pages": self.max_job_pages,
            "max_career_candidate_fetches": self.max_career_candidate_fetches,
            "max_career_search_queries": self.max_career_search_queries,
            "max_ats_board_fetches": self.max_ats_board_fetches,
            "enable_sitemap_discovery": self.enable_sitemap_discovery,
            "enable_career_search": self.enable_career_search,
            "career_search_timeout": self.career_search_timeout,
        }
        if self._schema_version in {
            _TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION,
            _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["max_career_discovery_transport_calls"] = (
                self.max_career_discovery_transport_calls
            )
        if self._schema_version in {
            _JOB_BOARD_PORTFOLIO_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["max_job_board_attempts"] = self.max_job_board_attempts
        if self._schema_version in {
            _PARALLEL_CANDIDATE_RUN_CONFIGURATION_SCHEMA_VERSION,
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["enable_parallel_candidate_discovery"] = (
                self.enable_parallel_candidate_discovery
            )
        if self._schema_version in {
            _ROUTE_EVALUATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            _STORED_PROVIDER_IDENTITY_RUN_CONFIGURATION_SCHEMA_VERSION,
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["evaluate_all_candidate_routes"] = self.evaluate_all_candidate_routes
        if self._schema_version in {
            _CANDIDATE_COORDINATOR_RUN_CONFIGURATION_SCHEMA_VERSION,
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["candidate_discovery_engine"] = self.candidate_discovery_engine
        if self._schema_version in {
            _PROVIDER_RESERVATION_RUN_CONFIGURATION_SCHEMA_VERSION,
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["provider_search_reserve_seconds"] = (
                self.provider_search_reserve_seconds
            )
        if self._schema_version == RUN_CONFIGURATION_SCHEMA_VERSION:
            agent["search_backend_kind"] = self.search_backend_kind
            agent["search_backend_contract_version"] = (
                self.search_backend_contract_version
            )
            agent["search_backend_profile_digest"] = (
                self.search_backend_profile_digest
            )
        return {"schema_version": self._schema_version, "agent": agent}

    def to_agent_config(self) -> AgentConfig:
        return AgentConfig(
            max_candidates=self.max_candidates,
            max_job_pages=self.max_job_pages,
            max_job_board_attempts=self.max_job_board_attempts,
            max_career_candidate_fetches=self.max_career_candidate_fetches,
            max_career_discovery_transport_calls=self.max_career_discovery_transport_calls,
            max_career_search_queries=self.max_career_search_queries,
            max_ats_board_fetches=self.max_ats_board_fetches,
            enable_sitemap_discovery=self.enable_sitemap_discovery,
            enable_career_search=self.enable_career_search,
            career_search_timeout=self.career_search_timeout,
            enable_parallel_candidate_discovery=self.enable_parallel_candidate_discovery,
            evaluate_all_candidate_routes=self.evaluate_all_candidate_routes,
            candidate_discovery_engine=self.candidate_discovery_engine,
            provider_search_reserve_seconds=self.provider_search_reserve_seconds,
            search_backend_kind=self.search_backend_kind,
            search_backend_contract_version=self.search_backend_contract_version,
            search_backend_profile_digest=self.search_backend_profile_digest,
        )

    @property
    def digest(self) -> str:
        return _payload_digest(self.to_payload())


@dataclass(frozen=True)
class BatchExecutionConfig:
    """Versioned live-run settings that affect whole-company completion reuse."""

    company_time_budget: float
    website_time_budget: float
    fetch_timeout: float
    fetch_retries: int
    retry_base_delay: float
    render_mode: str
    render_budget: int
    verify_limit: int
    offline: bool
    opening_phase_policy: str

    @classmethod
    def from_payload(cls, payload: Any) -> BatchExecutionConfig:
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "batch"}:
            raise ValueError("Batch execution configuration must contain schema_version and batch")
        if payload["schema_version"] != BATCH_EXECUTION_SCHEMA_VERSION:
            raise ValueError("Batch execution configuration schema version is incompatible")
        batch = payload["batch"]
        expected_fields = {
            "company_time_budget",
            "website_time_budget",
            "fetch_timeout",
            "fetch_retries",
            "retry_base_delay",
            "render_mode",
            "render_budget",
            "verify_limit",
            "offline",
            "opening_phase_policy",
        }
        if not isinstance(batch, dict) or set(batch) != expected_fields:
            raise ValueError("Batch execution fields are incomplete or unsupported")
        company_time_budget = _bounded_number(
            batch["company_time_budget"], "company_time_budget", minimum=0, maximum=3_600
        )
        website_time_budget = _bounded_number(
            batch["website_time_budget"], "website_time_budget", minimum=0, maximum=3_600
        )
        if website_time_budget > company_time_budget:
            raise ValueError("Batch website_time_budget cannot exceed company_time_budget")
        render_mode = batch["render_mode"]
        if render_mode not in {"none", "smart", "always"}:
            raise ValueError("Batch render_mode is unsupported")
        opening_phase_policy = batch["opening_phase_policy"]
        if opening_phase_policy != "reserved_after_verified_job_list_v1":
            raise ValueError("Batch opening_phase_policy is unsupported")
        return cls(
            company_time_budget=company_time_budget,
            website_time_budget=website_time_budget,
            fetch_timeout=_bounded_number(
                batch["fetch_timeout"], "fetch_timeout", minimum=0, maximum=300
            ),
            fetch_retries=_bounded_integer(
                batch["fetch_retries"], "fetch_retries", minimum=0, maximum=20
            ),
            retry_base_delay=_bounded_number(
                batch["retry_base_delay"],
                "retry_base_delay",
                minimum=0,
                maximum=60,
                inclusive_minimum=True,
            ),
            render_mode=render_mode,
            render_budget=_bounded_integer(
                batch["render_budget"], "render_budget", minimum=0, maximum=100
            ),
            verify_limit=_bounded_integer(
                batch["verify_limit"], "verify_limit", minimum=1, maximum=100
            ),
            offline=_boolean(batch["offline"], "offline"),
            opening_phase_policy=opening_phase_policy,
        )

    def to_payload(self) -> dict[str, Any]:
        return {"schema_version": BATCH_EXECUTION_SCHEMA_VERSION, "batch": asdict(self)}

    @property
    def digest(self) -> str:
        return _payload_digest(self.to_payload())


def combined_configuration_digest(*digests: str) -> str:
    for digest in digests:
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Configuration digests must be SHA-256 hex strings")
    return _payload_digest({"configuration_digests": list(digests)})


def _bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int = _MAX_BUDGET,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Run configuration {field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(
            f"Run configuration {field} must be between {minimum} and {maximum}"
        )
    return value


def _optional_bounded_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int = _MAX_BUDGET,
) -> int | None:
    if value is None:
        return None
    return _bounded_integer(value, field, minimum=minimum, maximum=maximum)


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Run configuration {field} must be a boolean")
    return value


def _optional_timeout(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Run configuration career_search_timeout must be a number or null")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "Run configuration career_search_timeout must be finite and between 0 and 300"
        )
    return timeout


def _bounded_number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    inclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Batch {field} must be a number")
    number = float(value)
    below_minimum = number < minimum if inclusive_minimum else number <= minimum
    if not math.isfinite(number) or below_minimum or number > maximum:
        raise ValueError(f"Batch {field} must be finite and between {minimum} and {maximum}")
    return number


def _payload_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
