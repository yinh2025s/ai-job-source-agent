from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlparse


LLM_DECISION_SCHEMA_VERSION = "1"
MAX_PLANNER_QUERIES = 3
MAX_RANKER_CANDIDATES = 10

QueryPurpose = Literal["official_website", "career_site", "provider_site"]
Confidence = Literal["high", "medium", "low"]
DecisionKind = Literal["query_plan", "candidate_rank"]
DecisionStatus = Literal["success", "failure"]
AdvisoryFailureCode = Literal[
    "TIMEOUT",
    "PROVIDER_ERROR",
    "MALFORMED_JSON",
    "SCHEMA_INVALID",
    "UNKNOWN_CANDIDATE_ID",
    "OUTPUT_URL_FORBIDDEN",
    "INPUT_POLICY_REJECTED",
    "DECISION_STORE_ERROR",
    "CALL_BUDGET_EXHAUSTED",
]

QUERY_PURPOSES = frozenset({"official_website", "career_site", "provider_site"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
DECISION_KINDS = frozenset({"query_plan", "candidate_rank"})
DECISION_STATUSES = frozenset({"success", "failure"})
ADVISORY_FAILURE_CODES = frozenset(
    {
        "TIMEOUT",
        "PROVIDER_ERROR",
        "MALFORMED_JSON",
        "SCHEMA_INVALID",
        "UNKNOWN_CANDIDATE_ID",
        "OUTPUT_URL_FORBIDDEN",
        "INPUT_POLICY_REJECTED",
        "DECISION_STORE_ERROR",
        "CALL_BUDGET_EXHAUSTED",
    }
)


class LLMOutputURLForbidden(ValueError):
    """A planner tried to emit a URL instead of a bounded search query."""

PLANNER_REASON_CODES = frozenset(
    {
        "LEGAL_SUFFIX",
        "DESCRIPTIVE_SUFFIX",
        "ACRONYM",
        "BRAND_ALIAS",
        "PARENT_BRAND",
        "NO_SOURCE_BACKED_CANDIDATE",
        "SPECULATIVE_CANDIDATES_ONLY",
        "SAME_NAME_AMBIGUITY",
        "IDENTITY_THRESHOLD_NOT_MET",
    }
)
RANKER_REASON_CODES = frozenset(
    {
        "BRAND_MATCH",
        "BRAND_CONFLICT",
        "INDUSTRY_MATCH",
        "INDUSTRY_CONFLICT",
        "LOCATION_MATCH",
        "LOCATION_CONFLICT",
        "OFFICIAL_SITE_SIGNAL",
        "CAREER_SITE_SIGNAL",
        "PROVIDER_SITE_SIGNAL",
        "AMBIGUOUS_EVIDENCE",
    }
)
REJECTION_REASON_CODES = frozenset(
    {
        "FETCH_REJECTED",
        "IDENTITY_MISMATCH",
        "IDENTITY_AMBIGUOUS",
        "PARKED_DOMAIN",
        "BLOCKED_DOMAIN",
        "REGION_MISMATCH",
        "SPECULATIVE_GUESS",
        "BELOW_IDENTITY_THRESHOLD",
    }
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_URL_LIKE = re.compile(
    r"(?i)(?:https?\s*:\s*//|www\s*\.|[a-z0-9-]+(?:\s*\.\s*[a-z0-9-]+)+\s*(?:/|$))"
)
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(
        r"(?i)\b(?:authorization|bearer|cookie|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token)\s*[:=]"
    ),
    re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\)"),
    re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){8,}\d(?!\d)"),
)
_FORBIDDEN_FIELDS = frozenset(
    {
    "chain_of_thought",
    "reasoning_content",
    "cookie",
    "cookies",
    "authorization",
    "headers",
    "access_token",
    "auth_token",
    "id_token",
    "refresh_token",
    "api_key",
    "password",
    "browser_state",
    "html",
    "raw_html",
    "raw_prompt",
    "prompt_text",
    "raw_response",
    "response_body",
    }
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "code",
        "cookie",
        "id_token",
        "jwt",
        "password",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)


@dataclass(frozen=True)
class RejectedCandidateSummary:
    candidate_id: str
    source: str
    rejection_reason: str
    display_domain: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.candidate_id, "candidate_id")
        _validate_text(self.source, "source", 64)
        _validate_enum(self.rejection_reason, REJECTION_REASON_CODES, "rejection_reason")
        _validate_optional_text(self.display_domain, "display_domain", 253)


@dataclass(frozen=True)
class SearchQuerySpec:
    query: str
    purpose: QueryPurpose

    def __post_init__(self) -> None:
        _validate_text(self.query, "query", 300)
        _validate_enum(self.purpose, QUERY_PURPOSES, "purpose")
        _reject_url_like(self.query, "planner query")

    @classmethod
    def from_payload(cls, payload: Any) -> SearchQuerySpec:
        value = _exact_object(payload, {"query", "purpose"}, "search query")
        return cls(**value)


@dataclass(frozen=True)
class QueryPlannerRequest:
    normalized_company_name: str
    linkedin_company_slug: str | None
    public_company_summary: str | None
    job_title: str | None
    job_location: str | None
    industry: str | None
    company_location: str | None
    rejected_candidates: tuple[RejectedCandidateSummary, ...] = ()
    schema_version: str = LLM_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _validate_text(self.normalized_company_name, "normalized_company_name", 300)
        _validate_optional_identifier(self.linkedin_company_slug, "linkedin_company_slug")
        _validate_optional_text(self.public_company_summary, "public_company_summary", 1_000)
        _validate_optional_text(self.job_title, "job_title", 300)
        _validate_optional_text(self.job_location, "job_location", 300)
        _validate_optional_text(self.industry, "industry", 200)
        _validate_optional_text(self.company_location, "company_location", 300)
        for name in (
            "public_company_summary",
            "job_title",
            "job_location",
            "industry",
            "company_location",
        ):
            _reject_sensitive_text(getattr(self, name), name)
        _require_tuple(self.rejected_candidates, "rejected_candidates")
        if len(self.rejected_candidates) > 10:
            raise ValueError("rejected_candidates exceeds limit")
        _require_instances(self.rejected_candidates, RejectedCandidateSummary, "rejected_candidates")
        _reject_duplicate(item.candidate_id for item in self.rejected_candidates)


@dataclass(frozen=True)
class QueryPlannerDecision:
    normalized_company_name: str
    core_brand_tokens: tuple[str, ...]
    legal_or_descriptive_suffixes: tuple[str, ...]
    possible_aliases: tuple[str, ...]
    queries: tuple[SearchQuerySpec, ...]
    ambiguous: bool
    reason_codes: tuple[str, ...]
    schema_version: str = LLM_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _validate_text(self.normalized_company_name, "normalized_company_name", 300)
        _reject_url_like(self.normalized_company_name, "normalized_company_name")
        for name, values, limit, maximum in (
            ("core_brand_tokens", self.core_brand_tokens, 12, 80),
            (
                "legal_or_descriptive_suffixes",
                self.legal_or_descriptive_suffixes,
                8,
                100,
            ),
            ("possible_aliases", self.possible_aliases, 8, 200),
        ):
            _validate_text_tuple(values, name, limit, maximum, forbid_url=True)
        _require_tuple(self.queries, "queries")
        if len(self.queries) > MAX_PLANNER_QUERIES:
            raise ValueError("queries exceeds limit")
        _require_instances(self.queries, SearchQuerySpec, "queries")
        _reject_duplicate((item.query, item.purpose) for item in self.queries)
        _validate_bool(self.ambiguous, "ambiguous")
        _validate_enum_tuple(self.reason_codes, PLANNER_REASON_CODES, "reason_codes", 10)

    @classmethod
    def from_payload(cls, payload: Any) -> QueryPlannerDecision:
        expected = {
            "schema_version",
            "normalized_company_name",
            "core_brand_tokens",
            "legal_or_descriptive_suffixes",
            "possible_aliases",
            "queries",
            "ambiguous",
            "reason_codes",
        }
        value = _exact_object(payload, expected, "planner decision")
        return cls(
            schema_version=value["schema_version"],
            normalized_company_name=value["normalized_company_name"],
            core_brand_tokens=_string_tuple(value["core_brand_tokens"], "core_brand_tokens"),
            legal_or_descriptive_suffixes=_string_tuple(
                value["legal_or_descriptive_suffixes"],
                "legal_or_descriptive_suffixes",
            ),
            possible_aliases=_string_tuple(
                value["possible_aliases"],
                "possible_aliases",
            ),
            queries=tuple(SearchQuerySpec.from_payload(item) for item in _list(value["queries"], "queries")),
            ambiguous=value["ambiguous"],
            reason_codes=_string_tuple(value["reason_codes"], "reason_codes"),
        )


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    url: str
    title: str
    snippet: str
    source: str
    query_id: str
    rank: int

    def __post_init__(self) -> None:
        _validate_id(self.candidate_id, "candidate_id")
        _validate_https_url(self.url)
        _validate_text(self.title, "title", 300, allow_empty=True)
        _validate_text(self.snippet, "snippet", 1_000, allow_empty=True)
        _reject_sensitive_text(self.title, "title")
        _reject_sensitive_text(self.snippet, "snippet")
        _validate_id(self.query_id, "query_id")
        _validate_identifier(self.source, "source")
        _validate_positive_int(self.rank, "rank", 100)


@dataclass(frozen=True)
class CandidateRankerRequest:
    normalized_company_name: str
    industry: str | None
    company_location: str | None
    candidates: tuple[CandidateEvidence, ...]
    context_evidence_ids: tuple[str, ...] = ()
    schema_version: str = LLM_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _validate_text(self.normalized_company_name, "normalized_company_name", 300)
        _validate_optional_text(self.industry, "industry", 200)
        _validate_optional_text(self.company_location, "company_location", 300)
        _reject_sensitive_text(self.industry, "industry")
        _reject_sensitive_text(self.company_location, "company_location")
        _require_tuple(self.candidates, "candidates")
        if not self.candidates or len(self.candidates) > MAX_RANKER_CANDIDATES:
            raise ValueError("candidates must contain between 1 and 10 entries")
        _require_instances(self.candidates, CandidateEvidence, "candidates")
        _reject_duplicate(item.candidate_id for item in self.candidates)
        _validate_id_tuple(self.context_evidence_ids, "context_evidence_ids", 10)


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: str
    confidence_bucket: Confidence
    evidence_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_id(self.candidate_id, "candidate_id")
        _validate_enum(
            self.confidence_bucket,
            CONFIDENCE_LEVELS,
            "confidence_bucket",
        )
        _validate_id_tuple(self.evidence_ids, "evidence_ids", 10)
        _validate_enum_tuple(self.reason_codes, RANKER_REASON_CODES, "reason_codes", 10)

    @classmethod
    def from_payload(cls, payload: Any) -> RankedCandidate:
        value = _exact_object(
            payload,
            {
                "candidate_id",
                "confidence_bucket",
                "evidence_ids",
                "reason_codes",
            },
            "ranked candidate",
        )
        return cls(
            candidate_id=value["candidate_id"],
            confidence_bucket=value["confidence_bucket"],
            evidence_ids=_string_tuple(value["evidence_ids"], "evidence_ids"),
            reason_codes=_string_tuple(value["reason_codes"], "reason_codes"),
        )


@dataclass(frozen=True)
class CandidateRankerDecision:
    ranked_candidates: tuple[RankedCandidate, ...]
    ambiguous: bool
    schema_version: str = LLM_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _require_tuple(self.ranked_candidates, "ranked_candidates")
        if not self.ranked_candidates or len(self.ranked_candidates) > MAX_RANKER_CANDIDATES:
            raise ValueError("ranked_candidates must contain between 1 and 10 entries")
        _require_instances(self.ranked_candidates, RankedCandidate, "ranked_candidates")
        _reject_duplicate(item.candidate_id for item in self.ranked_candidates)
        _validate_bool(self.ambiguous, "ambiguous")

    @classmethod
    def from_payload(
        cls,
        payload: Any,
        request: CandidateRankerRequest,
    ) -> CandidateRankerDecision:
        value = _exact_object(
            payload,
            {"schema_version", "ranked_candidates", "ambiguous"},
            "ranker decision",
        )
        decision = cls(
            schema_version=value["schema_version"],
            ranked_candidates=tuple(
                RankedCandidate.from_payload(item)
                for item in _list(value["ranked_candidates"], "ranked_candidates")
            ),
            ambiguous=value["ambiguous"],
        )
        candidate_ids = {item.candidate_id for item in request.candidates}
        ranked_ids = {item.candidate_id for item in decision.ranked_candidates}
        if ranked_ids != candidate_ids:
            raise ValueError("Ranker decision must reference every and only input candidate ID")
        evidence_ids = {
            *(item.candidate_id for item in request.candidates),
            *request.context_evidence_ids,
        }
        if any(
            evidence_id not in evidence_ids
            for item in decision.ranked_candidates
            for evidence_id in item.evidence_ids
        ):
            raise ValueError("Ranker decision references an unknown evidence ID")
        return decision


@dataclass(frozen=True)
class StructuredLLMRequest:
    decision_kind: DecisionKind
    schema_name: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _validate_enum(self.decision_kind, DECISION_KINDS, "decision_kind")
        _validate_identifier(self.schema_name, "schema_name")
        object.__setattr__(self, "payload", _freeze_json_object(self.payload, "payload"))


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            _validate_nonnegative_int(getattr(self, name), name)
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens plus completion_tokens")


@dataclass(frozen=True)
class StructuredLLMResponse:
    payload: Mapping[str, Any]
    raw_response_id: str | None = None
    token_usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_json_object(self.payload, "payload"))
        _validate_optional_identifier(self.raw_response_id, "raw_response_id")
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must use TokenUsage")


@runtime_checkable
class LLMReasoningClient(Protocol):
    def complete(self, request: StructuredLLMRequest) -> StructuredLLMResponse:
        ...


@runtime_checkable
class CompanyQueryPlanner(Protocol):
    def plan(self, request: QueryPlannerRequest) -> QueryPlannerDecision:
        ...


@runtime_checkable
class CompanyCandidateRanker(Protocol):
    def rank(self, request: CandidateRankerRequest) -> CandidateRankerDecision:
        ...


@dataclass(frozen=True)
class LLMAdvisoryFailure:
    code: AdvisoryFailureCode
    decision_kind: DecisionKind
    detail: str | None = None

    def __post_init__(self) -> None:
        _validate_enum(self.code, ADVISORY_FAILURE_CODES, "code")
        _validate_enum(self.decision_kind, DECISION_KINDS, "decision_kind")
        _validate_optional_text(self.detail, "detail", 300)


@dataclass(frozen=True)
class LLMDecisionKey:
    decision_kind: DecisionKind
    normalized_company_identity_digest: str
    input_evidence_digest: str
    llm_provider: str
    model_id: str
    prompt_version: str
    decision_schema_version: str
    adapter_version: str

    def __post_init__(self) -> None:
        _validate_enum(self.decision_kind, DECISION_KINDS, "decision_kind")
        _validate_digest(self.normalized_company_identity_digest, "normalized_company_identity_digest")
        _validate_digest(self.input_evidence_digest, "input_evidence_digest")
        for name in ("llm_provider", "model_id", "prompt_version", "decision_schema_version", "adapter_version"):
            _validate_identifier(getattr(self, name), name)


def llm_decision_key_digest(key: LLMDecisionKey) -> str:
    """Content-address every behavior and evidence field in a decision key."""
    if not isinstance(key, LLMDecisionKey):
        raise TypeError("key must use LLMDecisionKey")
    payload = {
        "decision_kind": key.decision_kind,
        "normalized_company_identity_digest": key.normalized_company_identity_digest,
        "input_evidence_digest": key.input_evidence_digest,
        "llm_provider": key.llm_provider,
        "model_id": key.model_id,
        "prompt_version": key.prompt_version,
        "decision_schema_version": key.decision_schema_version,
        "adapter_version": key.adapter_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class LLMDecisionRecord:
    record_key: str
    execution_fingerprint: str
    key: LLMDecisionKey
    sanitized_request: Mapping[str, Any]
    sanitized_response: Mapping[str, Any]
    candidate_ids: tuple[str, ...]
    query_ids: tuple[str, ...]
    candidate_evidence_digest: str
    duration_ms: float
    token_usage: TokenUsage
    created_at_epoch: float
    status: DecisionStatus
    failure_code: AdvisoryFailureCode | None
    schema_version: str = LLM_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _validate_digest(self.record_key, "record_key")
        _validate_digest(self.execution_fingerprint, "execution_fingerprint")
        if not isinstance(self.key, LLMDecisionKey):
            raise TypeError("key must use LLMDecisionKey")
        object.__setattr__(self, "sanitized_request", _freeze_json_object(self.sanitized_request, "sanitized_request"))
        object.__setattr__(self, "sanitized_response", _freeze_json_object(self.sanitized_response, "sanitized_response"))
        _validate_id_tuple(self.candidate_ids, "candidate_ids", MAX_RANKER_CANDIDATES)
        _validate_id_tuple(self.query_ids, "query_ids", MAX_PLANNER_QUERIES)
        _validate_digest(self.candidate_evidence_digest, "candidate_evidence_digest")
        _validate_finite_nonnegative(self.duration_ms, "duration_ms")
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must use TokenUsage")
        _validate_finite_nonnegative(self.created_at_epoch, "created_at_epoch")
        _validate_enum(self.status, DECISION_STATUSES, "status")
        if self.status == "success" and self.failure_code is not None:
            raise ValueError("Successful decision cannot have a failure code")
        if self.status == "failure":
            _validate_enum(self.failure_code, ADVISORY_FAILURE_CODES, "failure_code")


@runtime_checkable
class LLMDecisionStore(Protocol):
    def load(self, key: LLMDecisionKey) -> LLMDecisionRecord | None:
        ...

    def save(self, record: LLMDecisionRecord) -> None:
        ...


def _exact_object(payload: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} must contain exactly the required fields")
    _reject_forbidden_keys(payload)
    return payload


def _freeze_json_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    frozen = _freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_json(value: Any, path: str = "payload") -> Any:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            _validate_text(value, path, 4_000, allow_empty=True)
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ValueError(f"{path} contains an invalid key")
            if key.casefold() in _FORBIDDEN_FIELDS:
                raise ValueError(f"{path} contains a forbidden field")
            result[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise ValueError(f"{path} contains too many items")
        return tuple(_freeze_json(item, path) for item in value)
    raise TypeError(f"{path} is not JSON-compatible")


def _reject_forbidden_keys(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        if key.casefold() in _FORBIDDEN_FIELDS:
            raise ValueError("Payload contains a forbidden field")
        if isinstance(value, dict):
            _reject_forbidden_keys(value)


def _validate_schema(value: str) -> None:
    if value != LLM_DECISION_SCHEMA_VERSION:
        raise ValueError("Candidate reasoning schema version is incompatible")


def _validate_text(value: Any, label: str, maximum: int, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    if (not allow_empty and not value.strip()) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} is invalid")


def _validate_optional_text(value: Any, label: str, maximum: int) -> None:
    if value is not None:
        _validate_text(value, label, maximum)


def _validate_identifier(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_optional_identifier(value: Any, label: str) -> None:
    if value is not None:
        _validate_identifier(value, label)


def _validate_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _validate_enum(value: Any, allowed: frozenset[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} is unsupported")


def _validate_bool(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be boolean")


def _validate_positive_int(value: Any, label: str, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} is invalid")


def _validate_nonnegative_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _validate_finite_nonnegative(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative")


def _require_tuple(value: Any, label: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be an immutable tuple")


def _require_instances(values: tuple[Any, ...], expected: type[Any], label: str) -> None:
    if any(not isinstance(item, expected) for item in values):
        raise TypeError(f"{label} contains an invalid item")


def _reject_duplicate(values: Any) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError("Duplicate identifiers are forbidden")


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    return tuple(_list(value, label))


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _validate_text_tuple(values: Any, label: str, limit: int, maximum: int, *, forbid_url: bool = False) -> None:
    _require_tuple(values, label)
    if len(values) > limit:
        raise ValueError(f"{label} exceeds limit")
    for value in values:
        _validate_text(value, label, maximum)
        if forbid_url:
            _reject_url_like(value, label)
    _reject_duplicate(values)


def _validate_id_tuple(values: Any, label: str, limit: int) -> None:
    _require_tuple(values, label)
    if len(values) > limit:
        raise ValueError(f"{label} exceeds limit")
    for value in values:
        _validate_id(value, label)
    _reject_duplicate(values)


def _validate_enum_tuple(values: Any, allowed: frozenset[str], label: str, limit: int) -> None:
    _require_tuple(values, label)
    if len(values) > limit:
        raise ValueError(f"{label} exceeds limit")
    for value in values:
        _validate_enum(value, allowed, label)
    _reject_duplicate(values)


def _reject_url_like(value: str, label: str) -> None:
    if _URL_LIKE.search(value):
        raise LLMOutputURLForbidden(f"{label} cannot contain a URL")


def _reject_sensitive_text(value: str | None, label: str) -> None:
    if value is not None and any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS):
        raise ValueError(f"{label} contains sensitive data")


def _validate_https_url(value: Any) -> None:
    _validate_text(value, "url", 2_048)
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Candidate URL contains unsafe characters")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Candidate URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("Candidate URL must be a normalized public HTTPS URL")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "example.com"} or host.endswith((".local", ".internal")):
        raise ValueError("Candidate URL host is not public")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Candidate URL cannot use a non-public address")
    if any(key.casefold() in _SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("Candidate URL contains a sensitive query parameter")
