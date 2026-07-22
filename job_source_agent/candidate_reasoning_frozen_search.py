"""Immutable query-response fixtures for candidate-reasoning search experiments.

The fixture key intentionally contains only the planner's public query text and
purpose.  It never carries evaluator labels, reference URLs, or a runtime
``query_id``.  A replay therefore tests identical source evidence while still
allowing the caller to use its own trace-safe query identifier.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .candidate_reasoning_contracts import CandidateEvidence, SearchQuerySpec
from .candidate_reasoning_coordinator import CompanySearchBackend


FROZEN_QUERY_RESPONSE_SCHEMA_VERSION = "1"
_DIGEST_LENGTH = 64


class FrozenQueryFixtureError(RuntimeError):
    """A frozen query fixture is missing, malformed, or inconsistent."""


def frozen_query_digest(query: SearchQuerySpec) -> str:
    """Return the stable key for public query text and purpose only."""
    if not isinstance(query, SearchQuerySpec):
        raise TypeError("query must use SearchQuerySpec")
    payload = {"purpose": query.purpose, "query": query.query}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class FrozenCandidateEvidence:
    """Candidate data independent of the caller's runtime query identifier."""

    candidate_id: str
    url: str
    title: str
    snippet: str
    source: str
    rank: int

    def __post_init__(self) -> None:
        # CandidateEvidence owns all public-data validation, including HTTPS.
        CandidateEvidence(
            self.candidate_id,
            self.url,
            self.title,
            self.snippet,
            self.source,
            "frozen-query",
            self.rank,
        )

    @classmethod
    def from_candidate(cls, candidate: CandidateEvidence) -> FrozenCandidateEvidence:
        if not isinstance(candidate, CandidateEvidence):
            raise TypeError("search response must contain CandidateEvidence")
        return cls(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            title=candidate.title,
            snippet=candidate.snippet,
            source=candidate.source,
            rank=candidate.rank,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> FrozenCandidateEvidence:
        value = _exact_object(
            payload,
            {"candidate_id", "url", "title", "snippet", "source", "rank"},
            "frozen candidate",
        )
        return cls(**value)

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "snippet": self.snippet,
            "source": self.source,
            "title": self.title,
            "url": self.url,
        }

    def materialize(self, query_id: str) -> CandidateEvidence:
        return CandidateEvidence(
            self.candidate_id,
            self.url,
            self.title,
            self.snippet,
            self.source,
            query_id,
            self.rank,
        )


@dataclass(frozen=True)
class FrozenQueryResponse:
    """Schema-versioned source response keyed by a canonical query digest."""

    query: SearchQuerySpec
    candidates: tuple[FrozenCandidateEvidence, ...]
    query_digest: str
    schema_version: str = FROZEN_QUERY_RESPONSE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FROZEN_QUERY_RESPONSE_SCHEMA_VERSION:
            raise ValueError("unsupported frozen query response schema version")
        if not isinstance(self.query, SearchQuerySpec):
            raise TypeError("query must use SearchQuerySpec")
        if not isinstance(self.candidates, tuple):
            raise TypeError("candidates must be a tuple")
        if len(self.candidates) > 10:
            raise ValueError("frozen query response exceeds 10 candidates")
        if not all(isinstance(item, FrozenCandidateEvidence) for item in self.candidates):
            raise TypeError("candidates must contain FrozenCandidateEvidence")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("frozen query response contains duplicate candidate IDs")
        if len({item.rank for item in self.candidates}) != len(self.candidates):
            raise ValueError("frozen query response contains duplicate ranks")
        expected = frozen_query_digest(self.query)
        if not isinstance(self.query_digest, str) or len(self.query_digest) != _DIGEST_LENGTH:
            raise ValueError("query_digest must be a SHA-256 digest")
        if self.query_digest != expected:
            raise ValueError("query_digest does not match query")

    @classmethod
    def capture(
        cls,
        query: SearchQuerySpec,
        *,
        query_id: str,
        candidates: tuple[CandidateEvidence, ...],
    ) -> FrozenQueryResponse:
        _validate_response(query, query_id=query_id, candidates=candidates)
        return cls(
            query=query,
            candidates=tuple(FrozenCandidateEvidence.from_candidate(item) for item in candidates),
            query_digest=frozen_query_digest(query),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> FrozenQueryResponse:
        value = _exact_object(
            payload,
            {"schema_version", "query", "query_digest", "candidates"},
            "frozen query response",
        )
        if not isinstance(value["candidates"], list):
            raise FrozenQueryFixtureError("frozen query response candidates must be a list")
        try:
            return cls(
                schema_version=value["schema_version"],
                query=SearchQuerySpec.from_payload(value["query"]),
                query_digest=value["query_digest"],
                candidates=tuple(FrozenCandidateEvidence.from_payload(item) for item in value["candidates"]),
            )
        except (TypeError, ValueError) as error:
            raise FrozenQueryFixtureError(f"invalid frozen query response: {error}") from error

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidates": [item.to_payload() for item in self.candidates],
            "query": {"purpose": self.query.purpose, "query": self.query.query},
            "query_digest": self.query_digest,
            "schema_version": self.schema_version,
        }

    def materialize(self, query_id: str) -> tuple[CandidateEvidence, ...]:
        return tuple(item.materialize(query_id) for item in self.candidates)


class FilesystemFrozenQueryStore:
    """Strict, atomic, content-addressed store for frozen public search results."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        if root.is_symlink():
            raise FrozenQueryFixtureError("frozen query store root cannot be a symlink")
        self._root = root

    def load(self, query: SearchQuerySpec) -> FrozenQueryResponse | None:
        digest = frozen_query_digest(query)
        path = self._path_for_digest(digest)
        if not path.exists():
            return None
        return self._read(path, expected_digest=digest)

    def save(self, response: FrozenQueryResponse) -> None:
        if not isinstance(response, FrozenQueryResponse):
            raise TypeError("response must use FrozenQueryResponse")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path_for_digest(response.query_digest)
        payload = _canonical_json(response.to_payload()) + b"\n"

        if path.exists():
            self._assert_same_existing(path, response)
            return

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{response.query_digest}.", suffix=".tmp", dir=self._root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                self._assert_same_existing(path, response)
            else:
                _fsync_directory(self._root)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def available_digests(self) -> frozenset[str]:
        if not self._root.exists():
            return frozenset()
        digests: set[str] = set()
        for path in self._root.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise FrozenQueryFixtureError(
                    f"unexpected frozen query fixture entry: {path.name}"
                )
            digest = path.stem
            if len(digest) != _DIGEST_LENGTH or any(char not in "0123456789abcdef" for char in digest):
                raise FrozenQueryFixtureError(f"invalid frozen query fixture filename: {path.name}")
            self._read(path, expected_digest=digest)
            digests.add(digest)
        return frozenset(digests)

    def _assert_same_existing(self, path: Path, response: FrozenQueryResponse) -> None:
        existing = self._read(path, expected_digest=response.query_digest)
        if existing.to_payload() != response.to_payload():
            raise FrozenQueryFixtureError("duplicate frozen query has a different payload")

    def _path_for_digest(self, digest: str) -> Path:
        return self._root / f"{digest}.json"

    def _read(self, path: Path, *, expected_digest: str) -> FrozenQueryResponse:
        if path.is_symlink() or not path.is_file():
            raise FrozenQueryFixtureError("frozen query fixture must be a regular file")
        try:
            raw = path.read_bytes()
            decoded = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FrozenQueryFixtureError(f"cannot read frozen query fixture {path.name}") from error
        response = FrozenQueryResponse.from_payload(decoded)
        if response.query_digest != expected_digest:
            raise FrozenQueryFixtureError("frozen query fixture digest does not match filename")
        if _canonical_json(response.to_payload()) + b"\n" != raw:
            raise FrozenQueryFixtureError("frozen query fixture is not deterministically serialized")
        return response


class RecordingFrozenCandidateSearchBackend:
    """Capture live source responses once, then reuse the exact frozen result."""

    def __init__(self, backend: CompanySearchBackend, store: FilesystemFrozenQueryStore) -> None:
        if not isinstance(backend, CompanySearchBackend):
            raise TypeError("backend must implement CompanySearchBackend")
        if not isinstance(store, FilesystemFrozenQueryStore):
            raise TypeError("store must use FilesystemFrozenQueryStore")
        self._backend = backend
        self._store = store

    def search(
        self,
        query: SearchQuerySpec,
        *,
        query_id: str,
        remaining_seconds: float,
    ) -> tuple[CandidateEvidence, ...]:
        existing = self._store.load(query)
        if existing is not None:
            return existing.materialize(query_id)
        _validate_remaining_seconds(remaining_seconds)
        candidates = self._backend.search(
            query,
            query_id=query_id,
            remaining_seconds=remaining_seconds,
        )
        response = FrozenQueryResponse.capture(query, query_id=query_id, candidates=candidates)
        self._store.save(response)
        return response.materialize(query_id)


class ReplayFrozenCandidateSearchBackend:
    """Serve only recorded source responses and make fixture drift explicit."""

    def __init__(self, store: FilesystemFrozenQueryStore) -> None:
        if not isinstance(store, FilesystemFrozenQueryStore):
            raise TypeError("store must use FilesystemFrozenQueryStore")
        self._store = store
        self._available_digests = store.available_digests()
        self._consumed_digests: set[str] = set()
        self._missing_digests: set[str] = set()

    @property
    def consumed_query_digests(self) -> frozenset[str]:
        return frozenset(self._consumed_digests)

    @property
    def missing_query_digests(self) -> frozenset[str]:
        return frozenset(self._missing_digests)

    def search(
        self,
        query: SearchQuerySpec,
        *,
        query_id: str,
        remaining_seconds: float,
    ) -> tuple[CandidateEvidence, ...]:
        _validate_remaining_seconds(remaining_seconds)
        digest = frozen_query_digest(query)
        response = self._store.load(query)
        if response is None:
            self._missing_digests.add(digest)
            raise FrozenQueryFixtureError(f"missing frozen query fixture: {digest}")
        self._consumed_digests.add(digest)
        return response.materialize(query_id)

    def assert_all_consumed(self) -> None:
        if self._missing_digests:
            raise FrozenQueryFixtureError("missing frozen query fixtures were requested")
        unconsumed = self._available_digests - self._consumed_digests
        if unconsumed:
            raise FrozenQueryFixtureError("unconsumed frozen query fixtures remain")


def _validate_response(
    query: SearchQuerySpec,
    *,
    query_id: str,
    candidates: tuple[CandidateEvidence, ...],
) -> None:
    if not isinstance(query, SearchQuerySpec):
        raise TypeError("query must use SearchQuerySpec")
    if not isinstance(candidates, tuple):
        raise TypeError("search response must be a tuple")
    if len(candidates) > 10:
        raise ValueError("search response exceeds 10 candidates")
    if not all(isinstance(item, CandidateEvidence) for item in candidates):
        raise TypeError("search response must contain CandidateEvidence")
    if any(item.query_id != query_id for item in candidates):
        raise ValueError("search response candidate query_id does not match request")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("search response contains duplicate candidate IDs")
    if len({item.rank for item in candidates}) != len(candidates):
        raise ValueError("search response contains duplicate ranks")


def _validate_remaining_seconds(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise TimeoutError("candidate reasoning deadline exhausted")


def _exact_object(payload: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise FrozenQueryFixtureError(f"{label} has an invalid schema")
    return payload


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
