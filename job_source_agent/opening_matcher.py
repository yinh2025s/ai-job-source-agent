from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

from .scoring import is_likely_job_detail, score_job_link
from .web import FetchError, Fetcher, RawLink, domain_of, extract_links, safe_normalize_url


STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "i",
    "ii",
    "iii",
    "in",
    "of",
    "the",
    "to",
}


@dataclass
class OpeningMatch:
    url: str
    title: str
    score: int
    provider: str
    reasons: list[str]
    job_list_page_url: str | None = None


@dataclass
class ProviderApiRequest:
    url: str
    data: bytes | None = None
    headers: dict[str, str] | None = None


class JobOpeningMatcher:
    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def match(
        self,
        job_list_url: str,
        target_title: str | None,
        target_location: str | None = None,
    ) -> tuple[OpeningMatch | None, dict]:
        trace = {
            "job_list_url": job_list_url,
            "target_title": target_title,
            "target_location": target_location,
            "provider": detect_provider(job_list_url),
            "searched_urls": [],
            "candidates": [],
        }
        if not target_title:
            return None, trace

        api_match, api_trace = self._match_provider_api(job_list_url, target_title)
        trace["provider_api"] = api_trace
        if api_match:
            trace["selected"] = {
                "url": api_match.url,
                "title": api_match.title,
                "score": api_match.score,
                "reasons": api_match.reasons,
            }
            return api_match, trace

        search_urls = build_provider_search_urls(job_list_url, target_title)
        for search_url in search_urls:
            trace["searched_urls"].append(search_url)
            try:
                page = self.fetcher.fetch(search_url)
            except FetchError as exc:
                trace.setdefault("errors", []).append({"url": search_url, "error": str(exc)})
                continue

            page_url = page.final_url or page.url
            candidates = []
            links = extract_links(page) + structured_job_links(page.html, page_url)
            for link in dedupe_raw_links(links):
                scored = score_job_link(link, page_url)
                title_score, title_reasons = score_title_match(link.text, target_title)
                if title_score < 45:
                    continue
                total_score = scored.score + title_score
                reasons = scored.reasons + title_reasons
                if not is_likely_job_detail(scored) and title_score < 60:
                    continue
                if total_score < 70:
                    continue
                candidates.append(
                    OpeningMatch(
                        url=link.url,
                        title=link.text,
                        score=total_score,
                        provider=trace["provider"],
                        reasons=reasons,
                        job_list_page_url=page_url,
                    )
                )

            candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            trace["candidates"].extend(
                [
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                    for candidate in candidates[:8]
                ]
            )
            if candidates:
                trace["selected"] = {
                    "url": candidates[0].url,
                    "title": candidates[0].title,
                    "score": candidates[0].score,
                    "reasons": candidates[0].reasons,
                }
                return candidates[0], trace

        fallback_url = build_search_result_url(job_list_url, target_title)
        if fallback_url:
            trace["fallback_search_url"] = fallback_url
        return None, trace

    def _match_provider_api(self, job_list_url: str, target_title: str) -> tuple[OpeningMatch | None, dict]:
        provider = detect_provider(job_list_url)
        api_requests = build_provider_api_requests(job_list_url, target_title)
        trace = {"provider": provider, "api_urls": [request.url for request in api_requests], "candidates": []}
        for api_request in api_requests:
            try:
                page = self.fetcher.fetch(api_request.url, data=api_request.data, headers=api_request.headers)
            except FetchError as exc:
                trace.setdefault("errors", []).append({"url": api_request.url, "error": str(exc)})
                continue
            candidates = provider_api_candidates(provider, page.html, job_list_url)
            scored = []
            for title, url in candidates:
                title_score, title_reasons = score_title_match(title, target_title)
                if title_score < 45:
                    continue
                scored.append(
                    OpeningMatch(
                        url=url,
                        title=title,
                        score=title_score + 100,
                        provider=provider,
                        reasons=["provider API result"] + title_reasons,
                        job_list_page_url=job_list_url,
                    )
                )
            scored.sort(key=lambda candidate: candidate.score, reverse=True)
            trace["candidates"].extend(
                [
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                    for candidate in scored[:8]
                ]
            )
            if scored:
                return scored[0], trace
        return None, trace


def detect_provider(url: str) -> str:
    host = domain_of(url)
    if "google.com" in host:
        return "google_careers"
    if "metacareers.com" in host:
        return "meta_careers"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "workable.com" in host:
        return "workable"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "icims.com" in host:
        return "icims"
    if "workdayjobs.com" in host or "myworkdayjobs.com" in host:
        return "workday"
    if "successfactors.com" in host or "sapsf.com" in host:
        return "successfactors"
    if "rippling.com" in host:
        return "rippling"
    return "generic"


def build_provider_search_urls(job_list_url: str, target_title: str) -> list[str]:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    if provider == "google_careers":
        return [
            f"https://www.google.com/about/careers/applications/jobs/results/?q={query}",
            job_list_url,
        ]
    if provider == "meta_careers":
        return [
            f"https://www.metacareers.com/jobs/?q={query}",
            job_list_url,
        ]
    if provider in {"greenhouse", "lever", "ashby"}:
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "workable":
        return [job_list_url, add_query_params(job_list_url, {"query": target_title})]
    if provider == "smartrecruiters":
        return [job_list_url, add_query_params(job_list_url, {"search": target_title})]
    if provider == "icims":
        return [
            job_list_url,
            add_query_params(job_list_url, {"ss": "1", "searchKeyword": target_title}),
        ]
    if provider == "workday":
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "successfactors":
        return [
            job_list_url,
            add_query_params(job_list_url, {"q": target_title}),
            add_query_params(job_list_url, {"keyword": target_title}),
        ]
    if provider == "rippling":
        return [job_list_url]
    return [job_list_url, f"{base}?q={query}", f"{base}?search={query}"]


def build_provider_api_urls(job_list_url: str) -> list[str]:
    return [request.url for request in build_provider_api_requests(job_list_url)]


def build_provider_api_requests(job_list_url: str, target_title: str | None = None) -> list[ProviderApiRequest]:
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if provider == "greenhouse" and parts:
        board = parts[0]
        return [ProviderApiRequest(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")]
    if provider == "lever" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.lever.co/v0/postings/{company}?mode=json")]
    if provider == "smartrecruiters" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")]
    if provider == "ashby":
        board = _ashby_board_name(job_list_url)
        if board:
            return [ProviderApiRequest(f"https://api.ashbyhq.com/posting-api/job-board/{board}")]
    if provider == "workday":
        workday_api_url = build_workday_api_url(job_list_url)
        if workday_api_url:
            payload = {
                "appliedFacets": {},
                "limit": 50,
                "offset": 0,
                "searchText": target_title or "",
            }
            return [ProviderApiRequest(workday_api_url, data=json.dumps(payload).encode("utf-8"))]
    return []


def provider_api_candidates(provider: str, body: str, job_list_url: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if provider == "greenhouse":
        return [
            (str(job.get("title") or ""), str(job.get("absolute_url") or ""))
            for job in data.get("jobs", [])
            if job.get("title") and job.get("absolute_url")
        ]
    if provider == "lever" and isinstance(data, list):
        return [
            (str(job.get("text") or ""), str(job.get("hostedUrl") or job.get("applyUrl") or ""))
            for job in data
            if job.get("text") and (job.get("hostedUrl") or job.get("applyUrl"))
        ]
    if provider == "smartrecruiters":
        candidates = []
        for job in data.get("content", []):
            title = str(job.get("name") or "")
            url = _smartrecruiters_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "workday":
        candidates = []
        for job in data.get("jobPostings", []):
            title = str(job.get("title") or "")
            url = _workday_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "ashby":
        candidates = []
        for job in data.get("jobs", []):
            title = str(job.get("title") or "")
            url = _ashby_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    return []


def structured_job_links(html: str, source_url: str) -> list[RawLink]:
    links: list[RawLink] = []
    for script_attrs, script_body in _script_blocks(html):
        if "application/ld+json" not in script_attrs.lower():
            continue
        try:
            data = json.loads(unescape(script_body.strip()))
        except json.JSONDecodeError:
            continue
        for job in _walk_json_ld_jobs(data):
            title = str(job.get("title") or job.get("name") or "").strip()
            url = _json_ld_url(job)
            normalized = safe_normalize_url(url, source_url) if url else None
            if title and normalized:
                links.append(RawLink(url=normalized, text=title, source_url=source_url))
    for script_attrs, script_body in _script_blocks(html):
        if not _looks_like_json_script(script_attrs, script_body):
            continue
        data = _parse_script_json(script_body)
        if data is None:
            continue
        for title, url in _walk_structured_job_records(data, source_url):
            links.append(RawLink(url=url, text=title, source_url=source_url))
    return dedupe_raw_links(links)


def _script_blocks(html: str) -> list[tuple[str, str]]:
    return [
        (attrs, unescape(body.strip()))
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.I | re.S)
        if body.strip()
    ]


def _looks_like_json_script(attrs: str, body: str) -> bool:
    attrs_lower = attrs.lower()
    body_stripped = body.strip()
    return (
        "application/json" in attrs_lower
        or "application/ld+json" in attrs_lower
        or body_stripped.startswith("{")
        or body_stripped.startswith("[")
    )


def _parse_script_json(body: str):
    text = body.strip()
    if text.startswith("<![CDATA["):
        text = text.removeprefix("<![CDATA[").removesuffix("]]>").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_json_ld_jobs(value):
    if isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(kind).lower() == "jobposting" for kind in types):
            yield value
        for child in value.values():
            yield from _walk_json_ld_jobs(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_ld_jobs(item)


def _json_ld_url(job: dict) -> str:
    raw_url = job.get("url") or job.get("sameAs")
    if isinstance(raw_url, list):
        raw_url = raw_url[0] if raw_url else ""
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url") or ""
    return str(raw_url or "")


STRUCTURED_TITLE_FIELDS = ("title", "name", "jobTitle", "job_title", "text")
STRUCTURED_URL_FIELDS = (
    "url",
    "absolute_url",
    "absoluteUrl",
    "hostedUrl",
    "applyUrl",
    "jobUrl",
    "job_url",
    "externalPath",
    "detailUrl",
    "link",
)


def _walk_structured_job_records(value, source_url: str):
    if isinstance(value, dict):
        title = _first_text_field(value, STRUCTURED_TITLE_FIELDS)
        url = _structured_record_url(value, source_url, title)
        if title and url and _looks_like_structured_job_record(value, url, source_url):
            yield title, url
        for child in value.values():
            yield from _walk_structured_job_records(child, source_url)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_structured_job_records(item, source_url)


def _first_text_field(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _structured_record_url(record: dict, source_url: str, title: str) -> str | None:
    raw_url = _first_text_field(record, STRUCTURED_URL_FIELDS)
    provider = detect_provider(source_url)
    if raw_url:
        normalized = safe_normalize_url(raw_url, source_url)
        if normalized:
            return normalized

    if provider == "successfactors":
        job_req_id = _first_text_field(record, ("career_job_req_id", "jobReqId", "job_req_id", "id"))
        if job_req_id:
            return add_query_params(source_url, {"career_ns": "job_listing", "career_job_req_id": job_req_id})

    if provider == "icims":
        job_id = _first_text_field(record, ("id", "jobId", "job_id", "jobNumber"))
        if job_id and title:
            return safe_normalize_url(f"/jobs/{job_id}/{_slugify_title(title)}/job", source_url)

    if provider == "ashby":
        job_id = _first_text_field(record, ("id", "jobId", "job_id"))
        board = _ashby_board_name(source_url)
        if job_id and board:
            return f"https://jobs.ashbyhq.com/{board}/{job_id}"

    if provider == "workable":
        short_code = _first_text_field(record, ("shortcode", "shortCode", "code", "id"))
        account = _workable_account_name(source_url)
        if short_code and account:
            return f"https://apply.workable.com/{account}/j/{short_code}/"

    return None


def _looks_like_structured_job_record(record: dict, url: str, source_url: str) -> bool:
    keys = " ".join(str(key).lower() for key in record)
    query = urlparse(url).query.lower()
    candidate = score_job_link(RawLink(url=url, text="", source_url=source_url), source_url)
    reason_text = " ".join(candidate.reasons)
    return (
        is_likely_job_detail(candidate)
        or "ATS job detail pattern" in reason_text
        or "career_job_req_id" in query
        or "jobreqid" in query
        or ("job" in keys and detect_provider(url) != "generic" and candidate.score >= 90)
    )


def _slugify_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def dedupe_raw_links(links: list[RawLink]) -> list[RawLink]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RawLink] = []
    for link in links:
        key = (link.url.rstrip("/"), link.text.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(link)
    return deduped


def _smartrecruiters_job_url(job: dict, job_list_url: str) -> str:
    actions = job.get("actions") or {}
    if actions.get("details"):
        return str(actions["details"])
    ref = job.get("ref")
    if ref:
        return str(ref)
    job_id = job.get("id")
    if not job_id:
        return ""
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    return f"https://jobs.smartrecruiters.com/{parts[0]}/{job_id}"


def _ashby_board_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()
    if host == "jobs.ashbyhq.com" and parts:
        return parts[0]
    if host.endswith(".ashbyhq.com") and host not in {"api.ashbyhq.com", "jobs.ashbyhq.com"}:
        return host.split(".", 1)[0]
    return ""


def _ashby_job_url(job: dict, job_list_url: str) -> str:
    raw_url = str(job.get("jobUrl") or job.get("hostedUrl") or job.get("url") or "")
    if raw_url:
        normalized = safe_normalize_url(raw_url, job_list_url)
        if normalized:
            return normalized
    job_id = str(job.get("id") or "")
    board = _ashby_board_name(job_list_url)
    if job_id and board:
        return f"https://jobs.ashbyhq.com/{board}/{job_id}"
    return ""


def _workable_account_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() == "apply.workable.com" and parts:
        return parts[0]
    return ""


def build_workday_api_url(job_list_url: str) -> str | None:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    site = parts[-1]
    tenant = parsed.netloc.split(".", 1)[0]
    if not tenant or not site:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}/jobs"


def _workday_job_url(job: dict, job_list_url: str) -> str:
    external_path = str(job.get("externalPath") or "")
    if not external_path:
        return ""
    if external_path.startswith("http"):
        return external_path
    parsed = urlparse(job_list_url)
    board_path = parsed.path.rstrip("/")
    if not external_path.startswith("/"):
        external_path = "/" + external_path
    return f"{parsed.scheme}://{parsed.netloc}{board_path}{external_path}"


def build_search_result_url(job_list_url: str, target_title: str) -> str | None:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    if provider == "google_careers":
        return f"https://www.google.com/about/careers/applications/jobs/results/?q={query}"
    if provider == "meta_careers":
        return f"https://www.metacareers.com/jobs/?q={query}"
    if provider in {
        "lever",
        "greenhouse",
        "ashby",
    }:
        return job_list_url
    if provider in {"workable", "smartrecruiters", "icims", "workday", "successfactors", "rippling"}:
        return build_provider_search_urls(job_list_url, target_title)[-1]
    return None


def add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def score_title_match(candidate_title: str, target_title: str) -> tuple[int, list[str]]:
    candidate_tokens = _tokens(candidate_title)
    target_tokens = _tokens(target_title)
    if not candidate_tokens or not target_tokens:
        return 0, []

    overlap = candidate_tokens & target_tokens
    recall = len(overlap) / len(target_tokens)
    precision = len(overlap) / len(candidate_tokens)
    score = int((recall * 70) + (precision * 30))
    reasons = []
    if overlap:
        reasons.append(f"title token overlap: {', '.join(sorted(overlap))}")
    if candidate_title.strip().lower() == target_title.strip().lower():
        score += 50
        reasons.append("exact title match")
    return score, reasons


def _tokens(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {token for token in normalized.split() if len(token) >= 2 and token not in STOPWORDS}
