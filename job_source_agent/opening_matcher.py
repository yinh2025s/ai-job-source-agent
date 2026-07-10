from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

from .scoring import is_likely_job_detail, score_job_link
from .web import FetchError, Fetcher, RawLink, domain_of, extract_links


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
            for link in extract_links(page):
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
        api_urls = build_provider_api_urls(job_list_url)
        trace = {"provider": provider, "api_urls": api_urls, "candidates": []}
        for api_url in api_urls:
            try:
                page = self.fetcher.fetch(api_url)
            except FetchError as exc:
                trace.setdefault("errors", []).append({"url": api_url, "error": str(exc)})
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
    return [job_list_url, f"{base}?q={query}", f"{base}?search={query}"]


def build_provider_api_urls(job_list_url: str) -> list[str]:
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if provider == "greenhouse" and parts:
        board = parts[0]
        return [f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"]
    if provider == "lever" and parts:
        company = parts[0]
        return [f"https://api.lever.co/v0/postings/{company}?mode=json"]
    if provider == "smartrecruiters" and parts:
        company = parts[0]
        return [f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"]
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
    return []


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
    if provider in {"workable", "smartrecruiters", "icims", "workday", "successfactors"}:
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
