from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from .opening_matcher import detect_provider


def summarize_results(results: list[dict], elapsed_sec: float | None = None) -> dict:
    total = len(results)
    status_counts = Counter(str(result.get("status") or "unknown") for result in results)
    error_counts = Counter(str(result.get("error") or "none") for result in results)
    provider_counts = Counter(_result_provider(result) for result in results)
    failure_stage_counts = Counter(_failure_stage(result) for result in results if result.get("error"))

    summary = {
        "total": total,
        "success": status_counts.get("success", 0),
        "partial": status_counts.get("partial", 0),
        "failed": status_counts.get("failed", 0),
        "with_website": sum(1 for result in results if result.get("company_website_url")),
        "with_career_page": sum(1 for result in results if result.get("career_page_url")),
        "with_job_list": sum(1 for result in results if result.get("job_list_page_url")),
        "with_opening": sum(1 for result in results if result.get("open_position_url")),
        "rates": _rates(results),
        "status_counts": dict(status_counts),
        "error_counts": dict(error_counts),
        "provider_counts": dict(provider_counts),
        "failure_stage_counts": dict(failure_stage_counts),
    }
    if elapsed_sec is not None:
        summary["elapsed_sec"] = elapsed_sec
    return summary


def _rates(results: list[dict]) -> dict[str, float]:
    total = len(results) or 1
    return {
        "website": round(sum(1 for result in results if result.get("company_website_url")) / total, 3),
        "career_page": round(sum(1 for result in results if result.get("career_page_url")) / total, 3),
        "job_list": round(sum(1 for result in results if result.get("job_list_page_url")) / total, 3),
        "opening": round(sum(1 for result in results if result.get("open_position_url")) / total, 3),
    }


def _result_provider(result: dict) -> str:
    for field in ("open_position_url", "job_list_page_url", "career_page_url", "career_root_url"):
        url = result.get(field)
        if isinstance(url, str) and url:
            provider = detect_provider(url)
            if provider != "generic":
                return provider
            host = urlparse(url).netloc.lower().removeprefix("www.")
            return host or "unknown"
    return "unknown"


def _failure_stage(result: dict) -> str:
    if not result.get("company_website_url"):
        return "website"
    if not result.get("career_page_url"):
        return "career_page"
    if not result.get("job_list_page_url"):
        return "job_list"
    if not result.get("open_position_url"):
        return "opening"
    return "unknown"
