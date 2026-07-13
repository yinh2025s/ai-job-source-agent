from __future__ import annotations

from html import unescape
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_MAX_PAGES = 5
_MAX_SCRIPT_CHARS = 2_000_000
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{3,100}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


class PhenomAdapter:
    name = "phenom"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # Phenom commonly runs on customer-owned domains and requires page evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        if not _is_safe_search_url(page_url):
            return None
        config, ddo = _phenom_state(page.html)
        identifier = str(config.get("refNum") or "").strip()
        if not _is_phenom_state(config, ddo) or not _IDENTIFIER.fullmatch(identifier):
            return None
        return JobBoard(
            url=_without_query(page_url),
            provider=self.name,
            identifier=identifier,
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_valid_board(board):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid Phenom board"},
            )

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        board_urls: list[str] = []
        rejected_urls: list[str] = []
        total_hits = None
        inventory_scope = "title_filtered" if query.title else "full"
        inventory_complete = False
        for page_index in range(_MAX_PAGES):
            search_url = _search_url(board.url, query.title, page_index * 10)
            try:
                page = fetcher.fetch(search_url)
            except (FetchError, OSError, TimeoutError) as error:
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=candidates,
                    reason_code=None if candidates else "PROVIDER_FETCH_FAILED",
                    retryable=not candidates,
                    inventory_scope=inventory_scope,
                    inventory_complete=False,
                    trace={
                        "adapter": self.name,
                        "variant": "ssr_eager_refine_search",
                        "board_urls": board_urls + [search_url],
                        "errors": [{"url": search_url, "error": str(error)}],
                        "candidate_count": len(candidates),
                        "inventory_scope": inventory_scope,
                        "inventory_complete": False,
                    },
                )

            final_url = page.final_url or page.url
            board_urls.append(final_url)
            if not _same_search_origin(final_url, board.url):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=candidates,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "variant": "ssr_eager_refine_search",
                        "board_urls": board_urls,
                        "rejected_response_url": final_url,
                    },
                )

            config, ddo = _phenom_state(page.html)
            if not _is_phenom_state(config, ddo) or config.get("refNum") != board.identifier:
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=candidates,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "variant": "ssr_eager_refine_search",
                        "board_urls": board_urls,
                        "error": "Phenom state or tenant mismatch",
                    },
                )

            eager = ddo.get("eagerLoadRefineSearch")
            jobs = _jobs_from_eager(eager)
            if isinstance(eager, dict) and isinstance(eager.get("totalHits"), int):
                total_hits = eager["totalHits"]
            for record in jobs:
                candidate = _job_candidate(record, config, board)
                if candidate is None:
                    rejected_urls.append(str(record.get("jobId") or ""))
                    continue
                if candidate.url in seen:
                    continue
                seen.add(candidate.url)
                candidates.append(candidate)

            consumed = page_index * 10 + len(jobs)
            if isinstance(total_hits, int) and consumed >= total_hits:
                inventory_complete = True
                break
            if total_hits is None and (not jobs or len(jobs) < 10):
                inventory_complete = True
                break
            if not jobs:
                break
            if query.title and any(
                candidate.title.casefold().strip() == query.title.casefold().strip()
                for candidate in candidates
            ):
                break

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "ssr_eager_refine_search",
                "board_urls": board_urls,
                "candidate_count": len(candidates),
                "total_hits": total_hits,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
                "rejected_job_ids": list(dict.fromkeys(rejected_urls)),
            },
        )


def _phenom_state(html: str) -> tuple[dict, dict]:
    config: dict = {}
    ddo: dict = {}
    decoder = json.JSONDecoder()
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", html or "", re.I | re.S):
        text = unescape(body)
        if len(text) > _MAX_SCRIPT_CHARS:
            continue
        for match in re.finditer(
            r"(?P<name>(?:var\s+)?phApp(?:\.ddo)?)\s*=\s*(?:phApp\s*\|\|\s*)?(?=[{])",
            text,
        ):
            try:
                value, _end = decoder.raw_decode(text, match.end())
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            if match.group("name").endswith(".ddo"):
                ddo = value
            else:
                config.update(value)
    return config, ddo


def _is_phenom_state(config: dict, ddo: dict) -> bool:
    cdn = str(config.get("cdnUrl") or "").casefold()
    page_name = str(config.get("pageName") or "").casefold()
    return (
        "phenompeople.com" in cdn
        and page_name == "search-results"
        and isinstance(ddo.get("eagerLoadRefineSearch"), dict)
    )


def _jobs_from_eager(eager) -> list[dict]:
    if not isinstance(eager, dict):
        return []
    data = eager.get("data")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return [record for record in jobs if isinstance(record, dict)] if isinstance(jobs, list) else []


def _job_candidate(record: dict, config: dict, board: JobBoard) -> JobCandidate | None:
    title = str(record.get("title") or record.get("jobTitle") or "").strip()
    job_id = str(record.get("jobId") or record.get("job_id") or "").strip()
    if not title or not _JOB_ID.fullmatch(job_id):
        return None
    base_url = str(config.get("baseUrl") or "").strip() or board.url.rsplit("/search-results", 1)[0] + "/"
    detail_url = f"{base_url.rstrip('/')}/job/{quote(job_id, safe='._-')}/{_slug(title)}"
    if not _same_origin(detail_url, board.url):
        return None
    location = str(
        record.get("cityStateCountry")
        or record.get("formattedLocation")
        or record.get("location")
        or ""
    ).strip()
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="phenom",
        location=location or None,
        raw={
            "job_id": job_id,
            "job_seq_no": record.get("jobSeqNo"),
            "locale": record.get("locale"),
        },
    )


def _search_url(board_url: str, title: str | None, offset: int) -> str:
    parsed = urlparse(board_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {"keywords", "from"}]
    if title:
        query.append(("keywords", title))
    if offset:
        query.append(("from", str(offset)))
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return slug[:160] or "job"


def _without_query(url: str) -> str:
    return urlunparse(urlparse(url)._replace(query="", fragment=""))


def _is_valid_board(board: JobBoard) -> bool:
    return (
        board.provider == "phenom"
        and bool(board.identifier)
        and bool(_IDENTIFIER.fullmatch(board.identifier or ""))
        and _is_safe_search_url(board.url)
    )


def _is_safe_search_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and bool(parsed.hostname)
        and bool(parts)
        and parts[-1] == "search-results"
    )


def _same_search_origin(first: str, second: str) -> bool:
    return _is_safe_search_url(first) and _same_origin(first, second)


def _same_origin(first: str, second: str) -> bool:
    try:
        left = urlparse(first)
        right = urlparse(second)
        left_port = left.port
        right_port = right.port
    except (TypeError, ValueError):
        return False
    return (
        left.scheme == right.scheme == "https"
        and left.hostname == right.hostname
        and left_port in {None, 443}
        and right_port in {None, 443}
        and left.username is None
        and left.password is None
        and right.username is None
        and right.password is None
    )


ADAPTER = PhenomAdapter()
