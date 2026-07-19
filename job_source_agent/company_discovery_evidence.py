from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_COMPANY_DISCOVERY_EVIDENCE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

WebsiteEvidenceSource = Literal[
    "extension_official_website",
    "linkedin_official_website",
    "provided_website",
    "verified_resolver",
]
CareerEvidenceSource = Literal[
    "first_party_navigation",
    "provider_handoff",
    "verified_career_search",
]
ProviderEvidenceSource = Literal[
    "external_apply_handoff",
    "first_party_handoff",
    "provider_page_identity",
]
EvidenceLayer = Literal["website", "career", "provider_board"]


@dataclass(frozen=True)
class VerifiedWebsiteEvidence:
    url: str
    source: WebsiteEvidenceSource
    evidence_url: str
    observed_at: float

    def __post_init__(self) -> None:
        _require_timestamp(self.observed_at)


@dataclass(frozen=True)
class VerifiedCareerEvidence:
    url: str
    website_url: str
    source: CareerEvidenceSource
    evidence_url: str
    observed_at: float

    def __post_init__(self) -> None:
        _require_timestamp(self.observed_at)


@dataclass(frozen=True)
class VerifiedProviderBoardEvidence:
    provider: str
    tenant: str
    canonical_board_url: str
    relationship_evidence_url: str
    verification_method: str
    source: ProviderEvidenceSource
    observed_at: float

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.tenant.strip():
            raise ValueError("tenant must not be empty")
        if not self.verification_method.strip():
            raise ValueError("verification_method must not be empty")
        _require_timestamp(self.observed_at)


@dataclass(frozen=True)
class VerifiedCompanyDiscoveryEvidence:
    company_name: str
    linkedin_company_url: str
    website: VerifiedWebsiteEvidence | None = None
    career: VerifiedCareerEvidence | None = None
    provider_boards: tuple[VerifiedProviderBoardEvidence, ...] = ()


@runtime_checkable
class CompanyDiscoveryEvidenceStore(Protocol):
    def load(
        self,
        company_name: str,
        linkedin_company_url: str,
    ) -> VerifiedCompanyDiscoveryEvidence | None:
        ...

    def save(
        self,
        company_name: str,
        linkedin_company_url: str,
        *,
        website: VerifiedWebsiteEvidence | None = None,
        career: VerifiedCareerEvidence | None = None,
        provider_board: VerifiedProviderBoardEvidence | None = None,
    ) -> None:
        """Atomically merge independently verified public evidence layers."""

    def invalidate(
        self,
        company_name: str,
        linkedin_company_url: str,
        *,
        layer: EvidenceLayer,
        evidence_url: str | None = None,
    ) -> None:
        """Invalidate one identity-matched layer and any dependent descendants."""


def _require_timestamp(value: float) -> None:
    if not math.isfinite(value):
        raise ValueError("observed_at must be finite")
