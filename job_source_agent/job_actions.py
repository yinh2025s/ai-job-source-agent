from __future__ import annotations

from dataclasses import dataclass
import re


_TOKEN = re.compile(r"[a-z0-9]+")
_ACTION_VERBS = {
    "apply",
    "browse",
    "explore",
    "find",
    "search",
    "see",
    "view",
}
_JOB_OBJECTS = {
    "career",
    "careers",
    "job",
    "jobs",
    "opening",
    "openings",
    "opportunities",
    "opportunity",
    "position",
    "positions",
    "posting",
    "postings",
    "role",
    "roles",
    "vacancies",
    "vacancy",
}
_STATE_WORDS = {"available", "current", "open"}
_INTERNAL_MARKERS = {
    "employee login",
    "employee sign in",
    "existing employee",
    "internal applicant",
    "internal applicants",
    "log in to apply",
    "returning sign in",
}
_NON_LISTING_MARKERS = {
    "can t find a role",
    "cannot find a role",
    "cant find a role",
    "don t see a role",
    "do not see a role",
    "join our talent community",
    "register your interest",
    "submit your resume",
}
_STANDALONE_JOB_LIST_LABELS = {
    "employment",
    "employment opportunities",
    "job board",
    "job openings",
    "jobs in the house",
}
_NON_JOB_OPPORTUNITY_SCOPES = {
    "advertising",
    "business",
    "community",
    "franchise",
    "investment",
    "partnership",
    "partnerships",
    "sponsorship",
    "talent",
    "vendor",
    "volunteer",
}


@dataclass(frozen=True)
class CareerAction:
    kind: str
    normalized_label: str
    confidence: str


def classify_career_action(label: str | None) -> CareerAction | None:
    normalized = normalize_action_label(label)
    if (
        not normalized
        or is_internal_career_action(normalized)
        or any(marker in normalized for marker in _NON_LISTING_MARKERS)
    ):
        return None
    if normalized in _STANDALONE_JOB_LIST_LABELS:
        return CareerAction(
            kind="open_job_list",
            normalized_label=normalized,
            confidence="high",
        )
    ordered_tokens = _TOKEN.findall(normalized)
    tokens = set(ordered_tokens)
    if (
        tokens & {"opportunity", "opportunities"}
        and tokens & _NON_JOB_OPPORTUNITY_SCOPES
    ):
        return None
    if (
        len(ordered_tokens) == 2
        and ordered_tokens[-1] in {"opportunity", "opportunities"}
        and ordered_tokens[0] not in _NON_JOB_OPPORTUNITY_SCOPES
        and ordered_tokens[0] not in _ACTION_VERBS
    ):
        return CareerAction(
            kind="open_job_list",
            normalized_label=normalized,
            confidence="high",
        )
    has_object = bool(tokens & _JOB_OBJECTS)
    has_verb = bool(tokens & _ACTION_VERBS)
    has_state = bool(tokens & _STATE_WORDS)
    if not has_object or not (has_verb or has_state):
        return None
    if "search" in tokens or "find" in tokens:
        kind = "search_jobs"
    elif "apply" in tokens:
        kind = "open_job_list_and_apply"
    elif "explore" in tokens or "browse" in tokens:
        kind = "browse_jobs"
    else:
        kind = "open_job_list"
    return CareerAction(
        kind=kind,
        normalized_label=normalized,
        confidence="high",
    )


def is_explicit_career_action(label: str | None) -> bool:
    return classify_career_action(label) is not None


def is_internal_career_action(label: str | None) -> bool:
    normalized = normalize_action_label(label)
    return any(marker in normalized for marker in _INTERNAL_MARKERS)


def normalize_action_label(label: str | None) -> str:
    if not isinstance(label, str):
        return ""
    return " ".join(_TOKEN.findall(label.casefold()))
