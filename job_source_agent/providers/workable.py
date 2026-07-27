from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse

from ..provider_candidates import ProviderPublishedEmployerEvidence
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "apply.workable.com"
_API_PATH_PREFIX = "/api/v3/accounts/"
_WIDGET_API_PATH_PREFIX = "/api/v1/widget/accounts/"
_WIDGET_ASSET_URL = "https://www.workable.com/assets/embed.js"
_MAX_API_PAGES = 5
_MAX_WIDGET_HTML_CHARS = 2_000_000
_MAX_WIDGET_RESPONSE_CHARS = 8_000_000
_MAX_WIDGET_RECORDS = 1_000
_MAX_WIDGET_FIELD_CHARS = 20_000
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SHORTCODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_WIDGET_IDENTIFIER_PATTERN = re.compile(r"^widget:([1-9][0-9]{0,18})$")
_WIDGET_SHORTCODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
_WIDGET_CALL_PATTERN = re.compile(
    r"\bwhr_embed\s*\(\s*([1-9][0-9]{0,18})\s*(?:,|\))"
)
_PUBLIC_HOSTNAME_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_PUBLISHED_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_ACCOUNT_UID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
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
        if (
            not parts
            or parts[0].casefold() in {"api", "j"}
            or not _IDENTIFIER_PATTERN.fullmatch(parts[0])
        ):
            return None
        identifier = parts[0]
        return JobBoard(
            url=f"https://{_HOST}/{quote(identifier, safe='-_')}/",
            provider=self.name,
            identifier=identifier,
        )

    def identify_board_from_page(self, page) -> JobBoard | None:
        page_url = _safe_public_career_url(page.final_url or page.url)
        if page_url is None or not isinstance(page.html, str):
            return None
        if len(page.html) > _MAX_WIDGET_HTML_CHARS:
            return None
        parser = _WorkableHTMLParser()
        try:
            parser.feed(page.html)
        except (TypeError, ValueError):
            return None
        account_id = _widget_account_id(parser, page_url)
        if account_id is None:
            return None
        return JobBoard(
            url=page_url,
            provider=self.name,
            identifier=f"widget:{account_id}",
            replay_safe=False,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        widget_identity = _widget_board_identity(board)
        if widget_identity is not None:
            return self._list_widget_jobs(
                fetcher,
                board,
                query,
                account_id=widget_identity,
            )
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

        parser = _WorkableHTMLParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            parser = _WorkableHTMLParser()

        final_board_url = page.final_url or page.url
        account_uid: str | None = None
        if not _is_account_board_url(final_board_url, board.identifier):
            account_uid = _custom_board_account_uid(
                final_board_url,
                parser.metadata,
                board.identifier,
            )
        if not _is_account_board_url(final_board_url, board.identifier) and account_uid is None:
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
                if account_uid is not None and not _records_match_account_uid(
                    records,
                    account_uid,
                ):
                    return AdapterResult(
                        provider=self.name,
                        board=board,
                        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                        inventory_scope=inventory_scope,
                        inventory_complete=False,
                        trace={
                            "adapter": self.name,
                            "board_urls": [board_url],
                            "board_final_url": final_board_url,
                            "api_urls": api_urls,
                            "account_uid": account_uid,
                            "error": "Workable API response did not preserve account identity",
                            "inventory_scope": inventory_scope,
                            "inventory_complete": False,
                        },
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
                "board_final_url": final_board_url,
                "api_urls": api_urls,
                "account_uid": account_uid,
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

    def _list_widget_jobs(
        self,
        fetcher,
        board: JobBoard,
        query: JobQuery,
        *,
        account_id: str,
    ) -> AdapterResult:
        api_url = _widget_api_url(account_id)
        try:
            page = fetcher.fetch(
                api_url,
                headers={
                    "Accept": "application/javascript, application/json",
                    "Referer": board.url,
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _widget_result(
                board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_complete=False,
                api_url=api_url,
                error=str(error),
            )

        if not _is_widget_api_url(page.final_url or page.url, account_id):
            return _widget_result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                api_url=api_url,
                rejected_final_url=page.final_url or page.url,
            )
        payload = _widget_payload(page.html)
        if payload is None:
            return _widget_result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                api_url=api_url,
                response_source=page.source,
            )

        employer_name = _bounded_public_text(payload.get("name"))
        records = payload.get("jobs")
        if (
            employer_name is None
            or not isinstance(records, list)
            or len(records) > _MAX_WIDGET_RECORDS
        ):
            return _widget_result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                api_url=api_url,
                response_source=page.source,
            )

        candidates: list[JobCandidate] = []
        employer_evidence: list[ProviderPublishedEmployerEvidence] = []
        seen_shortcodes: set[str] = set()
        seen_urls: set[str] = set()
        for record in records:
            parsed = _widget_candidate(record, account_id, employer_name)
            if parsed is None:
                return _widget_result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    api_url=api_url,
                    response_source=page.source,
                )
            shortcode, candidate = parsed
            if shortcode in seen_shortcodes or candidate.url in seen_urls:
                return _widget_result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    api_url=api_url,
                    response_source=page.source,
                )
            seen_shortcodes.add(shortcode)
            seen_urls.add(candidate.url)
            candidates.append(candidate)
            try:
                employer_evidence.append(
                    ProviderPublishedEmployerEvidence(
                        employer_name=employer_name,
                        descriptor_terms=(),
                        evidence_url=api_url,
                        opening_url=candidate.url,
                        extraction_method="workable_widget_employer",
                    )
                )
            except (TypeError, ValueError):
                return _widget_result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    api_url=api_url,
                    response_source=page.source,
                )

        normalized_target = _normalized_title(query.title)
        exact_title_found = bool(
            normalized_target
            and any(
                _normalized_title(candidate.title) == normalized_target
                for candidate in candidates
            )
        )
        return _widget_result(
            board,
            candidates=candidates,
            employer_evidence=employer_evidence,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=True,
            api_url=api_url,
            response_source=page.source,
            employer_name=employer_name,
            exact_title_found=exact_title_found,
        )


def _safe_public_career_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 8_192:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or not _PUBLIC_HOSTNAME_PATTERN.fullmatch(host)
        or "." not in host
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    path = parsed.path or "/"
    return urlunparse(("https", host, path, "", "", ""))


def _widget_account_id(
    parser: "_WorkableHTMLParser",
    page_url: str,
) -> str | None:
    assets = {
        asset
        for source in parser.script_sources
        if (asset := _safe_widget_asset_url(source, page_url)) is not None
    }
    if assets != {_WIDGET_ASSET_URL} or "whr_embed_hook" not in parser.element_ids:
        return None
    account_ids = {
        match.group(1)
        for script in parser.scripts
        for match in _WIDGET_CALL_PATTERN.finditer(_javascript_code_only(script))
    }
    return next(iter(account_ids)) if len(account_ids) == 1 else None


def _safe_widget_asset_url(raw_url: str, page_url: str) -> str | None:
    try:
        parsed = urlparse(urljoin(page_url, raw_url))
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or (parsed.hostname or "").casefold() != "www.workable.com"
        or parsed.path != "/assets/embed.js"
        or parsed.query
        or parsed.fragment
    ):
        return None
    return _WIDGET_ASSET_URL


def _widget_board_identity(board: JobBoard) -> str | None:
    if (
        board.provider != "workable"
        or not isinstance(board.identifier, str)
        or _safe_public_career_url(board.url) != board.url
        or board.replay_safe
    ):
        return None
    match = _WIDGET_IDENTIFIER_PATTERN.fullmatch(board.identifier)
    return match.group(1) if match else None


def _widget_api_url(account_id: str) -> str:
    return (
        f"https://{_HOST}{_WIDGET_API_PATH_PREFIX}{account_id}"
        "?origin=embed&callback=whrcallback"
    )


def _is_widget_api_url(value: str, account_id: str) -> bool:
    parsed = _parsed_workable_url(value)
    if parsed is None or parsed.scheme.casefold() != "https" or parsed.fragment:
        return False
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    try:
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        return False
    return bool(
        parts == ["api", "v1", "widget", "accounts", account_id]
        and query == [("origin", "embed"), ("callback", "whrcallback")]
    )


def _widget_payload(body: object) -> dict[str, Any] | None:
    if not isinstance(body, str) or len(body) > _MAX_WIDGET_RESPONSE_CHARS:
        return None
    match = re.fullmatch(
        r"\s*/\*\*/whrcallback\((.*)\)\s*;?\s*",
        body,
        flags=re.DOTALL,
    )
    if match is None:
        return None

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(match.group(1), object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _javascript_code_only(source: str) -> str:
    """Blank JS comments and literals before recognizing an embed invocation."""

    output = list(source)
    state = "code"
    index = 0
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if current == "/" and following == "/":
                output[index] = output[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if current == "/" and following == "*":
                output[index] = output[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if current in {"'", '"', "`"}:
                output[index] = " "
                state = {"'": "single", '"': "double", "`": "template"}[current]
                index += 1
                continue
            index += 1
            continue
        if state == "line_comment":
            if current in {"\r", "\n"}:
                state = "code"
            else:
                output[index] = " "
            index += 1
            continue
        if state == "block_comment":
            output[index] = " "
            if current == "*" and following == "/":
                output[index + 1] = " "
                state = "code"
                index += 2
            else:
                index += 1
            continue
        output[index] = " "
        if current == "\\" and following:
            output[index + 1] = " "
            index += 2
            continue
        expected = {"single": "'", "double": '"', "template": "`"}[state]
        if current == expected:
            state = "code"
        index += 1
    return "".join(output)


def _widget_candidate(
    value: object,
    account_id: str,
    employer_name: str,
) -> tuple[str, JobCandidate] | None:
    if not isinstance(value, dict):
        return None
    title = _bounded_public_text(value.get("title"))
    shortcode = _bounded_public_text(value.get("shortcode"))
    published_on = _bounded_public_text(value.get("published_on"))
    if (
        title is None
        or shortcode is None
        or not _WIDGET_SHORTCODE_PATTERN.fullmatch(shortcode)
        or published_on is None
        or not _PUBLISHED_DATE_PATTERN.fullmatch(published_on)
    ):
        return None
    opening_url = f"https://{_HOST}/j/{quote(shortcode, safe='-_')}"
    application_url = f"{opening_url}/apply"
    for field, expected in (
        ("url", opening_url),
        ("shortlink", opening_url),
        ("application_url", application_url),
    ):
        raw_url = value.get(field)
        if not isinstance(raw_url, str) or raw_url != expected:
            return None
    location = _widget_location(value)
    if location is None:
        return None
    candidate = JobCandidate(
        title=title,
        url=opening_url,
        provider="workable",
        location=location,
        raw={
            "shortcode": shortcode,
            "account_id": account_id,
            "published_on": published_on,
            "hiring_organization_name": employer_name,
            "inventory_source": "public_widget_api",
        },
    )
    return shortcode, candidate


def _widget_location(value: dict[str, Any]) -> str | None:
    locations = value.get("locations")
    if not isinstance(locations, list) or len(locations) > 100:
        return None
    rendered: list[str] = ["Remote"] if value.get("telecommuting") is True else []
    for location in locations:
        if not isinstance(location, dict) or location.get("hidden") is not False:
            return None
        parts: list[str] = []
        for key in ("city", "region", "country"):
            item = _bounded_public_text(location.get(key))
            if item and item.casefold() not in {part.casefold() for part in parts}:
                parts.append(item)
        if not parts:
            return None
        text = ", ".join(parts)
        if text not in rendered:
            rendered.append(text)
    return "; ".join(rendered) if rendered else None


def _bounded_public_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > _MAX_WIDGET_FIELD_CHARS:
        return None
    return normalized


def _widget_result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    employer_evidence: list[ProviderPublishedEmployerEvidence] | None = None,
    reason_code: str | None,
    retryable: bool = False,
    inventory_complete: bool,
    api_url: str,
    error: str | None = None,
    rejected_final_url: str | None = None,
    response_source: str | None = None,
    employer_name: str | None = None,
    exact_title_found: bool | None = None,
) -> AdapterResult:
    trace: dict[str, Any] = {
        "adapter": "workable",
        "variant": "public_numeric_widget_v1",
        "board_identity": {
            "provider": "workable",
            "url": board.url,
            "identifier": board.identifier or "",
            "runtime_only": True,
        },
        "board_urls": [board.url],
        "api_urls": [api_url],
        "account_id": board.identifier.removeprefix("widget:")
        if isinstance(board.identifier, str)
        else None,
        "candidate_count": len(candidates or ()),
        "inventory_verified_opening_urls": [
            candidate.url for candidate in candidates or ()
        ] if inventory_complete else [],
        "inventory_scope": "full" if inventory_complete else "partial",
        "inventory_complete": inventory_complete,
    }
    optional = {
        "error": error,
        "rejected_final_url": rejected_final_url,
        "response_source": response_source,
        "employer_name": employer_name,
        "exact_title_found": exact_title_found,
    }
    trace.update({key: value for key, value in optional.items() if value is not None})
    return AdapterResult(
        provider="workable",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "partial",
        inventory_complete=inventory_complete,
        employer_evidence=tuple(employer_evidence or ()),
        trace=trace,
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


def _custom_board_account_uid(
    url: str,
    metadata: dict[str, str],
    account: str,
) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or not parsed.hostname
        or metadata.get("domain", "").casefold() != "workable.com"
        or metadata.get("subdomain", "").casefold() != account.casefold()
    ):
        return None
    account_uid = metadata.get("account", "").strip()
    return account_uid if _ACCOUNT_UID_PATTERN.fullmatch(account_uid) else None


def _records_match_account_uid(records: list[object], account_uid: str) -> bool:
    for record in records:
        if not isinstance(record, dict):
            return False
        record_uid = record.get("accountUid")
        if not isinstance(record_uid, str) or record_uid.casefold() != account_uid.casefold():
            return False
    return True


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
        self.script_sources: list[str] = []
        self.element_ids: set[str] = set()
        self.links: list[tuple[str, str]] = []
        self.metadata: dict[str, str] = {}
        self._script_parts: list[str] | None = None
        self._link_href = ""
        self._link_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if attributes.get("id"):
            self.element_ids.add(attributes["id"])
        if tag.casefold() == "script":
            if attributes.get("src"):
                self.script_sources.append(attributes["src"])
            self._script_parts = []
        elif tag.casefold() == "meta" and attributes.get("name"):
            self.metadata.setdefault(
                attributes["name"].casefold(),
                attributes.get("content", ""),
            )
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
