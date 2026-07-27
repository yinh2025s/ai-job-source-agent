from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .result_identity import canonicalize_identity_url


PROVISIONAL_WEBSITE_EVIDENCE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ProvisionalWebsiteEvidence:
    """A verified official site that cannot yet establish hiring identity."""

    source_company_name: str
    url: str
    evidence_source: str
    reason_code: str
    homepage_verified: bool
    schema_version: str = PROVISIONAL_WEBSITE_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_company_name, str)
            or not " ".join(self.source_company_name.split())
            or len(self.source_company_name) > 300
        ):
            raise ValueError("Provisional website company name is invalid")
        canonical_url = canonicalize_identity_url(self.url)
        parsed = urlsplit(canonical_url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Provisional website URL must be public HTTPS")
        object.__setattr__(self, "url", canonical_url)
        if self.evidence_source != "linkedin_official_website":
            raise ValueError("Provisional website evidence source is unsupported")
        if self.reason_code != "downstream_hiring_relationship_required":
            raise ValueError("Provisional website reason is unsupported")
        if self.homepage_verified is not True:
            raise ValueError("Provisional website requires current homepage verification")
        if self.schema_version != PROVISIONAL_WEBSITE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Provisional website evidence schema is incompatible")

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> ProvisionalWebsiteEvidence:
        expected = set(cls.__dataclass_fields__)
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("Provisional website checkpoint payload is invalid")
        return cls(**payload)
