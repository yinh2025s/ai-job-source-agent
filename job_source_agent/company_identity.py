from __future__ import annotations

from dataclasses import dataclass

from .web import domain_of, normalize_url


@dataclass
class CompanyIdentity:
    brand_name: str
    hiring_entity_name: str
    career_root_url: str | None = None
    official_website_url: str | None = None
    reasons: list[str] | None = None


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
}


class CompanyIdentityResolver:
    def resolve(
        self,
        company_name: str,
        website_url: str | None = None,
        linkedin_company_url: str | None = None,
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
            if (
                rule_key == key
                or rule_key in key.split()
                or rule_key in website_domain
                or rule_key in linkedin_key
            ):
                trace["matched_rule"] = rule_key
                trace["selected"] = {
                    "brand_name": identity.brand_name,
                    "hiring_entity_name": identity.hiring_entity_name,
                    "career_root_url": identity.career_root_url,
                    "official_website_url": identity.official_website_url,
                    "reasons": identity.reasons or [],
                }
                return identity, trace

        return None, trace


def _normalize_company_key(company_name: str) -> str:
    return " ".join(part.lower() for part in company_name.replace("&", " ").split())
