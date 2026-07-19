from __future__ import annotations

import ipaddress
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import fcntl

from .company_discovery_evidence import (
    COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
    DEFAULT_COMPANY_DISCOVERY_EVIDENCE_MAX_AGE_SECONDS,
    CareerEvidenceSource,
    CompanyDiscoveryEvidenceStore,
    EvidenceLayer,
    ProviderEvidenceSource,
    VerifiedCareerEvidence,
    VerifiedCompanyDiscoveryEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
    WebsiteEvidenceSource,
)
from .identity_evidence import (
    _evidence_key,
    _fsync_directory,
    _normalize_company_name,
    _normalize_linkedin_url,
)
from .web import safe_normalize_url


_WEBSITE_SOURCES = {
    "extension_official_website",
    "linkedin_official_website",
    "provided_website",
    "verified_resolver",
}
_CAREER_SOURCES = {
    "first_party_navigation",
    "provider_handoff",
    "verified_career_search",
}
_PROVIDER_SOURCES = {
    "external_apply_handoff",
    "first_party_handoff",
    "provider_page_identity",
}
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "code",
    "id_token",
    "key",
    "password",
    "refresh_token",
    "secret",
    "session",
    "sig",
    "signature",
    "state",
    "token",
}
_MAX_PROVIDER_BOARDS = 8
_DETERMINISTIC_WEBSITE_REJECTION_MARKERS = {
    "hosted non-company destination rejected",
    "parked domain rejected",
    "registrable domain does not establish company ownership",
}
_CURRENT_WEBSITE_IDENTITY_CONFIRMATION_MARKERS = {
    "homepage canonical confirms company identity",
    "homepage title confirms company identity",
    "homepage body confirms company identity",
    "linkedin company page identifies official website",
}


def stored_website_deterministically_rejected(trace: object, website_url: str) -> bool:
    """Return true only when current page evidence disproves a stored website identity."""

    target = safe_normalize_url(website_url)
    if not target or not isinstance(trace, dict):
        return False
    for failure in trace.get("fetch_errors", []):
        if (
            isinstance(failure, dict)
            and isinstance(failure.get("url"), str)
            and _same_public_url(failure["url"], target)
        ):
            return False
    for candidate in trace.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        reasons = candidate.get("reasons")
        if (
            not isinstance(candidate.get("url"), str)
            or not _same_public_url(candidate["url"], target)
            or not isinstance(reasons, list)
            or "candidate source: stored_verified_company_evidence" not in reasons
        ):
            continue
        normalized_reasons = {str(reason).casefold() for reason in reasons}
        if normalized_reasons & _CURRENT_WEBSITE_IDENTITY_CONFIRMATION_MARKERS:
            return False
        if normalized_reasons & _DETERMINISTIC_WEBSITE_REJECTION_MARKERS:
            return True
    return False


def _same_public_url(left: str, right: str) -> bool:
    normalized_left = safe_normalize_url(left)
    normalized_right = safe_normalize_url(right)
    return bool(
        normalized_left
        and normalized_right
        and normalized_left.rstrip("/") == normalized_right.rstrip("/")
    )


class FilesystemCompanyDiscoveryEvidenceStore(CompanyDiscoveryEvidenceStore):
    """Atomic store for public company discovery candidates that require revalidation."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_age_seconds: float = DEFAULT_COMPANY_DISCOVERY_EVIDENCE_MAX_AGE_SECONDS,
        clock=time.time,
    ) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self.clock = clock

    def load(
        self,
        company_name: str,
        linkedin_company_url: str,
    ) -> VerifiedCompanyDiscoveryEvidence | None:
        normalized_company = _normalize_company_name(company_name)
        normalized_linkedin = _normalize_linkedin_url(linkedin_company_url)
        key = _evidence_key(company_name, linkedin_company_url)
        with self._lock():
            raw = self._read_payload().get("records", {}).get(key)
        if not isinstance(raw, dict):
            return None
        if raw.get("company_name") != normalized_company:
            return None
        if raw.get("linkedin_company_url") != normalized_linkedin:
            return None
        now = float(self.clock())
        if not math.isfinite(now):
            return None
        website = self._load_website(raw.get("website"), now)
        career = self._load_career(raw.get("career"), now)
        boards = tuple(
            board
            for item in raw.get("provider_boards", [])
            if (board := self._load_provider_board(item, now)) is not None
        ) if isinstance(raw.get("provider_boards", []), list) else ()
        if website is None:
            career = None
            boards = ()
        elif career is None:
            boards = ()
        if website is None and career is None and not boards:
            return None
        return VerifiedCompanyDiscoveryEvidence(
            company_name=normalized_company,
            linkedin_company_url=normalized_linkedin,
            website=website,
            career=career,
            provider_boards=boards,
        )

    def save(
        self,
        company_name: str,
        linkedin_company_url: str,
        *,
        website: VerifiedWebsiteEvidence | None = None,
        career: VerifiedCareerEvidence | None = None,
        provider_board: VerifiedProviderBoardEvidence | None = None,
    ) -> None:
        if website is None and career is None and provider_board is None:
            return
        normalized_company = _normalize_company_name(company_name)
        normalized_linkedin = _normalize_linkedin_url(linkedin_company_url)
        key = _evidence_key(company_name, linkedin_company_url)
        serialized_website = self._serialize_website(website) if website else None
        serialized_career = self._serialize_career(career) if career else None
        serialized_board = (
            self._serialize_provider_board(provider_board) if provider_board else None
        )
        with self._lock():
            payload = self._read_payload()
            records = payload.setdefault("records", {})
            record = records.get(key)
            if not isinstance(record, dict):
                record = {
                    "company_name": normalized_company,
                    "linkedin_company_url": normalized_linkedin,
                    "provider_boards": [],
                }
            if serialized_website is not None:
                prior_website = record.get("website")
                if _layer_is_not_newer(prior_website, serialized_website):
                    prior_url = _layer_url(prior_website, "url")
                    if prior_url and prior_url != serialized_website["url"]:
                        record.pop("career", None)
                        record["provider_boards"] = []
                    record["website"] = serialized_website
            if serialized_career is not None:
                current_website_url = _layer_url(record.get("website"), "url")
                prior_career = record.get("career")
                if (
                    current_website_url == serialized_career["website_url"]
                    and _layer_is_not_newer(prior_career, serialized_career)
                ):
                    prior_url = _layer_url(prior_career, "url")
                    if prior_url and prior_url != serialized_career["url"]:
                        record["provider_boards"] = []
                    record["career"] = serialized_career
            if serialized_board is not None:
                existing = record.get("provider_boards")
                boards = existing if isinstance(existing, list) else []
                boards = [
                    item
                    for item in boards
                    if isinstance(item, dict)
                    and _safe_observed_at(item) is not None
                ]
                identity = _provider_identity(serialized_board)
                same_identity = [
                    item
                    for item in boards
                    if isinstance(item, dict) and _provider_identity(item) == identity
                ]
                if any(
                    not _layer_is_not_newer(item, serialized_board)
                    for item in same_identity
                ):
                    serialized_board = None
                boards = [
                    item
                    for item in boards
                    if serialized_board is None
                    or not isinstance(item, dict)
                    or _provider_identity(item) != identity
                ]
                if serialized_board is not None:
                    boards.append(serialized_board)
                boards.sort(
                    key=lambda item: _safe_observed_at(item) or 0.0,
                    reverse=True,
                )
                record["provider_boards"] = boards[:_MAX_PROVIDER_BOARDS]
            record["company_name"] = normalized_company
            record["linkedin_company_url"] = normalized_linkedin
            records[key] = record
            self._write_payload(payload)

    def invalidate(
        self,
        company_name: str,
        linkedin_company_url: str,
        *,
        layer: EvidenceLayer,
        evidence_url: str | None = None,
    ) -> None:
        key = _evidence_key(company_name, linkedin_company_url)
        normalized_evidence_url = (
            _normalize_public_url(evidence_url) if evidence_url is not None else None
        )
        with self._lock():
            payload = self._read_payload()
            records = payload.get("records", {})
            record = records.get(key) if isinstance(records, dict) else None
            if not isinstance(record, dict):
                return
            changed = False
            if layer == "website":
                if _matches_layer(record.get("website"), normalized_evidence_url):
                    record.pop("website", None)
                    record.pop("career", None)
                    record["provider_boards"] = []
                    changed = True
            elif layer == "career":
                if _matches_layer(record.get("career"), normalized_evidence_url):
                    record.pop("career", None)
                    record["provider_boards"] = []
                    changed = True
            elif layer == "provider_board":
                existing = record.get("provider_boards")
                boards = existing if isinstance(existing, list) else []
                retained = [
                    item
                    for item in boards
                    if not _matches_provider_board(item, normalized_evidence_url)
                ]
                changed = len(retained) != len(boards)
                record["provider_boards"] = retained
            else:
                raise ValueError(f"unknown evidence layer: {layer}")
            if changed:
                if not record.get("website"):
                    records.pop(key, None)
                else:
                    records[key] = record
                self._write_payload(payload)

    def _load_website(self, raw: object, now: float) -> VerifiedWebsiteEvidence | None:
        if not isinstance(raw, dict) or not self._fresh(raw, now):
            return None
        source = raw.get("source")
        if source not in _WEBSITE_SOURCES:
            return None
        try:
            return VerifiedWebsiteEvidence(
                url=_normalize_public_url(raw.get("url")),
                source=source,
                evidence_url=_normalize_public_url(raw.get("evidence_url")),
                observed_at=float(raw["observed_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _load_career(self, raw: object, now: float) -> VerifiedCareerEvidence | None:
        if not isinstance(raw, dict) or not self._fresh(raw, now):
            return None
        source = raw.get("source")
        if source not in _CAREER_SOURCES:
            return None
        try:
            return VerifiedCareerEvidence(
                url=_normalize_public_url(raw.get("url")),
                website_url=_normalize_public_url(raw.get("website_url")),
                source=source,
                evidence_url=_normalize_public_url(raw.get("evidence_url")),
                observed_at=float(raw["observed_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _load_provider_board(
        self,
        raw: object,
        now: float,
    ) -> VerifiedProviderBoardEvidence | None:
        if not isinstance(raw, dict) or not self._fresh(raw, now):
            return None
        source = raw.get("source")
        if source not in _PROVIDER_SOURCES:
            return None
        try:
            return VerifiedProviderBoardEvidence(
                provider=_require_text(raw.get("provider")),
                tenant=_require_text(raw.get("tenant")),
                canonical_board_url=_normalize_public_url(raw.get("canonical_board_url")),
                relationship_evidence_url=_normalize_public_url(
                    raw.get("relationship_evidence_url")
                ),
                verification_method=_require_text(raw.get("verification_method")),
                source=source,
                observed_at=float(raw["observed_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _fresh(self, raw: dict, now: float) -> bool:
        try:
            observed_at = float(raw["observed_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(observed_at):
            return False
        age = now - observed_at
        return 0 <= age <= self.max_age_seconds

    def _serialize_website(self, item: VerifiedWebsiteEvidence) -> dict:
        if item.source not in _WEBSITE_SOURCES:
            raise ValueError("unsupported website evidence source")
        return {
            "url": _normalize_public_url(item.url),
            "source": item.source,
            "evidence_url": _normalize_public_url(item.evidence_url),
            "observed_at": float(item.observed_at),
        }

    def _serialize_career(self, item: VerifiedCareerEvidence) -> dict:
        if item.source not in _CAREER_SOURCES:
            raise ValueError("unsupported career evidence source")
        return {
            "url": _normalize_public_url(item.url),
            "website_url": _normalize_public_url(item.website_url),
            "source": item.source,
            "evidence_url": _normalize_public_url(item.evidence_url),
            "observed_at": float(item.observed_at),
        }

    def _serialize_provider_board(self, item: VerifiedProviderBoardEvidence) -> dict:
        if item.source not in _PROVIDER_SOURCES:
            raise ValueError("unsupported provider evidence source")
        return {
            "provider": _require_text(item.provider),
            "tenant": _require_text(item.tenant),
            "canonical_board_url": _normalize_public_url(item.canonical_board_url),
            "relationship_evidence_url": _normalize_public_url(
                item.relationship_evidence_url
            ),
            "verification_method": _require_text(item.verification_method),
            "source": item.source,
            "observed_at": float(item.observed_at),
        }

    def _read_payload(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return _empty_payload()
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION
            or not isinstance(payload.get("records"), dict)
        ):
            return _empty_payload()
        return payload

    def _write_payload(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            _fsync_directory(self.path.parent)
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _normalize_public_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("evidence URL must be a string")
    if not value.strip() or any(ord(character) <= 32 or ord(character) == 127 for character in value):
        raise ValueError("evidence URL must not contain whitespace or control characters")
    normalized = safe_normalize_url(value)
    if normalized is None:
        raise ValueError("evidence URL must be an absolute HTTP(S) URL")
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("evidence URL has an invalid port") from exc
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("evidence URL must be public HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("evidence URL must not contain credentials")
    if port not in {None, 80, 443}:
        raise ValueError("evidence URL must use a standard HTTP(S) port")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("evidence URL must not use a private host")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("evidence URL must not use a private address")
    safe_query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _SENSITIVE_QUERY_KEYS
        ],
        doseq=True,
    )
    return urlunparse(parsed._replace(query=safe_query, fragment=""))


def _require_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence identity fields must not be empty")
    return value.strip()


def _empty_payload() -> dict:
    return {
        "schema_version": COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
        "records": {},
    }


def _layer_url(raw: object, key: str) -> str | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _provider_identity(raw: dict) -> tuple[str, str, str]:
    return (
        str(raw.get("provider") or "").casefold(),
        str(raw.get("tenant") or "").casefold(),
        str(raw.get("canonical_board_url") or ""),
    )


def _safe_observed_at(raw: dict) -> float | None:
    try:
        value = float(raw.get("observed_at"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _layer_is_not_newer(current: object, candidate: dict) -> bool:
    """Allow monotonic evidence updates while treating corrupt state as replaceable."""

    if not isinstance(current, dict):
        return True
    current_observed_at = _safe_observed_at(current)
    candidate_observed_at = _safe_observed_at(candidate)
    if candidate_observed_at is None:
        return False
    return current_observed_at is None or candidate_observed_at >= current_observed_at


def _matches_layer(raw: object, evidence_url: str | None) -> bool:
    if not isinstance(raw, dict):
        return False
    if evidence_url is None:
        return True
    return evidence_url in {raw.get("url"), raw.get("evidence_url")}


def _matches_provider_board(raw: object, evidence_url: str | None) -> bool:
    if not isinstance(raw, dict):
        return False
    if evidence_url is None:
        return True
    return evidence_url in {
        raw.get("canonical_board_url"),
        raw.get("relationship_evidence_url"),
    }
