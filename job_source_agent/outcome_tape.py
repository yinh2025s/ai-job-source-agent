from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Literal, TypeAlias

from .evidence_scope import EvidenceScopeRef
from .reasons import REASON_SPECS, reason_spec
from .request_identity import (
    RequestIdentity,
    build_request_identity,
    request_identity_from_dict,
    sanitize_url,
)
from .snapshot import sanitize_snapshot_body
from .web import FetchError, Page


OUTCOME_TAPE_SCHEMA_VERSION = "1.0"
FAILURE_TAXONOMY_VERSION = 1
OFFLINE_TAPE_DIVERGENCE = "OFFLINE_TAPE_DIVERGENCE"


class OutcomeTapeError(ValueError):
    """Raised when a scoped outcome tape is malformed or inconsistent."""


@dataclass(frozen=True)
class PageOutcomeTapeEntry:
    snapshot_store_id: str
    scope_id: str
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str
    request_ordinal: int
    request: RequestIdentity
    page_url: str
    html: str
    final_url: str | None = None
    source: str = "snapshot_replay"

    kind: ClassVar[Literal["page"]] = "page"

    def __post_init__(self) -> None:
        _validate_membership(self)
        _validate_request(self.request)
        _validate_sanitized_url(self.page_url, "page_url")
        if self.final_url is not None:
            _validate_sanitized_url(self.final_url, "final_url")
        if not isinstance(self.html, str) or sanitize_snapshot_body(self.html) != self.html:
            raise OutcomeTapeError("page HTML is not sanitized")
        _validate_source(self.source)

    def as_payload(self) -> dict[str, Any]:
        return {
            **_membership_payload(self),
            "kind": self.kind,
            "page": {
                "final_url": self.final_url,
                "html": self.html,
                "source": self.source,
                "url": self.page_url,
            },
            "request": self.request.as_dict(),
            "schema_version": OUTCOME_TAPE_SCHEMA_VERSION,
        }

    def to_page(self) -> Page:
        return Page(
            url=self.page_url,
            html=self.html,
            final_url=self.final_url,
            source=self.source,
        )


@dataclass(frozen=True)
class FetchFailureOutcomeTapeEntry:
    snapshot_store_id: str
    scope_id: str
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str
    request_ordinal: int
    request: RequestIdentity
    status: int | None
    reason_code: str
    retryable: bool
    message: str
    taxonomy_version: int = FAILURE_TAXONOMY_VERSION

    kind: ClassVar[Literal["fetch_failure"]] = "fetch_failure"

    def __post_init__(self) -> None:
        _validate_membership(self)
        _validate_request(self.request)
        if self.status is not None and (
            type(self.status) is not int or not 100 <= self.status <= 599
        ):
            raise OutcomeTapeError("failure status must be an HTTP status or null")
        if self.reason_code not in REASON_SPECS:
            raise OutcomeTapeError("failure reason_code is unknown")
        if type(self.retryable) is not bool:
            raise OutcomeTapeError("failure retryable must be boolean")
        if self.retryable != reason_spec(self.reason_code).retryable:
            raise OutcomeTapeError("failure retryability does not match its reason")
        if self.taxonomy_version != FAILURE_TAXONOMY_VERSION:
            raise OutcomeTapeError("failure taxonomy version is unsupported")
        if (
            not isinstance(self.message, str)
            or not self.message
            or len(self.message) > 500
            or sanitize_snapshot_body(self.message) != self.message
        ):
            raise OutcomeTapeError("failure message is not privacy-safe")

    def as_payload(self) -> dict[str, Any]:
        return {
            **_membership_payload(self),
            "failure": {
                "message": self.message,
                "reason_code": self.reason_code,
                "retryable": self.retryable,
                "status": self.status,
                "taxonomy_version": self.taxonomy_version,
            },
            "kind": self.kind,
            "request": self.request.as_dict(),
            "schema_version": OUTCOME_TAPE_SCHEMA_VERSION,
        }

    def to_error(self) -> FetchError:
        return FetchError(
            self.message,
            status=self.status,
            reason_code=self.reason_code,
            retryable=self.retryable,
            request_identity=self.request.as_dict(),
        )


OutcomeTapeEntry: TypeAlias = PageOutcomeTapeEntry | FetchFailureOutcomeTapeEntry


@dataclass(frozen=True)
class OutcomeTape:
    scope: EvidenceScopeRef
    entries: tuple[OutcomeTapeEntry, ...]

    def __init__(
        self,
        scope: EvidenceScopeRef,
        entries: Iterable[OutcomeTapeEntry],
    ) -> None:
        if not isinstance(scope, EvidenceScopeRef):
            raise TypeError("scope must be an EvidenceScopeRef")
        materialized = tuple(entries)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "entries", materialized)
        self._validate()

    def _validate(self) -> None:
        if len(self.entries) != self.scope.request_count:
            raise OutcomeTapeError("outcome tape count does not match evidence scope")
        expected_membership = _scope_membership(self.scope)
        for ordinal, entry in enumerate(self.entries, start=1):
            if not isinstance(entry, (PageOutcomeTapeEntry, FetchFailureOutcomeTapeEntry)):
                raise OutcomeTapeError("outcome tape contains an unsupported entry")
            if _entry_membership(entry) != expected_membership:
                raise OutcomeTapeError("outcome tape entry belongs to a different evidence scope")
            if entry.request_ordinal != ordinal:
                raise OutcomeTapeError("outcome tape ordinals must be exactly 1..request_count")
        if outcome_records_sha256(self.entries) != self.scope.records_sha256:
            raise OutcomeTapeError("outcome tape digest does not match evidence scope")

    def as_payload(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_payload() for entry in self.entries],
            "schema_version": OUTCOME_TAPE_SCHEMA_VERSION,
        }

    @classmethod
    def from_payload(cls, scope: EvidenceScopeRef, payload: Any) -> OutcomeTape:
        if not isinstance(payload, dict) or set(payload) != {"entries", "schema_version"}:
            raise OutcomeTapeError("outcome tape payload fields do not match schema")
        if payload["schema_version"] != OUTCOME_TAPE_SCHEMA_VERSION:
            raise OutcomeTapeError("outcome tape schema version is unsupported")
        raw_entries = payload["entries"]
        if not isinstance(raw_entries, list):
            raise OutcomeTapeError("outcome tape entries must be an array")
        return cls(scope, (_entry_from_payload(item) for item in raw_entries))


class OutcomeTapeFetcher:
    """A strict, single-pass FetchClient over one validated outcome tape."""

    timeout = None

    def __init__(self, tape: OutcomeTape) -> None:
        if not isinstance(tape, OutcomeTape):
            raise TypeError("tape must be an OutcomeTape")
        self._tape = tape
        self._cursor = 0

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Page:
        identity = build_request_identity(url, data=data, headers=headers)
        if self._cursor >= len(self._tape.entries):
            raise _divergence("outcome tape received an extra request", identity)

        entry = self._tape.entries[self._cursor]
        self._cursor += 1
        if identity != entry.request:
            raise _divergence("outcome tape request does not match the next entry", identity)
        if isinstance(entry, PageOutcomeTapeEntry):
            return entry.to_page()
        raise entry.to_error()

    def finish(self) -> None:
        if self._cursor != len(self._tape.entries):
            raise _divergence("outcome tape has unconsumed entries")

    def remaining_fetch_seconds(self) -> float | None:
        return None


def outcome_records_sha256(entries: Iterable[OutcomeTapeEntry]) -> str:
    encoded = b"\n".join(
        json.dumps(
            entry.as_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        for entry in entries
    )
    return hashlib.sha256(encoded).hexdigest()


def _entry_from_payload(payload: Any) -> OutcomeTapeEntry:
    common_fields = {
        "capture_attempt_id",
        "execution_fingerprint",
        "kind",
        "request",
        "request_ordinal",
        "schema_version",
        "scope_id",
        "snapshot_store_id",
        "stage",
    }
    if not isinstance(payload, dict) or payload.get("kind") not in {"page", "fetch_failure"}:
        raise OutcomeTapeError("outcome tape entry kind is unsupported")
    detail_field = "page" if payload["kind"] == "page" else "failure"
    if set(payload) != common_fields | {detail_field}:
        raise OutcomeTapeError("outcome tape entry fields do not match schema")
    if payload["schema_version"] != OUTCOME_TAPE_SCHEMA_VERSION:
        raise OutcomeTapeError("outcome tape entry schema version is unsupported")
    try:
        request = request_identity_from_dict(payload["request"])
    except (TypeError, ValueError) as error:
        raise OutcomeTapeError(f"invalid outcome tape request identity: {error}") from error
    common = {
        "snapshot_store_id": payload["snapshot_store_id"],
        "scope_id": payload["scope_id"],
        "capture_attempt_id": payload["capture_attempt_id"],
        "execution_fingerprint": payload["execution_fingerprint"],
        "stage": payload["stage"],
        "request_ordinal": payload["request_ordinal"],
        "request": request,
    }
    detail = payload[detail_field]
    if payload["kind"] == "page":
        if not isinstance(detail, dict) or set(detail) != {
            "final_url",
            "html",
            "source",
            "url",
        }:
            raise OutcomeTapeError("page outcome fields do not match schema")
        return PageOutcomeTapeEntry(
            **common,
            page_url=detail["url"],
            html=detail["html"],
            final_url=detail["final_url"],
            source=detail["source"],
        )
    if not isinstance(detail, dict) or set(detail) != {
        "message",
        "reason_code",
        "retryable",
        "status",
        "taxonomy_version",
    }:
        raise OutcomeTapeError("fetch failure fields do not match schema")
    return FetchFailureOutcomeTapeEntry(**common, **detail)


def _validate_membership(entry: OutcomeTapeEntry) -> None:
    try:
        EvidenceScopeRef(
            snapshot_store_id=entry.snapshot_store_id,
            scope_id=entry.scope_id,
            capture_attempt_id=entry.capture_attempt_id,
            execution_fingerprint=entry.execution_fingerprint,
            stage=entry.stage,
            request_count=1,
            records_sha256="0" * 64,
            first_sequence=1,
            last_sequence=1,
        )
    except (TypeError, ValueError) as error:
        raise OutcomeTapeError(f"invalid outcome tape scope membership: {error}") from error
    if type(entry.request_ordinal) is not int or entry.request_ordinal <= 0:
        raise OutcomeTapeError("request_ordinal must be a positive integer")


def _validate_request(request: RequestIdentity) -> None:
    if not isinstance(request, RequestIdentity):
        raise TypeError("request must be a RequestIdentity")
    try:
        validated = request_identity_from_dict(request.as_dict())
    except ValueError as error:
        raise OutcomeTapeError(f"invalid request identity: {error}") from error
    if validated != request or not request.replayable:
        raise OutcomeTapeError("request identity is not safely replayable")


def _validate_sanitized_url(url: Any, field_name: str) -> None:
    if not isinstance(url, str) or not url or sanitize_url(url) != url:
        raise OutcomeTapeError(f"{field_name} is not a sanitized URL")


def _validate_source(source: Any) -> None:
    if (
        not isinstance(source, str)
        or not source
        or len(source) > 200
        or source.startswith(("/", "\\"))
        or "://" in source
        or sanitize_snapshot_body(source) != source
    ):
        raise OutcomeTapeError("page source is not privacy-safe")


def _membership_payload(entry: OutcomeTapeEntry) -> dict[str, Any]:
    return {
        "capture_attempt_id": entry.capture_attempt_id,
        "execution_fingerprint": entry.execution_fingerprint,
        "request_ordinal": entry.request_ordinal,
        "scope_id": entry.scope_id,
        "snapshot_store_id": entry.snapshot_store_id,
        "stage": entry.stage,
    }


def _entry_membership(entry: OutcomeTapeEntry) -> tuple[Any, ...]:
    return (
        entry.snapshot_store_id,
        entry.scope_id,
        entry.capture_attempt_id,
        entry.execution_fingerprint,
        entry.stage,
    )


def _scope_membership(scope: EvidenceScopeRef) -> tuple[Any, ...]:
    return (
        scope.snapshot_store_id,
        scope.scope_id,
        scope.capture_attempt_id,
        scope.execution_fingerprint,
        scope.stage,
    )


def _divergence(message: str, identity: RequestIdentity | None = None) -> FetchError:
    return FetchError(
        message,
        reason_code=OFFLINE_TAPE_DIVERGENCE,
        retryable=False,
        request_identity=identity.as_dict() if identity is not None else None,
    )
