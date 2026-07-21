from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar


CISA_PUBLIC_DOMAIN_CSV_URL = (
    "https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv"
)
PUBLIC_DOMAIN_REGISTRY_SOURCE = "cisa_get_gov_current_full_csv"
PUBLIC_DOMAIN_REGISTRY_SCHEMA_VERSION = "1.0"

_EXPECTED_COLUMNS = (
    "Domain name",
    "Domain type",
    "Organization name",
    "Suborganization name",
    "City",
    "State",
    "Security contact email",
)
_PUBLIC_COLUMNS = _EXPECTED_COLUMNS[:-1]
_CITY_DOMAIN_TYPE = "City"
_STATE_DOMAIN_TYPE = "State or territory"
_MAX_DATASET_BYTES = 32 * 1024 * 1024
_MAX_DATASET_ROWS = 100_000
_MAX_CANDIDATES = 8
_DEFAULT_CANDIDATE_LIMIT = 5
_DEFAULT_MAX_AGE = timedelta(days=2)
_MAX_FUTURE_SKEW = timedelta(minutes=5)

_DOMAIN_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+gov\Z"
)
_CITY_IDENTITY_RE = re.compile(r"^city of [a-z0-9](?:[a-z0-9 ]*[a-z0-9])?$")
_STATE_IDENTITY_RE = re.compile(r"^state of [a-z0-9](?:[a-z0-9 ]*[a-z0-9])?$")

_STATE_ABBREVIATIONS = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE_CODES = frozenset(_STATE_ABBREVIATIONS.values())


@dataclass(frozen=True)
class PublicDomainProvenance:
    schema_version: str
    source: str
    source_url: str
    dataset_sha256: str
    row_sha256: str
    row_number: int
    retrieved_at: str


@dataclass(frozen=True)
class PublicDomainCandidate:
    url: str
    domain_name: str
    organization_name: str
    domain_type: str
    city: str
    state: str
    provenance: PublicDomainProvenance


@dataclass(frozen=True)
class PublicDomainQueryResult:
    status: str
    candidates: tuple[PublicDomainCandidate, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class _RegistryRow:
    domain_name: str
    domain_type: str
    organization_name: str
    suborganization_name: str
    city: str
    state: str
    normalized_organization: str
    normalized_city: str
    row_sha256: str
    row_number: int


@dataclass(frozen=True)
class PublicDomainRegistry:
    """A privacy-minimized, fail-closed view of CISA's authoritative registry."""

    source_url: str
    dataset_sha256: str
    retrieved_at: datetime
    rows: tuple[_RegistryRow, ...] = ()
    load_error: str | None = None
    candidate_limit: int = _DEFAULT_CANDIDATE_LIMIT

    EXPECTED_COLUMNS: ClassVar[tuple[str, ...]] = _EXPECTED_COLUMNS
    SCHEMA_VERSION: ClassVar[str] = PUBLIC_DOMAIN_REGISTRY_SCHEMA_VERSION

    @property
    def available(self) -> bool:
        return self.load_error is None

    @classmethod
    def from_csv_bytes(
        cls,
        content: bytes,
        *,
        retrieved_at: datetime,
        now: datetime | None = None,
        source_url: str = CISA_PUBLIC_DOMAIN_CSV_URL,
        max_age: timedelta = _DEFAULT_MAX_AGE,
        candidate_limit: int = _DEFAULT_CANDIDATE_LIMIT,
    ) -> PublicDomainRegistry:
        now = now or datetime.now(timezone.utc)
        digest = hashlib.sha256(content).hexdigest() if isinstance(content, bytes) else ""

        configuration_error = _validate_configuration(
            source_url=source_url,
            retrieved_at=retrieved_at,
            now=now,
            max_age=max_age,
            candidate_limit=candidate_limit,
        )
        if configuration_error:
            return cls(
                source_url=source_url,
                dataset_sha256=digest,
                retrieved_at=retrieved_at,
                load_error=configuration_error,
                candidate_limit=candidate_limit,
            )

        if not isinstance(content, bytes) or not content:
            return cls._unavailable(
                source_url,
                digest,
                retrieved_at,
                candidate_limit,
                "empty_dataset",
            )
        if len(content) > _MAX_DATASET_BYTES:
            return cls._unavailable(
                source_url,
                digest,
                retrieved_at,
                candidate_limit,
                "dataset_too_large",
            )
        if b"\x00" in content:
            return cls._unavailable(
                source_url,
                digest,
                retrieved_at,
                candidate_limit,
                "malformed_csv",
            )

        try:
            text = content.decode("utf-8-sig", errors="strict")
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if tuple(reader.fieldnames or ()) != _EXPECTED_COLUMNS:
                return cls._unavailable(
                    source_url,
                    digest,
                    retrieved_at,
                    candidate_limit,
                    "schema_mismatch",
                )

            parsed_rows: list[_RegistryRow] = []
            seen_domains: set[str] = set()
            for row_number, raw in enumerate(reader, start=2):
                if len(parsed_rows) >= _MAX_DATASET_ROWS:
                    return cls._unavailable(
                        source_url,
                        digest,
                        retrieved_at,
                        candidate_limit,
                        "row_limit_exceeded",
                    )
                if None in raw or any(raw.get(column) is None for column in _EXPECTED_COLUMNS):
                    return cls._unavailable(
                        source_url,
                        digest,
                        retrieved_at,
                        candidate_limit,
                        "malformed_csv",
                    )
                public_values = {column: raw[column].strip() for column in _PUBLIC_COLUMNS}
                domain_name = public_values["Domain name"].casefold()
                if not _valid_registry_domain(domain_name):
                    return cls._unavailable(
                        source_url,
                        digest,
                        retrieved_at,
                        candidate_limit,
                        "invalid_domain",
                    )
                if domain_name in seen_domains:
                    return cls._unavailable(
                        source_url,
                        digest,
                        retrieved_at,
                        candidate_limit,
                        "domain_collision",
                    )
                if not public_values["Domain type"] or not public_values["Organization name"]:
                    return cls._unavailable(
                        source_url,
                        digest,
                        retrieved_at,
                        candidate_limit,
                        "malformed_row",
                    )
                seen_domains.add(domain_name)
                parsed_rows.append(_build_row(public_values, domain_name, row_number))
        except (csv.Error, UnicodeDecodeError):
            return cls._unavailable(
                source_url,
                digest,
                retrieved_at,
                candidate_limit,
                "malformed_csv",
            )

        if not parsed_rows:
            return cls._unavailable(
                source_url,
                digest,
                retrieved_at,
                candidate_limit,
                "empty_dataset",
            )
        return cls(
            source_url=source_url,
            dataset_sha256=digest,
            retrieved_at=retrieved_at,
            rows=tuple(parsed_rows),
            candidate_limit=candidate_limit,
        )

    @classmethod
    def _unavailable(
        cls,
        source_url: str,
        dataset_sha256: str,
        retrieved_at: datetime,
        candidate_limit: int,
        reason: str,
    ) -> PublicDomainRegistry:
        return cls(
            source_url=source_url,
            dataset_sha256=dataset_sha256,
            retrieved_at=retrieved_at,
            load_error=reason,
            candidate_limit=candidate_limit,
        )

    def query(
        self,
        organization_name: str,
        *,
        state: str,
        city: str | None = None,
    ) -> PublicDomainQueryResult:
        if not self.available:
            return PublicDomainQueryResult("unavailable", reason=self.load_error)

        normalized_organization = _normalize_text(organization_name)
        normalized_state = _normalize_state(state)
        identity_type = _government_identity_type(normalized_organization)
        if identity_type is None or normalized_state is None:
            return PublicDomainQueryResult(
                "unsupported_identity",
                reason="invalid_government_identity",
            )

        normalized_city = _normalize_text(city or "")
        if identity_type == _CITY_DOMAIN_TYPE and not normalized_city:
            return PublicDomainQueryResult("unsupported_identity", reason="city_required")

        matching_rows = [
            row
            for row in self.rows
            if row.normalized_organization == normalized_organization
            and row.domain_type.casefold() == identity_type.casefold()
            and _normalize_state(row.state) == normalized_state
            and (
                identity_type != _CITY_DOMAIN_TYPE
                or row.normalized_city == normalized_city
            )
        ]
        matching_rows.sort(key=lambda row: row.domain_name)
        if not matching_rows:
            return PublicDomainQueryResult("not_found", reason="no_exact_registry_match")
        if len(matching_rows) > self.candidate_limit:
            return PublicDomainQueryResult("ambiguous", reason="candidate_limit_exceeded")

        retrieved_at = (
            self.retrieved_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        candidates = tuple(
            PublicDomainCandidate(
                url=f"https://{row.domain_name}/",
                domain_name=row.domain_name,
                organization_name=row.organization_name,
                domain_type=row.domain_type,
                city=row.city,
                state=row.state,
                provenance=PublicDomainProvenance(
                    schema_version=PUBLIC_DOMAIN_REGISTRY_SCHEMA_VERSION,
                    source=PUBLIC_DOMAIN_REGISTRY_SOURCE,
                    source_url=self.source_url,
                    dataset_sha256=self.dataset_sha256,
                    row_sha256=row.row_sha256,
                    row_number=row.row_number,
                    retrieved_at=retrieved_at,
                ),
            )
            for row in matching_rows
        )
        return PublicDomainQueryResult("candidates", candidates=candidates)


def _validate_configuration(
    *,
    source_url: str,
    retrieved_at: datetime,
    now: datetime,
    max_age: timedelta,
    candidate_limit: int,
) -> str | None:
    if source_url != CISA_PUBLIC_DOMAIN_CSV_URL:
        return "untrusted_source"
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        return "invalid_retrieval_time"
    if not isinstance(now, datetime) or now.tzinfo is None:
        return "invalid_clock"
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        return "invalid_max_age"
    if not isinstance(candidate_limit, int) or isinstance(candidate_limit, bool):
        return "invalid_candidate_limit"
    if not 1 <= candidate_limit <= _MAX_CANDIDATES:
        return "invalid_candidate_limit"

    retrieved_utc = retrieved_at.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    if retrieved_utc > now_utc + _MAX_FUTURE_SKEW:
        return "future_dataset"
    if now_utc - retrieved_utc > max_age:
        return "stale_dataset"
    return None


def _build_row(values: dict[str, str], domain_name: str, row_number: int) -> _RegistryRow:
    public_record = {column: values[column] for column in _PUBLIC_COLUMNS}
    public_record["Domain name"] = domain_name
    encoded = json.dumps(
        public_record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _RegistryRow(
        domain_name=domain_name,
        domain_type=values["Domain type"],
        organization_name=values["Organization name"],
        suborganization_name=values["Suborganization name"],
        city=values["City"],
        state=values["State"],
        normalized_organization=_normalize_text(values["Organization name"]),
        normalized_city=_normalize_text(values["City"]),
        row_sha256=hashlib.sha256(encoded).hexdigest(),
        row_number=row_number,
    )


def _normalize_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in normalized).split())


def _normalize_state(value: str) -> str | None:
    normalized = _normalize_text(value)
    if len(normalized) == 2 and normalized.upper() in _STATE_CODES:
        return normalized.upper()
    return _STATE_ABBREVIATIONS.get(normalized)


def _government_identity_type(normalized_organization: str) -> str | None:
    if _CITY_IDENTITY_RE.fullmatch(normalized_organization):
        return _CITY_DOMAIN_TYPE
    if _STATE_IDENTITY_RE.fullmatch(normalized_organization):
        return _STATE_DOMAIN_TYPE
    return None


def _valid_registry_domain(domain_name: str) -> bool:
    if not domain_name or domain_name != domain_name.strip():
        return False
    try:
        domain_name.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        return False
    return _DOMAIN_RE.fullmatch(domain_name) is not None
