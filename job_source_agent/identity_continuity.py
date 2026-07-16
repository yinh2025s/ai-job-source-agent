from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .result_identity import canonicalize_identity_url


IDENTITY_CONTRACT_VERSION = "1.1"

_RELATIONSHIP_TYPES = {
    "same_entity",
    "brand_parent",
    "acquired_brand",
    "alternate_employer",
    "input_asserted",
}
_VERIFICATION_METHOD = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RELATIONSHIP_EVIDENCE_TYPES = {
    "first_party_handoff",
    "linkedin_external_apply",
    "provider_tenant_match",
    "unverified_candidate",
}


@dataclass(frozen=True)
class HiringIdentityEvidence:
    source_company_name: str
    hiring_entity_name: str
    relationship_type: str
    verification_method: str
    verified: bool
    evidence_url: str | None = None
    schema_version: str = IDENTITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_name(self.source_company_name, "source company")
        _validate_name(self.hiring_entity_name, "hiring entity")
        if self.relationship_type not in _RELATIONSHIP_TYPES:
            raise ValueError("Unsupported hiring relationship type")
        _validate_method(self.verification_method)
        if not isinstance(self.verified, bool):
            raise TypeError("Hiring relationship verification must be boolean")
        _validate_optional_url(self.evidence_url)
        _validate_schema(self.schema_version)

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return _strict_payload(self)

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> HiringIdentityEvidence:
        return cls(**_validated_payload(payload, cls))


@dataclass(frozen=True)
class HiringRelationshipEvidence:
    """Candidate-scoped proof that one provider tenant recruits for an entity."""

    source_company_name: str
    hiring_entity_name: str
    provider: str
    tenant: str
    evidence_type: str
    evidence_url: str
    strength: int
    verified: bool
    schema_version: str = IDENTITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_name(self.source_company_name, "source company")
        _validate_name(self.hiring_entity_name, "hiring entity")
        _validate_provider(self.provider)
        _validate_tenant(self.tenant)
        if self.evidence_type not in _RELATIONSHIP_EVIDENCE_TYPES:
            raise ValueError("Unsupported hiring relationship evidence type")
        _validate_url(self.evidence_url, "hiring relationship evidence")
        if (
            isinstance(self.strength, bool)
            or not isinstance(self.strength, int)
            or not 0 <= self.strength <= 100
        ):
            raise ValueError("Hiring relationship strength is invalid")
        if not isinstance(self.verified, bool):
            raise TypeError("Hiring relationship verification must be boolean")
        if self.verified != (self.strength >= 80):
            raise ValueError("Hiring relationship strength conflicts with verification")
        _validate_schema(self.schema_version)

    def to_trace_payload(self) -> dict[str, Any]:
        return _strict_payload(self)


@dataclass(frozen=True)
class ProviderIdentity:
    hiring_entity_name: str
    provider: str
    tenant: str
    canonical_board_url: str
    evidence_url: str
    verification_method: str
    relationship_verified: bool
    schema_version: str = IDENTITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_name(self.hiring_entity_name, "hiring entity")
        _validate_provider(self.provider)
        _validate_tenant(self.tenant)
        _validate_url(self.canonical_board_url, "canonical board")
        _validate_url(self.evidence_url, "provider evidence")
        _validate_method(self.verification_method)
        if not isinstance(self.relationship_verified, bool):
            raise TypeError("Provider relationship verification must be boolean")
        _validate_schema(self.schema_version)

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return _strict_payload(self)

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> ProviderIdentity:
        return cls(**_validated_payload(payload, cls))


@dataclass(frozen=True)
class OpeningIdentity:
    hiring_entity_name: str
    provider: str
    tenant: str
    canonical_board_url: str
    canonical_opening_url: str
    schema_version: str = IDENTITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_name(self.hiring_entity_name, "hiring entity")
        _validate_provider(self.provider)
        _validate_tenant(self.tenant)
        _validate_url(self.canonical_board_url, "canonical board")
        _validate_url(self.canonical_opening_url, "canonical opening")
        _validate_schema(self.schema_version)

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return _strict_payload(self)

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> OpeningIdentity:
        return cls(**_validated_payload(payload, cls))


@dataclass(frozen=True)
class OpeningSelectionEvidence:
    provider: str
    tenant: str
    canonical_board_url: str
    canonical_opening_url: str
    title: str
    location: str | None
    inventory_scope: str
    inventory_complete: bool
    candidate_count: int
    schema_version: str = IDENTITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_provider(self.provider)
        _validate_tenant(self.tenant)
        _validate_url(self.canonical_board_url, "selection board")
        _validate_url(self.canonical_opening_url, "selection opening")
        _validate_bounded_text(self.title, "selection title", required=True)
        _validate_bounded_text(self.location, "selection location")
        if self.inventory_scope not in {"full", "title_filtered", "unknown"}:
            raise ValueError("Unsupported opening inventory scope")
        if not isinstance(self.inventory_complete, bool):
            raise TypeError("Opening inventory completeness must be boolean")
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count < 0
        ):
            raise ValueError("Opening candidate count is invalid")
        _validate_schema(self.schema_version)

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return _strict_payload(self)

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> OpeningSelectionEvidence:
        return cls(**_validated_payload(payload, cls))


def validate_opening_identity_chain(
    *,
    hiring: HiringIdentityEvidence | None,
    provider: ProviderIdentity | None,
    opening: OpeningIdentity | None,
    open_position_url: str | None,
    job_list_page_url: str | None = None,
) -> list[str]:
    """Return stable failures for verified board and exact-opening identity."""

    if not job_list_page_url and not open_position_url:
        return []
    failures: list[str] = []
    if hiring is None:
        failures.append("HIRING_IDENTITY_MISSING")
    elif not hiring.verified:
        failures.append("HIRING_RELATIONSHIP_UNVERIFIED")
    if provider is None:
        failures.append("PROVIDER_IDENTITY_MISSING")
    elif not provider.relationship_verified:
        failures.append("PROVIDER_RELATIONSHIP_UNVERIFIED")
    if open_position_url and opening is None:
        failures.append("OPENING_IDENTITY_MISSING")
    if hiring is None or provider is None:
        return failures

    if _name_key(hiring.hiring_entity_name) != _name_key(provider.hiring_entity_name):
        failures.append("HIRING_PROVIDER_ENTITY_MISMATCH")
    if not open_position_url:
        return failures
    if opening is None:
        return failures
    if _name_key(provider.hiring_entity_name) != _name_key(opening.hiring_entity_name):
        failures.append("PROVIDER_OPENING_ENTITY_MISMATCH")
    if provider.provider != opening.provider:
        failures.append("OPENING_PROVIDER_MISMATCH")
    if provider.tenant != opening.tenant:
        failures.append("OPENING_TENANT_MISMATCH")
    if provider.canonical_board_url != opening.canonical_board_url:
        failures.append("OPENING_BOARD_MISMATCH")
    try:
        canonical_output = canonicalize_identity_url(open_position_url)
    except ValueError:
        failures.append("OPENING_URL_INVALID")
    else:
        if canonical_output != opening.canonical_opening_url:
            failures.append("OPENING_URL_MISMATCH")
    return failures


def _strict_payload(value: object) -> dict[str, Any]:
    return {
        name: getattr(value, name)
        for name in value.__dataclass_fields__
    }


def _validated_payload(payload: Any, cls: type) -> dict[str, Any]:
    expected = set(cls.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("Identity checkpoint payload has unsupported fields")
    return dict(payload)


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not " ".join(value.split()) or len(value) > 300:
        raise ValueError(f"Invalid {label} name")


def _validate_bounded_text(
    value: str | None,
    label: str,
    *,
    required: bool = False,
) -> None:
    if value is None:
        if required:
            raise ValueError(f"Invalid {label}")
        return
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 500
        or " ".join(value.split()) != value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"Invalid {label}")


def _validate_provider(value: str) -> None:
    if not isinstance(value, str) or not _PROVIDER.fullmatch(value):
        raise ValueError("Invalid provider identity")


def _validate_tenant(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 65_536
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Invalid provider tenant identity")


def _validate_method(value: str) -> None:
    if not isinstance(value, str) or not _VERIFICATION_METHOD.fullmatch(value):
        raise ValueError("Invalid identity verification method")


def _validate_url(value: str, label: str) -> None:
    if canonicalize_identity_url(value) != value:
        raise ValueError(f"{label.title()} URL must be canonical")


def _validate_optional_url(value: str | None) -> None:
    if value is not None:
        _validate_url(value, "evidence")


def _validate_schema(value: str) -> None:
    if value != IDENTITY_CONTRACT_VERSION:
        raise ValueError("Identity contract schema is incompatible")


def _name_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
