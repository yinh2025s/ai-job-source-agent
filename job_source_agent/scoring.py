from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

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
    "jobs.smartrecruiters.com",
    "recruitee.com",
    "bamboohr.com",
    "jobvite.com",
    "icims.com",
    "careers.icims.com",
    "successfactors.com",
    "sapsf.com",
    "myworkdayjobs.com",
    "ats.rippling.com",
    "eightfold.ai",
    "careers.oracle.com",
    "oraclecloud.com",
    "whitecarrot.io",
    "whitecarrot.ai",
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
    "article": -80,
    "blog": -80,
    "insight": -80,
    "insights": -80,
    "resource": -55,
    "resources": -55,
    "how-": -100,
    "why-": -100,
    "what-": -100,
    "guide": -55,
    "whitepaper": -80,
}

ATS_AUXILIARY_PATH_PARTS = {
    "introduceyourself",
    "login",
    "my-profile",
    "sign-in",
}

NON_JOB_PATH_PARTS = {
    "api",
    "assets",
    "benefits",
    "embed",
    "images",
    "logo",
    "privacy",
    "share_image",
    "static",
    "terms",
    "culture",
    "locations",
    "departments",
    "people",
    *ATS_AUXILIARY_PATH_PARTS,
}

RESOURCE_EXTENSIONS = (
    ".apng",
    ".avif",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mjs",
    ".pdf",
    ".png",
    ".svg",
    ".txt",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
)

NON_OFFICIAL_JOB_DOMAINS = (
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
)

GENERIC_JOB_LISTING_PARTS = {
    "jobs",
    "job",
    "careers",
    "career",
    "positions",
    "openings",
    "job-openings",
    "job-results",
    "search-results",
    "candidateexperience",
}

_DETAIL_QUERY_KEYS = {"jid", "jobid", "job_id"}
_UUID_DETAIL_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_OPAQUE_DETAIL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{5,127}")


def is_ats_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in ATS_DOMAINS)


def score_career_link(link: RawLink) -> LinkCandidate:
    if is_resource_url(link.url):
        return LinkCandidate(link.url, link.text, link.source_url, -500, ["static/resource URL"], link.origin)

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

    return LinkCandidate(link.url, link.text, link.source_url, score, reasons, link.origin)


def score_job_link(link: RawLink, career_page_url: str) -> LinkCandidate:
    parsed = urlparse(link.url)
    path = parsed.path.lower()
    text = link.text.lower()
    haystack = f"{path} {text}"
    path_parts = [part for part in path.split("/") if part]
    leaf = path_parts[-1] if path_parts else ""
    normalized_text = " ".join(text.split())
    explicit_job_list_command = any(
        phrase in normalized_text
        for phrase in (
            "browse jobs",
            "browse roles",
            "open positions",
            "open roles",
            "search jobs",
            "search roles",
            "view jobs",
            "view roles",
        )
    )
    score = 0
    reasons: list[str] = []
    same_page_detail_query = _looks_like_same_page_detail_query(
        link.url,
        link.source_url,
    )
    first_party_numeric_detail = _looks_like_first_party_numeric_detail_route(
        link.url,
        link.source_url,
    )

    if is_resource_url(link.url):
        return LinkCandidate(link.url, link.text, link.source_url, -500, ["static/resource URL"], link.origin)
    if is_non_official_job_domain(link.url):
        return LinkCandidate(link.url, link.text, link.source_url, -500, ["non-official job/social domain"], link.origin)

    if (
        normalize_for_compare(link.url) == normalize_for_compare(career_page_url)
        and not same_page_detail_query
    ):
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

    if first_party_numeric_detail:
        score += 90
        reasons.append("first-party numeric job detail route")
    elif same_page_detail_query:
        score += 90
        reasons.append("job-detail query pattern")
    elif _looks_like_generic_listing_leaf(leaf):
        score += 80
        reasons.append("job-listing route name")
    elif any(token in path for token in ("/jobs/", "/job/", "/positions/", "/openings/", "/job-openings/")):
        score += 60
        reasons.append("job-detail path pattern")
    elif "/careers/" in path and any(keyword in text for keyword in JOB_TITLE_KEYWORDS):
        score += 60
        reasons.append("job-detail path pattern")
    elif leaf == "search" and any(
        part in {"jobs", "careers"} for part in path_parts[:-1]
    ):
        score += 25
        reasons.append("job-listing path pattern")
    elif leaf == "all-jobs" and explicit_job_list_command:
        score += 20
        reasons.append("explicit all-jobs route")

    if explicit_job_list_command:
        score += 30
        reasons.append("explicit job-list command")

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

    return LinkCandidate(link.url, link.text, link.source_url, score, reasons, link.origin)


def is_likely_job_detail(candidate: LinkCandidate) -> bool:
    if is_resource_url(candidate.url):
        return False
    if is_non_official_job_domain(candidate.url):
        return False
    path_parts = [part.lower() for part in urlparse(candidate.url).path.split("/") if part]
    if is_ats_url(candidate.url) and set(path_parts).intersection(ATS_AUXILIARY_PATH_PARTS):
        return False
    if _looks_like_same_page_detail_query(candidate.url, candidate.source_url):
        return True
    if _looks_like_first_party_numeric_detail_route(candidate.url, candidate.source_url):
        return True
    if normalize_for_compare(candidate.url) == normalize_for_compare(candidate.source_url):
        return False
    if not path_parts or _looks_like_generic_listing_leaf(path_parts[-1]):
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
    if is_resource_url(candidate.url):
        return False
    if is_non_official_job_domain(candidate.url):
        return False
    if is_likely_job_detail(candidate):
        return False
    reason_text = " ".join(candidate.reasons)
    path_parts = [part.lower() for part in urlparse(candidate.url).path.split("/") if part]
    blocked_parts = set(path_parts).intersection(NON_JOB_PATH_PARTS)
    trusted_ats_embed = (
        blocked_parts == {"embed"}
        and is_ats_url(candidate.url)
        and "ATS board/listing candidate" in reason_text
    )
    if blocked_parts and not trusted_ats_embed:
        return False
    text = candidate.text.lower()
    return (
        candidate.score >= 45
        and (
            "ATS board/listing candidate" in reason_text
            or "job-listing path pattern" in reason_text
            or "job-listing route name" in reason_text
            or "explicit all-jobs route" in reason_text
            or (path_parts and _looks_like_generic_listing_leaf(path_parts[-1]))
            or "open roles" in text
            or "open positions" in text
            or "career keyword" in reason_text
        )
    )


def normalize_for_compare(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="", path=parsed.path.rstrip("/")).geturl()


def _looks_like_same_page_detail_query(url: str, source_url: str) -> bool:
    parsed = urlparse(url)
    source = urlparse(source_url)
    detail_path = parsed.path.rstrip("/")
    source_path = source.path.rstrip("/")
    if (
        parsed.scheme.lower() != source.scheme.lower()
        or parsed.netloc.lower() != source.netloc.lower()
        or detail_path.split("/")[-1].lower() != "job"
        or detail_path not in {source_path, f"{source_path}/job"}
    ):
        return False

    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1:
        return False
    key, value = query[0]
    if key.lower() not in _DETAIL_QUERY_KEYS or not value:
        return False
    if _UUID_DETAIL_ID.fullmatch(value) or (value.isdigit() and len(value) <= 24):
        return True
    return bool(
        _OPAQUE_DETAIL_ID.fullmatch(value)
        and any(character.isalpha() for character in value)
        and any(character.isdigit() for character in value)
    )


def _looks_like_first_party_numeric_detail_route(url: str, source_url: str) -> bool:
    parsed = urlparse(url)
    source = urlparse(source_url)
    if (
        parsed.scheme.casefold() != source.scheme.casefold()
        or parsed.netloc.casefold() != source.netloc.casefold()
    ):
        return False
    target_parts = [part.casefold() for part in parsed.path.split("/") if part]
    source_parts = [part.casefold() for part in source.path.split("/") if part]
    if (
        not source_parts
        or source_parts[-1] not in {"all-jobs", "all-roles"}
        or target_parts[: len(source_parts)] != source_parts
        or len(target_parts) != len(source_parts) + 2
    ):
        return False
    job_id, slug = target_parts[-2:]
    if not (job_id.isdigit() and 4 <= len(job_id) <= 18):
        return False
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", slug):
        return False
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not query:
        return True
    if len(query) != 1:
        return False
    key, value = query[0]
    return key.casefold() in {"gh_jid", "jid", "jobid", "job_id"} and value == job_id


def _looks_like_generic_listing_leaf(leaf: str) -> bool:
    return leaf in GENERIC_JOB_LISTING_PARTS or any(
        marker in leaf
        for marker in (
            "job-results",
            "job-search",
            "jobs-search",
            "career-opportunities-search",
        )
    )


def _looks_like_ats_job_detail(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part.lower() for part in parsed.path.split("/") if part]
    if any(part in NON_JOB_PATH_PARTS for part in parts):
        return False
    if is_resource_url(url):
        return False
    if host == "jobs.lever.co":
        return len(parts) >= 2
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "jobs" in parts and len(parts) >= 3
    if host.endswith(".greenhouse.io"):
        return "jobs" in parts and len(parts) >= 2
    if "ashbyhq.com" in host:
        return len(parts) >= 2 and parts[-1] != "jobs"
    if "smartrecruiters.com" in host:
        return len(parts) >= 2 and parts[0].lower() not in {"companies", "company"}
    if "workable.com" in host:
        return "j" in parts or len(parts) >= 3
    if "icims.com" in host:
        return "jobs" in parts and any(part.isdigit() for part in parts)
    if "workdayjobs.com" in host or "myworkdayjobs.com" in host:
        return "job" in parts and len(parts) >= 3
    if "eightfold.ai" in host:
        return any(part in {"job", "jobs"} for part in parts) and len(parts) >= 3
    if host == "careers.oracle.com" or host.endswith(".oraclecloud.com"):
        return "job" in parts or "jobdetail" in parts
    if "successfactors.com" in host or "sapsf.com" in host:
        query = urlparse(url).query.lower()
        return "career_job_req_id" in query or "jobreqid" in query or "job" in parts
    if "rippling.com" in host:
        return "jobs" in parts and len(parts) >= 4 and parts[0] != "embed"
    if "bamboohr.com" in host:
        return len(parts) >= 2 and parts[0] == "careers" and parts[1].isdigit()
    return len(parts) >= 2


def is_resource_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(RESOURCE_EXTENSIONS) or any(
        marker in path
        for marker in ("/assets/", "/static/", "/images/", "/share_image/")
    )


def is_non_official_job_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in NON_OFFICIAL_JOB_DOMAINS)
