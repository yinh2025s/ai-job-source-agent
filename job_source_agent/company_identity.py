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
            if self._matches_rule(rule_key, key, website_domain, linkedin_key):
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
