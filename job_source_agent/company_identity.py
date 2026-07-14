from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from .posting_identity import PostingIdentityEvidence
from .web import domain_of, normalize_url


@dataclass
class CompanyIdentity:
    brand_name: str
    hiring_entity_name: str
    career_root_url: str | None = None
    official_website_url: str | None = None
    reasons: list[str] | None = None
    relationship_type: str | None = None
    relationship_verified: bool = False
    verification_method: str | None = None
    evidence_url: str | None = None


BRAND_HIRING_RULES = {
    "instagram": CompanyIdentity(
        brand_name="Instagram",
        hiring_entity_name="Meta",
        career_root_url="https://www.metacareers.com/jobs/",
        official_website_url="https://www.instagram.com/",
        reasons=["Instagram hiring is handled through Meta Careers"],
    ),
    "whatsapp": CompanyIdentity(
        brand_name="WhatsApp",
        hiring_entity_name="Meta",
        career_root_url="https://www.metacareers.com/jobs/",
        official_website_url="https://www.whatsapp.com/",
        reasons=["WhatsApp hiring is handled through Meta Careers"],
    ),
    "threads": CompanyIdentity(
        brand_name="Threads",
        hiring_entity_name="Meta",
        career_root_url="https://www.metacareers.com/jobs/",
        official_website_url="https://www.threads.net/",
        reasons=["Threads hiring is handled through Meta Careers"],
    ),
    "meta": CompanyIdentity(
        brand_name="Meta",
        hiring_entity_name="Meta",
        career_root_url="https://www.metacareers.com/jobs/",
        official_website_url="https://www.meta.com/",
        reasons=["Meta hiring is handled through Meta Careers"],
    ),
    "youtube": CompanyIdentity(
        brand_name="YouTube",
        hiring_entity_name="Google",
        career_root_url="https://www.google.com/about/careers/applications/",
        official_website_url="https://www.youtube.com/",
        reasons=["YouTube hiring is handled through Google Careers"],
    ),
    "google": CompanyIdentity(
        brand_name="Google",
        hiring_entity_name="Google",
        career_root_url="https://www.google.com/about/careers/applications/",
        official_website_url="https://www.google.com/",
        reasons=["Google has a dedicated careers search system"],
    ),
    "notion": CompanyIdentity(
        brand_name="Notion",
        hiring_entity_name="Notion",
        career_root_url="https://www.notion.com/careers",
        official_website_url="https://www.notion.com/",
        reasons=["Notion careers are served from the www.notion.com careers path"],
    ),
    "netflix": CompanyIdentity(
        brand_name="Netflix",
        hiring_entity_name="Netflix",
        career_root_url="https://jobs.netflix.com",
        official_website_url="https://www.netflix.com/",
        reasons=["Netflix uses a dedicated jobs.netflix.com careers system"],
    ),
    "hudl": CompanyIdentity(
        brand_name="Hudl",
        hiring_entity_name="Hudl",
        career_root_url="https://www.hudl.com/jobs#jobs",
        official_website_url="https://www.hudl.com/",
        reasons=["Hudl routes careers traffic to its jobs page"],
    ),
    "snap": CompanyIdentity(
        brand_name="Snap",
        hiring_entity_name="Snap Inc.",
        career_root_url="https://careers.snap.com/",
        official_website_url="https://www.snap.com/",
        reasons=["Snap has a dedicated careers.snap.com jobs system"],
    ),
    "roku": CompanyIdentity(
        brand_name="Roku",
        hiring_entity_name="Roku",
        career_root_url="https://www.weareroku.com/",
        official_website_url="https://www.roku.com/",
        reasons=["Roku careers are served from weareroku.com"],
    ),
    "home depot": CompanyIdentity(
        brand_name="The Home Depot",
        hiring_entity_name="The Home Depot",
        career_root_url="https://careers.homedepot.com/",
        official_website_url="https://www.homedepot.com/",
        reasons=["The Home Depot uses careers.homedepot.com for hiring"],
    ),
    "stripe": CompanyIdentity(
        brand_name="Stripe",
        hiring_entity_name="Stripe",
        career_root_url="https://stripe.com/jobs",
        official_website_url="https://stripe.com/",
        reasons=["Stripe publishes roles on stripe.com/jobs"],
    ),
    "nuro": CompanyIdentity(
        brand_name="Nuro",
        hiring_entity_name="Nuro",
        career_root_url="https://www.nuro.ai/careers",
        official_website_url="https://www.nuro.ai/",
        reasons=["Nuro careers are served from nuro.ai/careers"],
    ),
    "morgan stanley": CompanyIdentity(
        brand_name="Morgan Stanley",
        hiring_entity_name="Morgan Stanley",
        career_root_url="https://www.morganstanley.com/careers",
        official_website_url="https://www.morganstanley.com/",
        reasons=["Morgan Stanley uses morganstanley.com/careers for hiring"],
    ),
    "lemonade": CompanyIdentity(
        brand_name="Lemonade",
        hiring_entity_name="Lemonade",
        career_root_url="https://www.lemonade.com/careers",
        official_website_url="https://www.lemonade.com/",
        reasons=["Lemonade publishes roles from lemonade.com/careers"],
    ),
    "podium": CompanyIdentity(
        brand_name="Podium",
        hiring_entity_name="Podium",
        career_root_url="https://www.podium.com/careers",
        official_website_url="https://www.podium.com/",
        reasons=["Podium publishes roles from podium.com/careers"],
    ),
    "paretohealth": CompanyIdentity(
        brand_name="ParetoHealth",
        hiring_entity_name="ParetoHealth",
        career_root_url="https://www.paretohealth.com/careers",
        official_website_url="https://www.paretohealth.com/",
        reasons=["ParetoHealth publishes roles from paretohealth.com/careers"],
    ),
    "anthropic": CompanyIdentity(
        brand_name="Anthropic",
        hiring_entity_name="Anthropic",
        career_root_url="https://job-boards.greenhouse.io/anthropic",
        official_website_url="https://www.anthropic.com/",
        reasons=["Anthropic routes job listings through a Greenhouse job board"],
    ),
    "posthog": CompanyIdentity(
        brand_name="PostHog",
        hiring_entity_name="PostHog",
        career_root_url="https://posthog.com/careers/jobs",
        official_website_url="https://posthog.com/",
        reasons=["PostHog publishes roles from posthog.com/careers/jobs"],
    ),
    "ekimetrics": CompanyIdentity(
        brand_name="Ekimetrics",
        hiring_entity_name="Ekimetrics",
        career_root_url="https://jobs.lever.co/ekimetrics",
        official_website_url="https://www.ekimetrics.com/",
        reasons=["Ekimetrics routes job listings through Lever"],
    ),
    "brex": CompanyIdentity(
        brand_name="Brex",
        hiring_entity_name="Brex",
        career_root_url="https://www.brex.com/careers",
        official_website_url="https://www.brex.com/",
        reasons=["Brex publishes roles from brex.com/careers"],
    ),
    "lyft": CompanyIdentity(
        brand_name="Lyft",
        hiring_entity_name="Lyft",
        career_root_url="https://job-boards.greenhouse.io/lyft",
        official_website_url="https://www.lyft.com/",
        reasons=["Lyft routes careers traffic to a Greenhouse job board"],
    ),
    "modmed": CompanyIdentity(
        brand_name="ModMed",
        hiring_entity_name="ModMed",
        career_root_url="https://modmed.wd501.myworkdayjobs.com/ModMed12",
        official_website_url="https://www.modmed.com/",
        reasons=["ModMed publishes roles through its ModMed12 Workday career site"],
    ),
}


class WebsiteResolver(Protocol):
    def resolve(
        self,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
        preferred_url: str | None = None,
    ) -> tuple[str | None, dict]:
        ...


class PostingIdentityProbe(Protocol):
    def probe(
        self,
        publisher_name: str,
        linkedin_job_url: str | None,
        website_url: str | None = None,
    ) -> PostingIdentityEvidence:
        ...


class CompanyIdentityResolver:
    def __init__(
        self,
        posting_probe: PostingIdentityProbe | None = None,
        website_resolver: WebsiteResolver | None = None,
    ) -> None:
        self.posting_probe = posting_probe
        self.website_resolver = website_resolver

    def resolve(
        self,
        company_name: str,
        website_url: str | None = None,
        linkedin_company_url: str | None = None,
        linkedin_job_url: str | None = None,
        job_location: str | None = None,
    ) -> tuple[CompanyIdentity | None, dict]:
        key = _normalize_company_key(company_name)
        website_domain = domain_of(normalize_url(website_url)) if website_url else ""
        linkedin_key = (linkedin_company_url or "").lower()
        trace = {
            "company_name": company_name,
            "website_url": website_url,
            "linkedin_company_url": linkedin_company_url,
            "matched_rule": None,
        }

        for rule_key, identity in BRAND_HIRING_RULES.items():
            if self._matches_rule(rule_key, key, website_domain, linkedin_key):
                trace["matched_rule"] = rule_key
                trace["selected"] = {
                    "brand_name": identity.brand_name,
                    "hiring_entity_name": identity.hiring_entity_name,
                    "career_root_url": identity.career_root_url,
                    "official_website_url": identity.official_website_url,
                    "reasons": identity.reasons or [],
                    "relationship": _relationship_payload(identity, company_name),
                }
                return _resolved_identity(identity, company_name), trace

        if self.posting_probe is None:
            return None, trace

        posting_evidence = self.posting_probe.probe(
            company_name,
            linkedin_job_url,
            website_url=website_url,
        )
        trace["posting_identity"] = posting_evidence.trace()
        if (
            posting_evidence.classification != "alternate_employer"
            or not posting_evidence.employer_name
        ):
            return None, trace

        employer_name = posting_evidence.employer_name
        employer_key = _normalize_company_key(employer_name)
        for rule_key, identity in BRAND_HIRING_RULES.items():
            if self._matches_rule(rule_key, employer_key, "", ""):
                trace["matched_rule"] = rule_key
                trace["selected"] = {
                    "brand_name": identity.brand_name,
                    "hiring_entity_name": identity.hiring_entity_name,
                    "career_root_url": identity.career_root_url,
                    "official_website_url": identity.official_website_url,
                    "reasons": [
                        *(identity.reasons or []),
                        "LinkedIn job description verified a different hiring entity",
                    ],
                    "relationship": _relationship_payload(
                        identity,
                        company_name,
                        relationship_type="alternate_employer",
                        verification_method="posting_identity_probe",
                        evidence_url=linkedin_job_url,
                    ),
                }
                return _resolved_identity(
                    identity,
                    company_name,
                    relationship_type="alternate_employer",
                    verification_method="posting_identity_probe",
                    evidence_url=linkedin_job_url,
                ), trace

        if self.website_resolver is None:
            trace["alternate_employer_resolution"] = {
                "status": "unresolved",
                "reason": "website resolver was not configured",
            }
            return None, trace
        employer_website, website_trace = self.website_resolver.resolve(
            employer_name,
            job_location=job_location,
        )
        trace["alternate_employer_resolution"] = website_trace
        if not employer_website:
            return None, trace
        identity = CompanyIdentity(
            brand_name=employer_name,
            hiring_entity_name=employer_name,
            official_website_url=employer_website,
            reasons=["LinkedIn job description verified a different hiring entity"],
            relationship_type="alternate_employer",
            relationship_verified=True,
            verification_method="posting_identity_probe",
            evidence_url=linkedin_job_url,
        )
        trace["selected"] = {
            "brand_name": identity.brand_name,
            "hiring_entity_name": identity.hiring_entity_name,
            "career_root_url": None,
            "official_website_url": identity.official_website_url,
            "reasons": identity.reasons,
            "relationship": _relationship_payload(identity, company_name),
        }
        return identity, trace

    def _matches_rule(self, rule_key: str, company_key: str, website_domain: str, linkedin_key: str) -> bool:
        if rule_key == company_key or rule_key in company_key.split():
            return True
        if " " in rule_key and rule_key in company_key:
            return True

        domain_labels = [label for label in website_domain.replace("-", ".").split(".") if label]
        if " " not in rule_key and rule_key in domain_labels:
            return True

        linkedin_tokens = [
            token
            for token in linkedin_key.replace("-", " ").replace("/", " ").replace("?", " ").replace("&", " ").split()
            if token
        ]
        if " " not in rule_key and rule_key in linkedin_tokens:
            return True
        return False


def _normalize_company_key(company_name: str) -> str:
    return " ".join(part.lower() for part in company_name.replace("&", " ").split())


def _resolved_identity(
    identity: CompanyIdentity,
    source_company_name: str,
    *,
    relationship_type: str | None = None,
    verification_method: str | None = None,
    evidence_url: str | None = None,
) -> CompanyIdentity:
    relationship_type = relationship_type or identity.relationship_type
    if relationship_type is None:
        relationship_type = (
            "same_entity"
            if _same_entity(source_company_name, identity.hiring_entity_name)
            else "brand_parent"
        )
    return CompanyIdentity(
        brand_name=identity.brand_name,
        hiring_entity_name=identity.hiring_entity_name,
        career_root_url=identity.career_root_url,
        official_website_url=identity.official_website_url,
        reasons=identity.reasons,
        relationship_type=relationship_type,
        relationship_verified=True,
        verification_method=(
            verification_method or identity.verification_method or "identity_rule"
        ),
        evidence_url=(
            evidence_url
            or identity.evidence_url
            or identity.career_root_url
            or identity.official_website_url
        ),
    )


def _relationship_payload(
    identity: CompanyIdentity,
    source_company_name: str,
    **overrides: str | None,
) -> dict[str, str | bool | None]:
    resolved = _resolved_identity(identity, source_company_name, **overrides)
    return {
        "type": resolved.relationship_type,
        "verified": resolved.relationship_verified,
        "verification_method": resolved.verification_method,
        "evidence_url": resolved.evidence_url,
    }


def _same_entity(left: str, right: str) -> bool:
    def key(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    return key(left) == key(right)
