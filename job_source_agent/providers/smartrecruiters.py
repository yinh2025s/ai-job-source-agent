from __future__ import annotations

import json
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_API_HOST = "api.smartrecruiters.com"
_PUBLIC_HOST = "jobs.smartrecruiters.com"
_PAGE_SIZE = 100
_MAX_PAGES = 5


class SmartRecruitersAdapter:
    name = "smartrecruiters"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except (TypeError, ValueError):
            return False
        scheme = parsed.scheme.casefold()
        return bool(
            (parsed.hostname or "").casefold() == _PUBLIC_HOST
            and scheme in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and (port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80))
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parts = [part for part in urlparse(url).path.split("/") if part]
        if not parts:
            return None
        identifier = parts[0]
        return JobBoard(
            url=f"https://{_PUBLIC_HOST}/{quote(identifier, safe='-._~')}",
            provider=self.name,
            identifier=identifier,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing SmartRecruiters company identifier"},
            )

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        api_urls: list[str] = []
        errors: list[dict[str, str]] = []
        total_found: int | None = None
        offset = 0
        exact_title_found = False
        normalized_target = _normalized_title(query.title)

        for _ in range(_MAX_PAGES):
            api_url = self.api_url(board.identifier, query=query, offset=offset)
            api_urls.append(api_url)
            try:
                page = fetcher.fetch(api_url)
            except (FetchError, OSError, TimeoutError) as error:
                errors.append({"url": api_url, "error": str(error)})
                break
            final_url = page.final_url or page.url
            if not _is_company_api_url(final_url, board.identifier):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "api_urls": api_urls,
                        "error": "SmartRecruiters API redirected outside the company endpoint",
                    },
                )
            try:
                data = json.loads(page.html)
            except (json.JSONDecodeError, TypeError):
                return self._invalid_response(board, api_urls)
            postings = data.get("content") if isinstance(data, dict) else None
            if not isinstance(postings, list):
                return self._invalid_response(board, api_urls)

            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                title = str(posting.get("name") or "").strip()
                detail_url = _detail_url(posting, board)
                if not title or not detail_url or detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                candidates.append(
                    JobCandidate(
                        title=title,
                        url=detail_url,
                        provider=self.name,
                        location=_location_name(posting),
                        raw={"id": posting.get("id")},
                    )
                )
                if normalized_target and _normalized_title(title) == normalized_target:
                    exact_title_found = True

            total_found = _nonnegative_int(data.get("totalFound"))
            response_offset = _nonnegative_int(data.get("offset"))
            response_limit = _positive_int(data.get("limit")) or _PAGE_SIZE
            next_offset = (response_offset if response_offset is not None else offset) + len(postings)
            if (
                exact_title_found
                or not postings
                or len(postings) < response_limit
                or total_found is None
                or next_offset >= total_found
            ):
                break
            offset = next_offset

        reason_code = None if candidates else (
            "PROVIDER_FETCH_FAILED" if errors else "EMPTY_PROVIDER_RESPONSE"
        )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            trace={
                "adapter": self.name,
                "api_urls": api_urls,
                "candidate_count": len(candidates),
                "page_count": len(api_urls) - len(errors),
                "total_found": total_found,
                "exact_title_found": exact_title_found,
                "errors": errors,
            },
        )

    def _invalid_response(self, board: JobBoard, api_urls: list[str]) -> AdapterResult:
        return AdapterResult(
            provider=self.name,
            board=board,
            reason_code="INVALID_STRUCTURED_DATA",
            trace={"adapter": self.name, "api_urls": api_urls},
        )

    @staticmethod
    def api_url(
        company_identifier: str,
        query: JobQuery | None = None,
        offset: int = 0,
    ) -> str:
        company = quote(company_identifier, safe="-._~")
        params: list[tuple[str, str | int]] = [("limit", _PAGE_SIZE)]
        if offset:
            params.append(("offset", offset))
        if query and query.title:
            params.append(("q", query.title))
        return f"https://{_API_HOST}/v1/companies/{company}/postings?{urlencode(params)}"


def _detail_url(posting: dict, board: JobBoard) -> str:
    actions = posting.get("actions")
    raw_urls = []
    if isinstance(actions, dict) and actions.get("details"):
        raw_urls.append(actions["details"])
    if posting.get("ref"):
        raw_urls.append(posting["ref"])
    for raw_url in raw_urls:
        candidate = urljoin(board.url, str(raw_url).strip())
        if _is_public_detail_url(candidate, board.identifier):
            return _canonical_url(candidate)
    if posting.get("id") and board.identifier:
        candidate = (
            f"https://{_PUBLIC_HOST}/{quote(board.identifier, safe='-._~')}/"
            f"{quote(str(posting['id']).strip(), safe='-._~')}"
        )
        return candidate if _is_public_detail_url(candidate, board.identifier) else ""
    return ""


def _is_company_api_url(url: str, identifier: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == _API_HOST
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and len(parts) == 4
        and parts[:2] == ["v1", "companies"]
        and parts[2].casefold() == identifier.casefold()
        and parts[3] == "postings"
    )


def _is_public_detail_url(url: str, identifier: str | None) -> bool:
    if not identifier:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == _PUBLIC_HOST
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and len(parts) >= 2
        and parts[0].casefold() == identifier.casefold()
        and parts[1]
    )


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _nonnegative_int(value) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _positive_int(value) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _normalized_title(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _location_name(posting: dict) -> str | None:
    location = posting.get("location")
    if not isinstance(location, dict):
        return None
    if location.get("fullLocation"):
        return str(location["fullLocation"]).strip() or None

    parts = []
    for key in ("city", "region", "country"):
        value = str(location.get(key) or "").strip()
        if value and value.casefold() not in {part.casefold() for part in parts}:
            parts.append(value)
    return ", ".join(parts) or None


ADAPTER = SmartRecruitersAdapter()
