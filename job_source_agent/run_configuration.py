from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


RUN_CONFIGURATION_SCHEMA_VERSION = "1.2"
BATCH_EXECUTION_SCHEMA_VERSION = "1.0"
_LEGACY_RUN_CONFIGURATION_SCHEMA_VERSION = "1.0"
_TRANSPORT_LIMIT_RUN_CONFIGURATION_SCHEMA_VERSION = "1.1"
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
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            expected_fields.add("max_career_discovery_transport_calls")
        if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION:
            expected_fields.add("max_job_board_attempts")
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
            if schema_version == RUN_CONFIGURATION_SCHEMA_VERSION
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
        career_search_timeout = _optional_timeout(agent["career_search_timeout"])
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
            RUN_CONFIGURATION_SCHEMA_VERSION,
        }:
            agent["max_career_discovery_transport_calls"] = (
                self.max_career_discovery_transport_calls
            )
        if self._schema_version == RUN_CONFIGURATION_SCHEMA_VERSION:
            agent["max_job_board_attempts"] = self.max_job_board_attempts
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
