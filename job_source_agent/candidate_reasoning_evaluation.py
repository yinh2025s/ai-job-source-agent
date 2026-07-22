"""Offline A/B metrics for bounded LLM candidate reasoning.

This module deliberately consumes only frozen, evaluator-owned observations.
``reference_*`` fields are answer labels and must never be copied into planner,
ranker, search, or provider request objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Iterable, Literal
from urllib.parse import urlsplit


LLM_CANDIDATE_REASONING_EVALUATION_SCHEMA_VERSION = "1.1"
MAX_CANDIDATES_PER_OBSERVATION = 10
TOP_K = 3
LLMCausalContribution = Literal[
    "none", "planner_source_recovery", "ranker_ordering_recovery"
]
_CAUSAL_CONTRIBUTIONS = frozenset(
    {"none", "planner_source_recovery", "ranker_ordering_recovery"}
)


def _canonical_url(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{field} must be a public HTTPS URL")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError(f"{field} must include a hostname")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return f"https://{hostname.lower()}{port}{path}" + (
        f"?{parsed.query}" if parsed.query else ""
    )


def _canonical_urls(values: tuple[str, ...], field: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > maximum:
        raise ValueError(f"{field} exceeds limit {maximum}")
    canonical = tuple(_canonical_url(value, field) for value in values)
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{field} contains duplicate URLs")
    return canonical


@dataclass(frozen=True)
class CandidateReasoningABObservation:
    """One frozen eligible-G development record.

    The reference fields are intentionally evaluator-only labels.  They model
    a human/fixture answer and are not a permissible LLM input surface.
    """

    record_id: str
    eligible_g: bool
    reference_candidate_url: str
    reference_website_url: str
    frozen_search_evidence_urls: tuple[str, ...]
    baseline_top_candidate_urls: tuple[str, ...]
    treatment_top_candidate_urls: tuple[str, ...]
    baseline_verified_website_url: str | None
    treatment_verified_website_url: str | None
    treatment_cross_company: bool = False
    treatment_cross_tenant: bool = False
    replay_mismatch: bool = False
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_latency_ms: float = 0.0
    advisory_failure: bool = False
    llm_plan_used: bool = False
    llm_rank_used: bool = False
    llm_causal_contribution: LLMCausalContribution = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise ValueError("record_id must be a non-empty string")
        if self.eligible_g is not True:
            raise ValueError("observation must belong to the fixed eligible G subset")
        object.__setattr__(
            self,
            "reference_candidate_url",
            _canonical_url(self.reference_candidate_url, "reference_candidate_url"),
        )
        object.__setattr__(
            self,
            "reference_website_url",
            _canonical_url(self.reference_website_url, "reference_website_url"),
        )
        for field, maximum in (
            ("frozen_search_evidence_urls", MAX_CANDIDATES_PER_OBSERVATION),
            ("baseline_top_candidate_urls", TOP_K),
            ("treatment_top_candidate_urls", TOP_K),
        ):
            object.__setattr__(
                self, field, _canonical_urls(getattr(self, field), field, maximum=maximum)
            )
        for field in ("baseline_verified_website_url", "treatment_verified_website_url"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _canonical_url(value, field))
        for field in (
            "treatment_cross_company",
            "treatment_cross_tenant",
            "replay_mismatch",
            "advisory_failure",
            "llm_plan_used",
            "llm_rank_used",
        ):
            if not isinstance(getattr(self, field), bool):
                raise TypeError(f"{field} must be a bool")
        if not isinstance(self.llm_calls, int) or isinstance(self.llm_calls, bool) or self.llm_calls < 0:
            raise ValueError("llm_calls must be a non-negative integer")
        for field in ("prompt_tokens", "completion_tokens"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if (
            not isinstance(self.estimated_cost_usd, (int, float))
            or isinstance(self.estimated_cost_usd, bool)
            or self.estimated_cost_usd < 0
            or not isfinite(self.estimated_cost_usd)
        ):
            raise ValueError("estimated_cost_usd must be finite and non-negative")
        if not isinstance(self.llm_latency_ms, (int, float)) or isinstance(self.llm_latency_ms, bool):
            raise TypeError("llm_latency_ms must be numeric")
        if self.llm_latency_ms < 0 or not isfinite(self.llm_latency_ms):
            raise ValueError("llm_latency_ms must be finite and non-negative")
        if self.llm_causal_contribution not in _CAUSAL_CONTRIBUTIONS:
            raise ValueError("llm_causal_contribution is invalid")
        if self.llm_calls == 0 and (
            self.llm_plan_used
            or self.llm_rank_used
            or self.llm_causal_contribution != "none"
        ):
            raise ValueError("llm_calls=0 requires no LLM use or causal contribution")
        if self.llm_causal_contribution != "none":
            if self.baseline_candidate_hit or not self.treatment_candidate_hit:
                raise ValueError("causal contribution requires a candidate recovery")
            if not self.treatment_verified_website_hit:
                raise ValueError("causal contribution requires a verified website recovery")
            if (
                self.llm_causal_contribution == "planner_source_recovery"
                and not self.llm_plan_used
            ):
                raise ValueError("planner contribution requires llm_plan_used")
            if (
                self.llm_causal_contribution == "ranker_ordering_recovery"
                and not self.llm_rank_used
            ):
                raise ValueError("ranker contribution requires llm_rank_used")

    @property
    def baseline_candidate_hit(self) -> bool:
        return self.reference_candidate_url in self.baseline_top_candidate_urls

    @property
    def treatment_candidate_hit(self) -> bool:
        return self.reference_candidate_url in self.treatment_top_candidate_urls

    @property
    def treatment_recovers_g(self) -> bool:
        return not self.baseline_candidate_hit and self.treatment_candidate_hit

    @property
    def has_valid_causal_recovery(self) -> bool:
        return self.llm_causal_contribution != "none"

    @property
    def baseline_verified_website_hit(self) -> bool:
        return self.baseline_verified_website_url == self.reference_website_url

    @property
    def treatment_verified_website_hit(self) -> bool:
        return self.treatment_verified_website_url == self.reference_website_url

    @property
    def baseline_wrong_verified_url(self) -> bool:
        return (
            self.baseline_verified_website_url is not None
            and not self.baseline_verified_website_hit
        )

    @property
    def treatment_wrong_verified_url(self) -> bool:
        return (
            self.treatment_verified_website_url is not None
            and not self.treatment_verified_website_hit
        )

    @property
    def invented_or_modified_treatment_url_count(self) -> int:
        allowed = set(self.frozen_search_evidence_urls)
        return sum(url not in allowed for url in self.treatment_top_candidate_urls)


@dataclass(frozen=True)
class FrozenCandidate:
    """An evaluator-owned, frozen candidate. Runtime inputs use only ``candidate_id``."""

    candidate_id: str
    url: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        object.__setattr__(self, "url", _canonical_url(self.url, "candidate.url"))


def _frozen_pool(values: tuple[FrozenCandidate, ...], field: str) -> tuple[FrozenCandidate, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if not values or len(values) > MAX_CANDIDATES_PER_OBSERVATION:
        raise ValueError(f"{field} must contain 1 to {MAX_CANDIDATES_PER_OBSERVATION} candidates")
    if any(not isinstance(value, FrozenCandidate) for value in values):
        raise TypeError(f"{field} must contain FrozenCandidate instances")
    if len({value.candidate_id for value in values}) != len(values):
        raise ValueError(f"{field} contains duplicate candidate IDs")
    if len({value.url for value in values}) != len(values):
        raise ValueError(f"{field} contains duplicate URLs")
    return values


def _pool_ids(values: tuple[FrozenCandidate, ...]) -> frozenset[str]:
    return frozenset(value.candidate_id for value in values)


def _candidate_ids(
    values: tuple[str, ...], field: str, *, allowed: frozenset[str], maximum: int
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > maximum:
        raise ValueError(f"{field} exceeds limit {maximum}")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field} must contain non-empty candidate IDs")
    if len(set(values)) != len(values):
        raise ValueError(f"{field} contains duplicate candidate IDs")
    if not set(values).issubset(allowed):
        raise ValueError(f"{field} contains candidate IDs outside the frozen pool")
    return values


@dataclass(frozen=True)
class FrozenPlannerCausalABObservation:
    """Frozen planner outputs; this evaluator type is never a runtime request."""

    record_id: str
    candidate_pool: tuple[FrozenCandidate, ...]
    reference_candidate_id: str
    deterministic_source_candidate_ids: tuple[str, ...]
    llm_source_candidate_ids: tuple[str, ...]
    deterministic_top_candidate_ids: tuple[str, ...]
    llm_top_candidate_ids: tuple[str, ...]
    llm_structured_output_success: bool
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_latency_ms: float = 0.0
    verified_website_hit: bool = False
    cross_company: bool = False
    cross_tenant: bool = False
    wrong_verified_url: bool = False

    def __post_init__(self) -> None:
        _validate_record_id(self.record_id)
        pool = _frozen_pool(self.candidate_pool, "candidate_pool")
        object.__setattr__(self, "candidate_pool", pool)
        allowed = _pool_ids(pool)
        if self.reference_candidate_id not in allowed:
            raise ValueError("reference_candidate_id must be in the frozen pool")
        for field in ("deterministic_source_candidate_ids", "llm_source_candidate_ids"):
            object.__setattr__(
                self,
                field,
                _candidate_ids(getattr(self, field), field, allowed=allowed, maximum=MAX_CANDIDATES_PER_OBSERVATION),
            )
        for field in ("deterministic_top_candidate_ids", "llm_top_candidate_ids"):
            object.__setattr__(
                self, field, _candidate_ids(getattr(self, field), field, allowed=allowed, maximum=TOP_K)
            )
        _validate_usage_and_safety(self)
        if self.llm_calls == 0 and self.llm_structured_output_success:
            raise ValueError("llm_calls=0 cannot report planner structured output success")


@dataclass(frozen=True)
class FrozenRankerCausalABObservation:
    """Frozen ranker outputs over an identical candidate pool, never a runtime request."""

    record_id: str
    candidate_pool: tuple[FrozenCandidate, ...]
    reference_candidate_id: str
    deterministic_top_candidate_ids: tuple[str, ...]
    llm_top_candidate_ids: tuple[str, ...]
    llm_rank_invocation_success: bool
    llm_fallback_used: bool
    verified_website_hit: bool = False
    true_causal_recovery: bool = False
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_latency_ms: float = 0.0
    cross_company: bool = False
    cross_tenant: bool = False
    wrong_verified_url: bool = False

    def __post_init__(self) -> None:
        _validate_record_id(self.record_id)
        pool = _frozen_pool(self.candidate_pool, "candidate_pool")
        object.__setattr__(self, "candidate_pool", pool)
        allowed = _pool_ids(pool)
        if not isinstance(self.reference_candidate_id, str) or not self.reference_candidate_id:
            raise ValueError("reference_candidate_id must be a non-empty candidate ID")
        for field in ("deterministic_top_candidate_ids", "llm_top_candidate_ids"):
            object.__setattr__(
                self, field, _candidate_ids(getattr(self, field), field, allowed=allowed, maximum=TOP_K)
            )
        _validate_usage_and_safety(self)
        if self.llm_calls == 0 and (
            self.llm_rank_invocation_success or self.llm_fallback_used or self.true_causal_recovery
        ):
            raise ValueError("llm_calls=0 cannot report ranker activity or recovery")
        if self.llm_rank_invocation_success and self.llm_fallback_used:
            raise ValueError("successful rank invocation cannot also use fallback")
        if self.true_causal_recovery and not (
            self.llm_rank_invocation_success
            and self.reference_candidate_id in allowed
            and self.reference_candidate_id not in self.deterministic_top_candidate_ids
            and self.reference_candidate_id in self.llm_top_candidate_ids
            and self.verified_website_hit
        ):
            raise ValueError("true_causal_recovery lacks ranker causal evidence")


def _validate_record_id(record_id: str) -> None:
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("record_id must be a non-empty string")


def _validate_usage_and_safety(value: object) -> None:
    for field in ("llm_structured_output_success", "verified_website_hit", "cross_company", "cross_tenant", "wrong_verified_url"):
        if hasattr(value, field) and not isinstance(getattr(value, field), bool):
            raise TypeError(f"{field} must be a bool")
    for field in ("llm_rank_invocation_success", "llm_fallback_used", "true_causal_recovery"):
        if hasattr(value, field) and not isinstance(getattr(value, field), bool):
            raise TypeError(f"{field} must be a bool")
    if not isinstance(getattr(value, "llm_calls"), int) or isinstance(getattr(value, "llm_calls"), bool) or getattr(value, "llm_calls") < 0:
        raise ValueError("llm_calls must be a non-negative integer")
    for field in ("prompt_tokens", "completion_tokens"):
        token_value = getattr(value, field)
        if not isinstance(token_value, int) or isinstance(token_value, bool) or token_value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    for field in ("estimated_cost_usd", "llm_latency_ms"):
        numeric_value = getattr(value, field)
        if not isinstance(numeric_value, (int, float)) or isinstance(numeric_value, bool) or numeric_value < 0 or not isfinite(numeric_value):
            raise ValueError(f"{field} must be finite and non-negative")


@dataclass(frozen=True)
class Metric:
    count: int
    denominator: int

    def __post_init__(self) -> None:
        if self.count < 0 or self.denominator < 0 or self.count > self.denominator:
            raise ValueError("metric count must be within its denominator")

    @property
    def fraction(self) -> float:
        return self.count / self.denominator if self.denominator else 0.0

    @property
    def percentage(self) -> float:
        return self.fraction * 100.0


@dataclass(frozen=True)
class CandidateReasoningABReport:
    schema_version: str
    record_count: int
    baseline_candidate_recall_at_3: Metric
    treatment_candidate_recall_at_3: Metric
    candidate_recall_delta_percentage_points: float
    baseline_verified_website_recall: Metric
    treatment_verified_website_recall: Metric
    eligible_g_recovery_fraction: Metric
    baseline_wrong_verified_url_count: int
    treatment_wrong_verified_url_count: int
    invented_or_modified_treatment_url_count: int
    cross_company_count: int
    cross_tenant_count: int
    replay_mismatch_count: int
    calls_per_company_mean: float
    calls_per_company_max: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    estimated_cost_per_company_mean_usd: float
    latency_p50_ms: float
    latency_p95_ms: float
    advisory_failure_rate: Metric


@dataclass(frozen=True)
class CandidateReasoningGateResult:
    passed: bool
    failures: tuple[str, ...]
    report: CandidateReasoningABReport


@dataclass(frozen=True)
class FrozenPlannerCausalABReport:
    record_count: int
    structured_output_success: Metric
    deterministic_source_candidate_recall_at_10: Metric
    llm_source_candidate_recall_at_10: Metric
    deterministic_end_to_end_candidate_recall_at_3: Metric
    llm_end_to_end_candidate_recall_at_3: Metric
    verified_website_recall: Metric
    true_causal_recoveries: Metric
    cross_company_count: int
    cross_tenant_count: int
    wrong_verified_url_count: int
    calls_per_company_mean: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_estimated_cost_usd: float
    latency_p50_ms: float
    latency_p95_ms: float


@dataclass(frozen=True)
class FrozenRankerCausalABReport:
    record_count: int
    conditional_record_count: int
    deterministic_conditional_recall_at_3: Metric
    llm_conditional_recall_at_3: Metric
    rank_invocation_success: Metric
    fallback_count: int
    verified_website_recall: Metric
    true_causal_recoveries: Metric
    cross_company_count: int
    cross_tenant_count: int
    wrong_verified_url_count: int
    calls_per_company_mean: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_estimated_cost_usd: float
    latency_p50_ms: float
    latency_p95_ms: float


def evaluate_candidate_reasoning_ab(
    observations: Iterable[CandidateReasoningABObservation],
) -> CandidateReasoningABReport:
    """Aggregate a frozen eligible-G cohort; empty cohorts fail closed at gate time."""
    records = tuple(observations)
    if any(not isinstance(record, CandidateReasoningABObservation) for record in records):
        raise TypeError("observations must contain CandidateReasoningABObservation instances")
    ids = [record.record_id for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("observations contain duplicate record_id values")
    total = len(records)
    metric = lambda predicate: Metric(sum(bool(predicate(record)) for record in records), total)
    baseline_candidate = metric(lambda record: record.baseline_candidate_hit)
    treatment_candidate = metric(lambda record: record.treatment_candidate_hit)
    latencies = sorted(float(record.llm_latency_ms) for record in records)
    calls = [record.llm_calls for record in records]
    return CandidateReasoningABReport(
        schema_version=LLM_CANDIDATE_REASONING_EVALUATION_SCHEMA_VERSION,
        record_count=total,
        baseline_candidate_recall_at_3=baseline_candidate,
        treatment_candidate_recall_at_3=treatment_candidate,
        candidate_recall_delta_percentage_points=(
            treatment_candidate.percentage - baseline_candidate.percentage
        ),
        baseline_verified_website_recall=metric(
            lambda record: record.baseline_verified_website_hit
        ),
        treatment_verified_website_recall=metric(
            lambda record: record.treatment_verified_website_hit
        ),
        eligible_g_recovery_fraction=metric(
            lambda record: record.has_valid_causal_recovery
        ),
        baseline_wrong_verified_url_count=sum(
            record.baseline_wrong_verified_url for record in records
        ),
        treatment_wrong_verified_url_count=sum(
            record.treatment_wrong_verified_url for record in records
        ),
        invented_or_modified_treatment_url_count=sum(
            record.invented_or_modified_treatment_url_count for record in records
        ),
        cross_company_count=sum(record.treatment_cross_company for record in records),
        cross_tenant_count=sum(record.treatment_cross_tenant for record in records),
        replay_mismatch_count=sum(record.replay_mismatch for record in records),
        calls_per_company_mean=(sum(calls) / total if total else 0.0),
        calls_per_company_max=max(calls, default=0),
        total_prompt_tokens=sum(record.prompt_tokens for record in records),
        total_completion_tokens=sum(record.completion_tokens for record in records),
        total_tokens=sum(
            record.prompt_tokens + record.completion_tokens for record in records
        ),
        total_estimated_cost_usd=sum(
            float(record.estimated_cost_usd) for record in records
        ),
        estimated_cost_per_company_mean_usd=(
            sum(float(record.estimated_cost_usd) for record in records) / total
            if total
            else 0.0
        ),
        latency_p50_ms=_percentile(latencies, 50.0),
        latency_p95_ms=_percentile(latencies, 95.0),
        advisory_failure_rate=metric(lambda record: record.advisory_failure),
    )


def evaluate_frozen_planner_causal_ab(
    observations: Iterable[FrozenPlannerCausalABObservation],
) -> FrozenPlannerCausalABReport:
    """Measure source recovery using only frozen query outputs and candidate IDs."""
    records = tuple(observations)
    _validate_observation_collection(records, FrozenPlannerCausalABObservation)
    total = len(records)
    metric = lambda predicate: Metric(sum(bool(predicate(record)) for record in records), total)
    true_recovery = lambda record: (
        record.llm_calls > 0
        and record.llm_structured_output_success
        and record.reference_candidate_id
        not in record.deterministic_source_candidate_ids
        and record.reference_candidate_id in record.llm_source_candidate_ids
        and record.reference_candidate_id not in record.deterministic_top_candidate_ids
        and record.reference_candidate_id in record.llm_top_candidate_ids
        and record.verified_website_hit
    )
    return FrozenPlannerCausalABReport(
        record_count=total,
        structured_output_success=metric(lambda record: record.llm_structured_output_success),
        deterministic_source_candidate_recall_at_10=metric(
            lambda record: record.reference_candidate_id
            in record.deterministic_source_candidate_ids
        ),
        llm_source_candidate_recall_at_10=metric(
            lambda record: record.reference_candidate_id in record.llm_source_candidate_ids
        ),
        deterministic_end_to_end_candidate_recall_at_3=metric(
            lambda record: record.reference_candidate_id
            in record.deterministic_top_candidate_ids
        ),
        llm_end_to_end_candidate_recall_at_3=metric(
            lambda record: record.reference_candidate_id in record.llm_top_candidate_ids
        ),
        verified_website_recall=metric(lambda record: record.verified_website_hit),
        true_causal_recoveries=metric(true_recovery),
        cross_company_count=sum(record.cross_company for record in records),
        cross_tenant_count=sum(record.cross_tenant for record in records),
        wrong_verified_url_count=sum(record.wrong_verified_url for record in records),
        **_usage_summary(records),
    )


def evaluate_frozen_ranker_causal_ab(
    observations: Iterable[FrozenRankerCausalABObservation],
) -> FrozenRankerCausalABReport:
    """Measure ordering recovery only where the labelled candidate entered one frozen pool."""
    records = tuple(observations)
    _validate_observation_collection(records, FrozenRankerCausalABObservation)
    conditional = tuple(
        record
        for record in records
        if record.reference_candidate_id in _pool_ids(record.candidate_pool)
    )
    total = len(records)
    conditional_total = len(conditional)
    metric = lambda predicate: Metric(sum(bool(predicate(record)) for record in records), total)
    conditional_metric = lambda predicate: Metric(
        sum(bool(predicate(record)) for record in conditional), conditional_total
    )
    return FrozenRankerCausalABReport(
        record_count=total,
        conditional_record_count=conditional_total,
        deterministic_conditional_recall_at_3=conditional_metric(
            lambda record: record.reference_candidate_id
            in record.deterministic_top_candidate_ids
        ),
        llm_conditional_recall_at_3=conditional_metric(
            lambda record: record.reference_candidate_id in record.llm_top_candidate_ids
        ),
        rank_invocation_success=metric(lambda record: record.llm_rank_invocation_success),
        fallback_count=sum(record.llm_fallback_used for record in records),
        verified_website_recall=metric(lambda record: record.verified_website_hit),
        true_causal_recoveries=metric(lambda record: record.true_causal_recovery),
        cross_company_count=sum(record.cross_company for record in records),
        cross_tenant_count=sum(record.cross_tenant for record in records),
        wrong_verified_url_count=sum(record.wrong_verified_url for record in records),
        **_usage_summary(records),
    )


def _validate_observation_collection(
    records: tuple[object, ...], expected_type: type[object]
) -> None:
    if any(not isinstance(record, expected_type) for record in records):
        raise TypeError(f"observations must contain {expected_type.__name__} instances")
    ids = [record.record_id for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("observations contain duplicate record_id values")


def _usage_summary(records: tuple[object, ...]) -> dict[str, float | int]:
    total = len(records)
    latencies = sorted(float(record.llm_latency_ms) for record in records)
    return {
        "calls_per_company_mean": (
            sum(record.llm_calls for record in records) / total if total else 0.0
        ),
        "total_prompt_tokens": sum(record.prompt_tokens for record in records),
        "total_completion_tokens": sum(record.completion_tokens for record in records),
        "total_estimated_cost_usd": sum(
            float(record.estimated_cost_usd) for record in records
        ),
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
    }


def evaluate_candidate_reasoning_gate(
    report: CandidateReasoningABReport,
) -> CandidateReasoningGateResult:
    """Apply the Phase C promotion criteria without weakening zero-tolerance gates."""
    if not isinstance(report, CandidateReasoningABReport):
        raise TypeError("report must be a CandidateReasoningABReport")
    failures: list[str] = []
    if report.record_count == 0:
        failures.append("empty_eligible_g_subset")
    if report.candidate_recall_delta_percentage_points < 25.0:
        failures.append("candidate_recall_delta_below_25_percentage_points")
    if report.eligible_g_recovery_fraction.fraction < 0.40:
        failures.append("eligible_g_recovery_below_40_percent")
    for field, value in (
        ("wrong_verified_urls", report.treatment_wrong_verified_url_count),
        ("model_invented_or_modified_urls", report.invented_or_modified_treatment_url_count),
        ("cross_company", report.cross_company_count),
        ("cross_tenant", report.cross_tenant_count),
        ("replay_mismatch", report.replay_mismatch_count),
    ):
        if value:
            failures.append(f"{field}_nonzero")
    if report.calls_per_company_mean > 2.0:
        failures.append("calls_per_company_mean_above_2")
    if report.calls_per_company_max > 2:
        failures.append("calls_per_company_max_above_2")
    return CandidateReasoningGateResult(
        passed=not failures,
        failures=tuple(failures),
        report=report,
    )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    # Nearest-rank is deterministic and makes small fixed cohorts auditable.
    index = max(0, ceil((percentile / 100.0) * len(sorted_values)) - 1)
    return sorted_values[index]
