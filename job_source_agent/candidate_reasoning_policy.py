from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EligibilityState = Literal[
    "DISABLED",
    "VERIFIED_WEBSITE",
    "PROVIDER_BYPASS",
    "IDENTITY_FORBIDDEN",
    "TRANSPORT_FORBIDDEN",
    "BUDGET_FORBIDDEN",
    "REPLAY_REQUIRED",
    "ELIGIBLE",
    "INELIGIBLE",
]
GCondition = Literal[
    "NO_SOURCE_BACKED_CANDIDATE",
    "SPECULATIVE_CANDIDATES_ONLY",
    "SAME_NAME_AMBIGUITY",
    "NAME_VARIANT_UNVERIFIED",
    "IDENTITY_THRESHOLD_NOT_MET",
]
IdentityState = Literal["resolved", "undisclosed", "ambiguous"]
TransportCause = Literal[
    "DNS_FAILED",
    "TLS_FAILED",
    "NETWORK_TIMEOUT",
    "HTTP_FORBIDDEN",
    "RATE_LIMITED",
    "BOT_PROTECTION",
    "CONNECTION_FAILED",
    "SERVER_ERROR",
    "FETCH_FAILED",
    "LOGIN_REQUIRED",
    "CAPTCHA_REQUIRED",
]

ELIGIBLE_G_CONDITIONS = frozenset(
    {
        "NO_SOURCE_BACKED_CANDIDATE",
        "SPECULATIVE_CANDIDATES_ONLY",
        "SAME_NAME_AMBIGUITY",
        "NAME_VARIANT_UNVERIFIED",
        "IDENTITY_THRESHOLD_NOT_MET",
    }
)
TRANSPORT_CAUSES = frozenset(
    {
        "DNS_FAILED",
        "TLS_FAILED",
        "NETWORK_TIMEOUT",
        "HTTP_FORBIDDEN",
        "RATE_LIMITED",
        "BOT_PROTECTION",
        "CONNECTION_FAILED",
        "SERVER_ERROR",
        "FETCH_FAILED",
        "LOGIN_REQUIRED",
        "CAPTCHA_REQUIRED",
    }
)


@dataclass(frozen=True)
class CandidateReasoningEligibilityContext:
    feature_enabled: bool
    has_verified_website: bool = False
    has_verified_provider_relationship: bool = False
    has_official_external_apply: bool = False
    identity_state: IdentityState = "resolved"
    transport_cause: TransportCause | None = None
    has_sufficient_budget: bool = True
    replay_mode: bool = False
    has_compatible_replay_fixture: bool = False
    later_stage_started: bool = False
    has_verified_terminal_decision: bool = False
    g_conditions: tuple[GCondition, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "feature_enabled",
            "has_verified_website",
            "has_verified_provider_relationship",
            "has_official_external_apply",
            "has_sufficient_budget",
            "replay_mode",
            "has_compatible_replay_fixture",
            "later_stage_started",
            "has_verified_terminal_decision",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        if self.identity_state not in {"resolved", "undisclosed", "ambiguous"}:
            raise ValueError("identity_state is unsupported")
        if self.transport_cause is not None and self.transport_cause not in TRANSPORT_CAUSES:
            raise ValueError("transport_cause is unsupported")
        if not isinstance(self.g_conditions, tuple):
            raise TypeError("g_conditions must be an immutable tuple")
        if any(condition not in ELIGIBLE_G_CONDITIONS for condition in self.g_conditions):
            raise ValueError("g_conditions contains an unsupported typed cause")
        if len(self.g_conditions) != len(set(self.g_conditions)):
            raise ValueError("g_conditions cannot contain duplicates")


@dataclass(frozen=True)
class CandidateReasoningEligibilityResult:
    state: EligibilityState
    eligible: bool
    reason_code: str
    g_conditions: tuple[GCondition, ...] = ()

    def __post_init__(self) -> None:
        if self.eligible != (self.state == "ELIGIBLE"):
            raise ValueError("eligible flag must agree with state")
        if not self.reason_code or not self.reason_code.isascii():
            raise ValueError("reason_code is invalid")
        if not isinstance(self.g_conditions, tuple):
            raise TypeError("g_conditions must be an immutable tuple")
        if self.state != "ELIGIBLE" and self.g_conditions:
            raise ValueError("Only eligible results may carry G conditions")


def evaluate_candidate_reasoning_eligibility(
    context: CandidateReasoningEligibilityContext,
) -> CandidateReasoningEligibilityResult:
    """Apply the frozen priority policy without consulting traces or mutable state."""

    if not isinstance(context, CandidateReasoningEligibilityContext):
        raise TypeError("context must use CandidateReasoningEligibilityContext")
    if not context.feature_enabled:
        return _result("DISABLED", "FEATURE_DISABLED")
    if context.has_verified_website:
        return _result("VERIFIED_WEBSITE", "VERIFIED_WEBSITE_EXISTS")
    if context.has_verified_provider_relationship or context.has_official_external_apply:
        return _result("PROVIDER_BYPASS", "SUFFICIENT_DIRECT_ROUTE_EXISTS")
    if context.identity_state != "resolved":
        return _result("IDENTITY_FORBIDDEN", f"IDENTITY_{context.identity_state.upper()}")
    if context.transport_cause is not None:
        return _result("TRANSPORT_FORBIDDEN", context.transport_cause)
    if not context.has_sufficient_budget:
        return _result("BUDGET_FORBIDDEN", "INSUFFICIENT_REASONING_BUDGET")
    if context.replay_mode and not context.has_compatible_replay_fixture:
        return _result("REPLAY_REQUIRED", "LLM_DECISION_FIXTURE_REQUIRED")
    if context.later_stage_started:
        return _result("IDENTITY_FORBIDDEN", "LATER_STAGE_ALREADY_STARTED")
    if context.has_verified_terminal_decision:
        return _result("IDENTITY_FORBIDDEN", "VERIFIED_TERMINAL_DECISION_EXISTS")
    if context.g_conditions:
        return CandidateReasoningEligibilityResult(
            state="ELIGIBLE",
            eligible=True,
            reason_code="TYPED_G_CONDITION",
            g_conditions=context.g_conditions,
        )
    return _result("INELIGIBLE", "NO_TYPED_G_CONDITION")


def _result(state: EligibilityState, reason_code: str) -> CandidateReasoningEligibilityResult:
    return CandidateReasoningEligibilityResult(
        state=state,
        eligible=False,
        reason_code=reason_code,
    )
