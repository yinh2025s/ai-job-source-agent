from __future__ import annotations

from urllib.parse import urlparse

from .models import LinkCandidate
from .web import RawLink, domain_of, path_depth


ATS_DOMAINS = (
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workable.com",
    "apply.workable.com",
    "smartrecruiters.com",
    "recruitee.com",
    "bamboohr.com",
    "jobvite.com",
    "icims.com",
    "successfactors.com",
    "myworkdayjobs.com",
)

CAREER_KEYWORDS = {
    "careers": 100,
    "career": 95,
    "jobs": 95,
    "join-us": 85,
    "join us": 85,
    "work-with-us": 75,
    "work with us": 75,
    "open roles": 70,
    "open positions": 70,
    "opportunities": 55,
    "hiring": 45,
    "recruiting": 35,
    "vacancies": 35,
}

JOB_TITLE_KEYWORDS = {
    "engineer": 35,
    "developer": 35,
    "scientist": 35,
    "designer": 30,
    "manager": 30,
    "analyst": 30,
    "intern": 40,
    "software": 30,
    "machine learning": 45,
    "data": 20,
    "product": 20,
    "marketing": 18,
    "sales": 18,
    "operations": 18,
    "research": 18,
}

NEGATIVE_KEYWORDS = {
    "privacy": -100,
    "terms": -100,
    "cookie": -100,
    "blog": -45,
    "press": -45,
    "news": -45,
    "about": -35,
    "contact": -35,
    "benefits": -120,
    "culture": -55,
    "life at": -55,
    "office": -35,
    "locations": -60,
    "department": -55,
    "all jobs": -35,
    "all roles": -35,
}

NON_JOB_PATH_PARTS = {
    "benefits",
    "privacy",
    "terms",
    "culture",
    "locations",
    "departments",
    "people",
}

GENERIC_JOB_LISTING_PARTS = {
    "jobs",
    "job",
    "careers",
    "career",
    "positions",
    "openings",
    "job-openings",
}


def is_ats_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in ATS_DOMAINS)


def score_career_link(link: RawLink) -> LinkCandidate:
    haystack = f"{urlparse(link.url).path.lower()} {link.text.lower()} {domain_of(link.url)}"
    score = 0
    reasons: list[str] = []

    for keyword, weight in CAREER_KEYWORDS.items():
        if keyword in haystack:
            score += weight
            reasons.append(f"career keyword '{keyword}'")

    if is_ats_url(link.url):
        score += 95
        reasons.append("known ATS domain")
        if path_depth(link.url) <= 1:
            score += 25
            reasons.append("ATS company board URL")

    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in haystack:
            score += weight
            reasons.append(f"negative keyword '{keyword}'")

    return LinkCandidate(link.url, link.text, link.source_url, score, reasons)


def score_job_link(link: RawLink, career_page_url: str) -> LinkCandidate:
    parsed = urlparse(link.url)
    path = parsed.path.lower()
    text = link.text.lower()
    haystack = f"{path} {text}"
    score = 0
    reasons: list[str] = []

    if normalize_for_compare(link.url) == normalize_for_compare(career_page_url):
        score -= 200
        reasons.append("same as career page")

    if is_ats_url(link.url):
        score += 75
        reasons.append("known ATS domain")
        if _looks_like_ats_job_detail(link.url):
            score += 120
            reasons.append("ATS job detail pattern")
        else:
            score += 25
            reasons.append("ATS board/listing candidate")

    if any(token in path for token in ("/jobs/", "/job/", "/careers/", "/positions/", "/openings/", "/job-openings/")):
        score += 60
        reasons.append("job-detail path pattern")
    elif any(token in path for token in ("/jobs", "/careers", "/positions", "/openings", "/job-openings")):
        score += 25
        reasons.append("job-listing path pattern")

    for keyword, weight in JOB_TITLE_KEYWORDS.items():
        if keyword in haystack:
            score += weight
            reasons.append(f"title keyword '{keyword}'")

    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in haystack:
            score += weight
            reasons.append(f"negative keyword '{keyword}'")

    if len([part for part in path.split("/") if part]) >= 2:
        score += 10
        reasons.append("specific URL depth")

    return LinkCandidate(link.url, link.text, link.source_url, score, reasons)


def is_likely_job_detail(candidate: LinkCandidate) -> bool:
    if normalize_for_compare(candidate.url) == normalize_for_compare(candidate.source_url):
        return False
    path_parts = [part.lower() for part in urlparse(candidate.url).path.split("/") if part]
    if not path_parts or path_parts[-1] in GENERIC_JOB_LISTING_PARTS:
        return False
    if is_ats_url(candidate.url) and _looks_like_ats_job_detail(candidate.url):
        return True
    reason_text = " ".join(candidate.reasons)
    return (
        candidate.score >= 95
        and "job-detail path pattern" in reason_text
        and len(path_parts) >= 2
    )


def is_likely_job_listing_page(candidate: LinkCandidate) -> bool:
    if is_likely_job_detail(candidate):
        return False
    reason_text = " ".join(candidate.reasons)
    path_parts = [part.lower() for part in urlparse(candidate.url).path.split("/") if part]
    text = candidate.text.lower()
    return (
        candidate.score >= 45
        and (
            "ATS board/listing candidate" in reason_text
            or "job-listing path pattern" in reason_text
            or (path_parts and path_parts[-1] in GENERIC_JOB_LISTING_PARTS)
            or "open roles" in text
            or "open positions" in text
            or "career keyword" in reason_text
        )
    )


def normalize_for_compare(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="", path=parsed.path.rstrip("/")).geturl()


def _looks_like_ats_job_detail(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if any(part in NON_JOB_PATH_PARTS for part in parts):
        return False
    if host == "jobs.lever.co":
        return len(parts) >= 2
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "jobs" in parts and len(parts) >= 3
    if host.endswith(".greenhouse.io"):
        return "jobs" in parts and len(parts) >= 2
    if "ashbyhq.com" in host:
        return len(parts) >= 2 and parts[-1] != "jobs"
    return len(parts) >= 2
