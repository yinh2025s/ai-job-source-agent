from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping

import fcntl

from .candidate_reasoning_contracts import (
    LLM_DECISION_SCHEMA_VERSION,
    LLMDecisionKey,
    LLMDecisionRecord,
    TokenUsage,
    llm_decision_key_digest,
)


LLM_DECISION_STORE_SCHEMA_VERSION = "1"

LLM_DECISION_FIXTURE_MISSING = "LLM_DECISION_FIXTURE_MISSING"
LLM_DECISION_FIXTURE_INCOMPATIBLE = "LLM_DECISION_FIXTURE_INCOMPATIBLE"
LLM_DECISION_FIXTURE_CORRUPT = "LLM_DECISION_FIXTURE_CORRUPT"
LLM_DECISION_REPLAY_DIVERGENCE = "LLM_DECISION_REPLAY_DIVERGENCE"

_KEY_FIELDS = (
    "decision_kind",
    "normalized_company_identity_digest",
    "input_evidence_digest",
    "llm_provider",
    "model_id",
    "prompt_version",
    "decision_schema_version",
    "adapter_version",
)
_RECORD_FIELDS = {
    "schema_version",
    "record_key",
    "execution_fingerprint",
    "key",
    "sanitized_request",
    "sanitized_response",
    "candidate_ids",
    "query_ids",
    "candidate_evidence_digest",
    "duration_ms",
    "token_usage",
    "created_at_epoch",
    "status",
    "failure_code",
}
_TOKEN_FIELDS = {"prompt_tokens", "completion_tokens", "total_tokens"}
_ENVELOPE_FIELDS = {"schema_version", "record"}
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "browser_state",
        "cookie",
        "cookies",
        "exact_opening",
        "html",
        "job_board",
        "negative_terminal",
        "opening",
        "password",
        "raw_html",
        "raw_prompt",
        "raw_response",
        "refresh_token",
        "secret",
        "terminal_outcome",
        "token",
        "verified_website",
        "website",
    }
)


class LLMDecisionReplayError(ValueError):
    code = LLM_DECISION_REPLAY_DIVERGENCE

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class LLMDecisionFixtureMissing(LLMDecisionReplayError):
    code = LLM_DECISION_FIXTURE_MISSING


class LLMDecisionFixtureIncompatible(LLMDecisionReplayError):
    code = LLM_DECISION_FIXTURE_INCOMPATIBLE


class LLMDecisionFixtureCorrupt(LLMDecisionReplayError):
    code = LLM_DECISION_FIXTURE_CORRUPT


class LLMDecisionReplayDivergence(LLMDecisionReplayError):
    code = LLM_DECISION_REPLAY_DIVERGENCE


class LLMDecisionUnexpectedCall(LLMDecisionReplayDivergence):
    """Replay requested an LLM decision absent from the source call plan."""


class LLMDecisionFixturesUnconsumed(LLMDecisionReplayDivergence):
    """Replay ended before consuming every captured LLM decision."""


class FilesystemLLMDecisionStore:
    """Atomic live decision store; invalid records never authorize a candidate."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, key: LLMDecisionKey) -> LLMDecisionRecord | None:
        digest = llm_decision_key_digest(key)
        try:
            with self._key_lock(digest):
                path = self._record_path(digest)
                _reject_symlink(path)
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                record = _deserialize_envelope(payload)
                _require_compatible_record(record, key, digest)
                if record.status != "success" or key.decision_kind != "candidate_rank":
                    return None
                return record
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            return None

    def save(self, record: LLMDecisionRecord) -> None:
        if not isinstance(record, LLMDecisionRecord):
            raise TypeError("record must use LLMDecisionRecord")
        digest = llm_decision_key_digest(record.key)
        if record.record_key != digest:
            raise ValueError("record_key does not match the canonical decision key")
        payload = _serialize_envelope(record)
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._key_lock(digest):
            path = self._record_path(digest)
            _ensure_private_directory(path.parent)
            _reject_symlink(path)
            temporary_path: str | None = None
            try:
                descriptor, temporary_path = tempfile.mkstemp(
                    dir=path.parent,
                    prefix=f".{digest}.",
                    suffix=".tmp",
                    text=True,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, path)
                temporary_path = None
                _fsync_directory(path.parent)
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass

    def _record_path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.json"

    @contextmanager
    def _key_lock(self, digest: str) -> Iterator[None]:
        _ensure_private_directory(self.root)
        lock_directory = self.root / ".locks" / digest[:2]
        _ensure_private_directory(lock_directory)
        lock_path = lock_directory / f"{digest}.lock"
        _reject_symlink(lock_path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class StrictReplayLLMDecisionStore:
    """Single-pass fixture store. It cannot save or fall through to a real client."""

    def __init__(
        self,
        records: Iterable[LLMDecisionRecord],
        *,
        expected_keys: Iterable[LLMDecisionKey] | None = None,
    ) -> None:
        try:
            materialized = tuple(records)
            if any(not isinstance(record, LLMDecisionRecord) for record in materialized):
                raise TypeError("replay fixture contains an invalid record")
            by_digest: dict[str, LLMDecisionRecord] = {}
            for record in materialized:
                digest = llm_decision_key_digest(record.key)
                if record.record_key != digest or digest in by_digest:
                    raise ValueError("replay fixture key is invalid or duplicated")
                _serialize_envelope(record)
                by_digest[digest] = record
            expected = tuple(expected_keys) if expected_keys is not None else tuple(
                record.key for record in materialized
            )
            expected_by_digest = {llm_decision_key_digest(key): key for key in expected}
            if len(expected_by_digest) != len(expected):
                raise ValueError("expected replay decision keys are duplicated")
        except (TypeError, ValueError) as exc:
            raise LLMDecisionFixtureCorrupt(str(exc)) from exc
        self._records = MappingProxyType(by_digest)
        self._expected = MappingProxyType(expected_by_digest)
        self._consumed: set[str] = set()

    @classmethod
    def from_payloads(
        cls,
        payloads: Iterable[Any],
        *,
        expected_keys: Iterable[LLMDecisionKey] | None = None,
    ) -> StrictReplayLLMDecisionStore:
        try:
            records = tuple(_deserialize_envelope(payload) for payload in payloads)
        except (TypeError, ValueError) as exc:
            raise LLMDecisionFixtureCorrupt(str(exc)) from exc
        return cls(records, expected_keys=expected_keys)

    def load(self, key: LLMDecisionKey) -> LLMDecisionRecord:
        digest = llm_decision_key_digest(key)
        if digest not in self._expected:
            raise LLMDecisionUnexpectedCall("unexpected LLM decision request")
        if digest in self._consumed:
            raise LLMDecisionReplayDivergence("LLM decision fixture was consumed twice")
        record = self._records.get(digest)
        if record is None:
            if any(_same_replay_slot(candidate.key, key) for candidate in self._records.values()):
                raise LLMDecisionFixtureIncompatible("LLM decision fixture key is incompatible")
            raise LLMDecisionFixtureMissing("LLM decision fixture is missing")
        try:
            _require_compatible_record(record, key, digest)
        except ValueError as exc:
            raise LLMDecisionFixtureIncompatible(str(exc)) from exc
        self._consumed.add(digest)
        return record

    def save(self, record: LLMDecisionRecord) -> None:
        raise LLMDecisionReplayDivergence("replay store is read-only")

    def assert_consumed(self) -> None:
        unconsumed = set(self._records) - self._consumed
        missing = set(self._expected) - set(self._records)
        if missing:
            raise LLMDecisionFixtureMissing(
                f"{len(missing)} expected LLM decision fixture(s) are missing"
            )
        if unconsumed:
            raise LLMDecisionFixturesUnconsumed(
                f"{len(unconsumed)} LLM decision fixture(s) were not consumed"
            )


def _serialize_envelope(record: LLMDecisionRecord) -> dict[str, Any]:
    request = _thaw_json(record.sanitized_request)
    response = _thaw_json(record.sanitized_response)
    _reject_persisted_secrets(request)
    _reject_persisted_secrets(response)
    return {
        "schema_version": LLM_DECISION_STORE_SCHEMA_VERSION,
        "record": {
            "schema_version": record.schema_version,
            "record_key": record.record_key,
            "execution_fingerprint": record.execution_fingerprint,
            "key": _serialize_key(record.key),
            "sanitized_request": request,
            "sanitized_response": response,
            "candidate_ids": list(record.candidate_ids),
            "query_ids": list(record.query_ids),
            "candidate_evidence_digest": record.candidate_evidence_digest,
            "duration_ms": record.duration_ms,
            "token_usage": {
                "prompt_tokens": record.token_usage.prompt_tokens,
                "completion_tokens": record.token_usage.completion_tokens,
                "total_tokens": record.token_usage.total_tokens,
            },
            "created_at_epoch": record.created_at_epoch,
            "status": record.status,
            "failure_code": record.failure_code,
        },
    }


def _deserialize_envelope(payload: Any) -> LLMDecisionRecord:
    envelope = _exact_dict(payload, _ENVELOPE_FIELDS, "decision envelope")
    if envelope["schema_version"] != LLM_DECISION_STORE_SCHEMA_VERSION:
        raise ValueError("decision store schema version is incompatible")
    value = _exact_dict(envelope["record"], _RECORD_FIELDS, "decision record")
    key_payload = _exact_dict(value["key"], set(_KEY_FIELDS), "decision key")
    token_payload = _exact_dict(value["token_usage"], _TOKEN_FIELDS, "token usage")
    request = _exact_mapping(value["sanitized_request"], "sanitized_request")
    response = _exact_mapping(value["sanitized_response"], "sanitized_response")
    _reject_persisted_secrets(request)
    _reject_persisted_secrets(response)
    record = LLMDecisionRecord(
        schema_version=value["schema_version"],
        record_key=value["record_key"],
        execution_fingerprint=value["execution_fingerprint"],
        key=LLMDecisionKey(**key_payload),
        sanitized_request=request,
        sanitized_response=response,
        candidate_ids=_strict_string_tuple(value["candidate_ids"], "candidate_ids"),
        query_ids=_strict_string_tuple(value["query_ids"], "query_ids"),
        candidate_evidence_digest=value["candidate_evidence_digest"],
        duration_ms=value["duration_ms"],
        token_usage=TokenUsage(**token_payload),
        created_at_epoch=value["created_at_epoch"],
        status=value["status"],
        failure_code=value["failure_code"],
    )
    if record.schema_version != LLM_DECISION_SCHEMA_VERSION:
        raise ValueError("decision record schema version is incompatible")
    return record


def _serialize_key(key: LLMDecisionKey) -> dict[str, str]:
    return {field: getattr(key, field) for field in _KEY_FIELDS}


def _require_compatible_record(
    record: LLMDecisionRecord,
    key: LLMDecisionKey,
    digest: str,
) -> None:
    if record.key != key or record.record_key != digest:
        raise ValueError("stored LLM decision does not match its requested key")


def _same_replay_slot(left: LLMDecisionKey, right: LLMDecisionKey) -> bool:
    return (
        left.decision_kind == right.decision_kind
        and left.normalized_company_identity_digest
        == right.normalized_company_identity_digest
    )


def _exact_dict(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} contains missing or unknown fields")
    return value


def _exact_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _strict_string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string array")
    return tuple(value)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("decision payload is not JSON-compatible")


def _reject_persisted_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _FORBIDDEN_PERSISTED_KEYS:
                raise ValueError("decision payload contains forbidden persisted data")
            _reject_persisted_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_persisted_secrets(item)
    elif isinstance(value, float) and not (float("-inf") < value < float("inf")):
        raise ValueError("decision payload contains a non-finite number")


def _ensure_private_directory(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise OSError("decision store directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("decision store directory cannot be a symlink")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise OSError("decision store path cannot be a symlink")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


FileLLMDecisionStore = FilesystemLLMDecisionStore
ReplayLLMDecisionStore = StrictReplayLLMDecisionStore
