from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "apply.workable.com"
_API_PATH_PREFIX = "/api/v3/accounts/"
_MAX_API_PAGES = 5
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SHORTCODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_FIELDS = ("url", "jobUrl", "job_url", "applicationUrl", "application_url", "href")
_TITLE_FIELDS = ("title", "name", "jobTitle", "job_title")
_LOCATION_FIELDS = ("location", "workplace", "jobLocation")
_PAGINATION_KEYS = {
    "currentpage",
    "current_page",
    "hasnextpage",
    "has_next_page",
    "next",
    "nextpage",
    "next_page",
    "nexturl",
    "next_url",
    "page",
    "pagecount",
    "page_count",
    "total",
    "totalcount",
    "total_count",
    "totalpages",
    "total_pages",
}


class WorkableAdapter:
    name = "workable"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _parsed_workable_url(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _parsed_workable_url(url)
        if parsed is None:
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts or not _IDENTIFIER_PATTERN.fullmatch(parts[0]):
            return None
        identifier = parts[0]
        return JobBoard(
            url=f"https://{_HOST}/{quote(identifier, safe='-_')}/",
            provider=self.name,
            identifier=identifier,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        if not board.identifier or not _IDENTIFIER_PATTERN.fullmatch(board.identifier):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "error": "missing Workable account identifier",
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        board_url = f"https://{_HOST}/{quote(board.identifier, safe='-_')}/"
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "error": str(error),
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        final_board_url = page.final_url or page.url
        if not _is_account_board_url(final_board_url, board.identifier):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "error": "Workable board redirected outside the account",
                    "rejected_final_url": final_board_url,
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        parser = _WorkableHTMLParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            parser = _WorkableHTMLParser()

        payloads = _json_payloads(parser.scripts)
        candidates = _anchor_candidates(parser.links, board.identifier)
        found_jobs_container = bool(candidates)
        pagination: dict[str, object] = {}
        for payload in payloads:
            found_jobs_container = found_jobs_container or _contains_jobs_container(payload)
            for record in _walk_records(payload):
                candidate = _candidate(record, board.identifier)
                if candidate is not None:
                    candidates.append(candidate)
            _collect_pagination(payload, pagination)
        candidates = _dedupe_candidates(candidates)

        api_urls: list[str] = []
        api_errors: list[dict[str, str]] = []
        api_page_count = 0
        total_found: int | None = None
        normalized_target = _normalized_title(query.title)
        exact_title_found = bool(
            normalized_target
            and any(_normalized_title(candidate.title) == normalized_target for candidate in candidates)
        )
        inventory_complete = found_jobs_container and not _pagination_has_more(
            pagination,
            len(candidates),
        )

        # Current public Workable boards are client-rendered shells. Their own
        # frontend reads this public cursor API; retain HTML parsing above for
        # older/static variants and use the API only when the shell has no jobs.
        if not candidates and not found_jobs_container:
            inventory_complete = False
            token: str | None = None
            seen_tokens: set[str] = set()
            records_seen = 0
            for _ in range(_MAX_API_PAGES):
                api_url = _api_url(board.identifier)
                api_urls.append(api_url)
                request = _api_request(query, token)
                try:
                    response = fetcher.fetch(
                        api_url,
                        data=json.dumps(request).encode("utf-8"),
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "Referer": board_url,
                        },
                    )
                except (FetchError, OSError, TimeoutError) as error:
                    api_errors.append({"url": api_url, "error": str(error)})
                    break

                response_url = response.final_url or response.url
                if not _is_account_api_url(response_url, board.identifier):
                    return AdapterResult(
                        provider=self.name,
                        board=board,
                        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                        inventory_scope=inventory_scope,
                        inventory_complete=False,
                        trace={
                            "adapter": self.name,
                            "board_urls": [board_url],
                            "api_urls": api_urls,
                            "error": "Workable API redirected outside the account endpoint",
                            "rejected_final_url": response_url,
                            "inventory_scope": inventory_scope,
                            "inventory_complete": False,
                        },
                    )

                try:
                    payload = json.loads(response.html)
                except (json.JSONDecodeError, TypeError):
                    return _invalid_api_response(
                        board,
                        board_url,
                        api_urls,
                        inventory_scope,
                        candidates,
                    )
                records = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(records, list):
                    return _invalid_api_response(
                        board,
                        board_url,
                        api_urls,
                        inventory_scope,
                        candidates,
                    )

                api_page_count += 1
                records_seen += len(records)
                found_jobs_container = True
                page_total = _nonnegative_int(payload.get("total"))
                if page_total is not None:
                    total_found = max(total_found or 0, page_total)
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    candidate = _candidate(record, board.identifier)
                    if candidate is not None:
                        candidates.append(candidate)
                        if normalized_target and _normalized_title(candidate.title) == normalized_target:
                            exact_title_found = True
                candidates = _dedupe_candidates(candidates)

                next_token = payload.get("nextPage")
                has_next_token = isinstance(next_token, str) and bool(next_token.strip())
                inventory_complete = bool(
                    not records
                    or (total_found is not None and records_seen >= total_found)
                    or not has_next_token
                )
                repeated_token = has_next_token and next_token in seen_tokens
                if exact_title_found or inventory_complete or repeated_token:
                    break
                seen_tokens.add(next_token)
                token = next_token

        if candidates:
            reason_code = None
        elif api_errors:
            reason_code = "PROVIDER_FETCH_FAILED"
        elif found_jobs_container:
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        else:
            reason_code = "INVALID_STRUCTURED_DATA"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "board_urls": [board_url],
                "api_urls": api_urls,
                "response_source": page.source,
                "payload_count": len(payloads),
                "public_link_count": len(parser.links),
                "candidate_count": len(candidates),
                "pagination": pagination,
                "api_page_count": api_page_count,
                "total_found": total_found,
                "exact_title_found": exact_title_found,
                "errors": api_errors,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
        )


def _parsed_workable_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    standard_port = port is None or (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    if (
        scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or not standard_port
        or (parsed.hostname or "").casefold() != _HOST
    ):
        return None
    return parsed


def _is_account_board_url(url: str, account: str) -> bool:
    parsed = _parsed_workable_url(url)
    if parsed is None:
        return False
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    return bool(parts and parts[0].casefold() == account.casefold())


def _api_url(account: str) -> str:
    return f"https://{_HOST}{_API_PATH_PREFIX}{quote(account, safe='-_')}/jobs"


def _is_account_api_url(url: str, account: str) -> bool:
    parsed = _parsed_workable_url(url)
    if parsed is None or parsed.query or parsed.fragment:
        return False
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    return bool(
        len(parts) == 5
        and parts[:3] == ["api", "v3", "accounts"]
        and parts[3].casefold() == account.casefold()
        and parts[4] == "jobs"
    )


def _api_request(query: JobQuery, token: str | None) -> dict[str, object]:
    request: dict[str, object] = {
        "query": query.title.strip() if query.title else "",
        "location": [],
        "department": [],
        "worktype": [],
        "remote": [],
    }
    if token:
        request["token"] = token
    return request


def _invalid_api_response(
    board: JobBoard,
    board_url: str,
    api_urls: list[str],
    inventory_scope: str,
    candidates: list[JobCandidate],
) -> AdapterResult:
    return AdapterResult(
        provider="workable",
        board=board,
        candidates=candidates,
        reason_code=None if candidates else "INVALID_STRUCTURED_DATA",
        inventory_scope=inventory_scope,
        inventory_complete=False,
        trace={
            "adapter": "workable",
            "board_urls": [board_url],
            "api_urls": api_urls,
            "candidate_count": len(candidates),
            "inventory_scope": inventory_scope,
            "inventory_complete": False,
        },
    )


def _pagination_has_more(pagination: dict[str, object], candidate_count: int) -> bool:
    normalized = {key.casefold(): value for key, value in pagination.items()}
    for key in ("hasnextpage", "has_next_page"):
        if normalized.get(key) is True:
            return True
    for key in ("next", "nextpage", "next_page", "nexturl", "next_url"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            return True
    item_total = _nonnegative_int(
        normalized.get("total")
        or normalized.get("totalcount")
        or normalized.get("total_count")
    )
    if item_total is not None and candidate_count < item_total:
        return True
    current = _nonnegative_int(normalized.get("currentpage") or normalized.get("current_page"))
    total = _nonnegative_int(
        normalized.get("totalpages")
        or normalized.get("total_pages")
        or normalized.get("pagecount")
        or normalized.get("page_count")
    )
    return current is not None and total is not None and current < total


class _WorkableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._script_parts: list[str] | None = None
        self._link_href = ""
        self._link_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script":
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self._link_href = attributes["href"]
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._link_parts is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts).strip())
            self._script_parts = None
        elif tag.casefold() == "a" and self._link_parts is not None:
            title = " ".join("".join(self._link_parts).split())
            self.links.append((self._link_href, title))
            self._link_href = ""
            self._link_parts = None


def _json_payloads(scripts: list[str]) -> list[object]:
    payloads: list[object] = []
    decoder = json.JSONDecoder()
    for script in scripts:
        text = unescape(script).strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            starts = [index for token in ("{", "[") if (index := text.find(token)) >= 0]
            if not starts:
                continue
            try:
                payload, _ = decoder.raw_decode(text[min(starts):])
            except (json.JSONDecodeError, TypeError):
                continue
        payloads.append(payload)
    return payloads


def _walk_records(value: object):
    if isinstance(value, dict):
        if _looks_like_job(value):
            yield value
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                yield from _walk_records(json.loads(text))
            except json.JSONDecodeError:
                return


def _looks_like_job(record: dict) -> bool:
    return bool(_first_text(record, _TITLE_FIELDS) and _record_shortcode(record, account=None))


def _contains_jobs_container(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"jobs", "joblist", "job_list", "positions", "openings"}:
                return isinstance(child, (list, dict, str))
            if _contains_jobs_container(child):
                return True
    elif isinstance(value, list):
        return any(_contains_jobs_container(child) for child in value)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _contains_jobs_container(json.loads(text))
            except json.JSONDecodeError:
                return False
    return False


def _anchor_candidates(links: list[tuple[str, str]], account: str) -> list[JobCandidate]:
    candidates = []
    for raw_url, title in links:
        detail = _validated_detail(raw_url, account)
        if detail is None or not title:
            continue
        candidates.append(
            JobCandidate(title=title, url=detail[0], provider="workable", raw={"shortcode": detail[1]})
        )
    return candidates


def _candidate(record: dict, account: str) -> JobCandidate | None:
    title = _first_text(record, _TITLE_FIELDS)
    if not title:
        return None
    shortcode = _record_shortcode(record, account=account)
    if not shortcode:
        return None
    detail_url = f"https://{_HOST}/{quote(account, safe='-_')}/j/{quote(shortcode, safe='-_')}/"
    location = next((_location(record.get(field)) for field in _LOCATION_FIELDS if record.get(field)), None)
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="workable",
        location=location,
        raw={"shortcode": shortcode},
    )


def _record_shortcode(record: dict, account: str | None) -> str | None:
    explicit_url = _first_text(record, _URL_FIELDS)
    if explicit_url:
        if account is None:
            try:
                parsed = urlparse(urljoin(f"https://{_HOST}/", explicit_url))
            except (TypeError, ValueError):
                return None
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            return parts[-1] if len(parts) >= 3 and parts[-2] == "j" and _SHORTCODE_PATTERN.fullmatch(parts[-1]) else None
        detail = _validated_detail(explicit_url, account)
        return detail[1] if detail else None

    for field in ("shortcode", "shortCode", "code"):
        value = record.get(field)
        if isinstance(value, (str, int)):
            shortcode = str(value).strip()
            if _SHORTCODE_PATTERN.fullmatch(shortcode):
                return shortcode
    return None


def _validated_detail(raw_url: str, account: str) -> tuple[str, str] | None:
    try:
        parsed = _parsed_workable_url(urljoin(f"https://{_HOST}/{quote(account, safe='-_')}/", raw_url))
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed.query or parsed.fragment:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        len(parts) != 3
        or parts[0].casefold() != account.casefold()
        or parts[1] != "j"
        or not _SHORTCODE_PATTERN.fullmatch(parts[2])
    ):
        return None
    return f"https://{_HOST}/{quote(account, safe='-_')}/j/{quote(parts[2], safe='-_')}/", parts[2]


def _first_text(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _location(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("name", "location_str", "fullLocation"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    parts = []
    for key in ("city", "region", "country"):
        item = value.get(key)
        if isinstance(item, str) and item.strip() and item.strip().casefold() not in {part.casefold() for part in parts}:
            parts.append(item.strip())
    return ", ".join(parts) or None


def _collect_pagination(value: object, output: dict[str, object]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _PAGINATION_KEYS and isinstance(child, (str, int, float, bool, type(None))):
                output.setdefault(key, child)
            _collect_pagination(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_pagination(child, output)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                _collect_pagination(json.loads(text), output)
            except json.JSONDecodeError:
                return


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    output = []
    seen = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        output.append(candidate)
    return output


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


ADAPTER = WorkableAdapter()
