from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_API_HOST = "api.smartrecruiters.com"
_PUBLIC_HOST = "jobs.smartrecruiters.com"
_LEGACY_API_HOST = "www.smartrecruiters.com"
_PAGE_SIZE = 100
_MAX_PAGES = 5
_MAX_HTML_CHARS = 2_000_000
_TENANT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$")
_CONFIG_VALUE = re.compile(
    r'''(?<![A-Za-z0-9_$])["']?(company_code|api_url|job_ad_url)["']?'''
    r'''\s*[:=]\s*(["'])(.*?)(?<!\\)\2''',
    re.IGNORECASE | re.DOTALL,
)
_STATIC_HOST = "static.smartrecruiters.com"


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
            replay_safe=True,
        )

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        if _safe_public_page_url(page.final_url or page.url) is None:
            return None
        tenant = _widget_tenant(page.html)
        if tenant is None:
            return None
        return JobBoard(
            url=f"https://{_PUBLIC_HOST}/{quote(tenant, safe='-._~')}",
            provider=self.name,
            identifier=tenant,
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "error": "missing SmartRecruiters company identifier",
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        api_urls: list[str] = []
        errors: list[dict[str, str]] = []
        total_found: int | None = None
        offset = 0
        exact_title_found = False
        inventory_complete = False
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
                    inventory_scope=inventory_scope,
                    inventory_complete=False,
                    trace={
                        "adapter": self.name,
                        "api_urls": api_urls,
                        "error": "SmartRecruiters API redirected outside the company endpoint",
                        "inventory_scope": inventory_scope,
                        "inventory_complete": False,
                    },
                )
            try:
                data = json.loads(page.html)
            except (json.JSONDecodeError, TypeError):
                return self._invalid_response(board, api_urls, inventory_scope, candidates)
            postings = data.get("content") if isinstance(data, dict) else None
            if not isinstance(postings, list):
                return self._invalid_response(board, api_urls, inventory_scope, candidates)

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
                        raw={
                            "id": posting.get("id"),
                            "company_identifier": _company_identifier(posting),
                            "company_name": _company_name(posting),
                        },
                    )
                )
                if normalized_target and _normalized_title(title) == normalized_target:
                    exact_title_found = True

            page_total = _nonnegative_int(data.get("totalFound"))
            if page_total is not None:
                total_found = max(total_found or 0, page_total)
            response_offset = _nonnegative_int(data.get("offset"))
            response_limit = _positive_int(data.get("limit")) or _PAGE_SIZE
            next_offset = (response_offset if response_offset is not None else offset) + len(postings)
            inventory_complete = bool(
                not postings
                or (total_found is None and len(postings) < response_limit)
                or (total_found is not None and next_offset >= total_found)
            )
            if inventory_complete:
                break
            offset = next_offset

        tenant_identity_verified = bool(candidates) and all(
            str(candidate.raw.get("company_identifier") or "").casefold()
            == board.identifier.casefold()
            for candidate in candidates
        )
        tenant_identity_conflict = any(
            isinstance(candidate.raw.get("company_identifier"), str)
            and bool(candidate.raw["company_identifier"].strip())
            and candidate.raw["company_identifier"].casefold()
            != board.identifier.casefold()
            for candidate in candidates
        )
        if tenant_identity_conflict:
            inventory_complete = False
            reason_code = "INVALID_STRUCTURED_DATA"
        else:
            reason_code = None if candidates else (
                "PROVIDER_FETCH_FAILED" if errors else "EMPTY_PROVIDER_RESPONSE"
            )
        exposed_candidates = (
            candidates
            if inventory_complete and not tenant_identity_conflict
            else []
        )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=exposed_candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "api_urls": api_urls,
                "candidate_count": len(candidates),
                "exposed_candidate_count": len(exposed_candidates),
                "page_count": len(api_urls) - len(errors),
                "total_found": total_found,
                "exact_title_found": exact_title_found,
                "tenant_identity_verified": tenant_identity_verified,
                "tenant_identity_conflict": tenant_identity_conflict,
                "errors": errors,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )

    def _invalid_response(
        self,
        board: JobBoard,
        api_urls: list[str],
        inventory_scope: str,
        candidates: list[JobCandidate],
    ) -> AdapterResult:
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=[],
            reason_code="INVALID_STRUCTURED_DATA",
            inventory_scope=inventory_scope,
            inventory_complete=False,
            trace={
                "adapter": self.name,
                "api_urls": api_urls,
                "candidate_count": len(candidates),
                "exposed_candidate_count": 0,
                "inventory_scope": inventory_scope,
                "inventory_complete": False,
            },
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


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_widget_script = False
        self.inline_scripts: list[str] = []
        self._inline_data: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        source = attributes.get("src", "").strip()
        if source:
            if _is_widget_script_url(source):
                self.has_widget_script = True
            self._inline_data = None
        else:
            self._inline_data = []

    def handle_data(self, data: str) -> None:
        if self._inline_data is not None:
            self._inline_data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inline_data is not None:
            self.inline_scripts.append("".join(self._inline_data))
            self._inline_data = None


def _widget_tenant(html: str) -> str | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _WidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    if not parser.has_widget_script:
        return None

    tenants: dict[str, str] = {}
    saw_config = False
    for script in parser.inline_scripts:
        configs = _script_configs(script)
        if configs is None:
            return None
        for fields in configs:
            saw_config = True
            company_codes = {
                company_code.casefold(): company_code
                for company_code in fields["company_code"]
            }
            api_urls = set(fields.get("api_url", ()))
            job_ad_urls = set(fields.get("job_ad_url", ()))
            if len(company_codes) != 1 or len(api_urls) != 1 or len(job_ad_urls) > 1:
                return None
            tenant = next(iter(company_codes.values()))
            if not _TENANT.fullmatch(tenant):
                return None
            api_tenant = _config_api_tenant(next(iter(api_urls)))
            job_ad_tenant = (
                _config_job_ad_tenant(next(iter(job_ad_urls))) if job_ad_urls else None
            )
            if api_tenant is False or job_ad_tenant is False:
                return None
            if any(
                url_tenant is not None and url_tenant.casefold() != tenant.casefold()
                for url_tenant in (api_tenant, job_ad_tenant)
            ):
                return None
            tenants.setdefault(tenant.casefold(), tenant)
    if not saw_config or len(tenants) != 1:
        return None
    return next(iter(tenants.values()))


def _decode_script_string(value: str, quote_character: str) -> str:
    if quote_character == '"':
        try:
            decoded = json.loads(f'"{value}"')
            return decoded.strip() if isinstance(decoded, str) else ""
        except json.JSONDecodeError:
            return ""
    return value.replace(r"\/", "/").replace(r"\'", "'").replace(r"\\", "\\").strip()


def _script_configs(script: str) -> list[dict[str, list[str]]] | None:
    source = _without_js_comments(script)
    matches = list(_CONFIG_VALUE.finditer(source))
    company_matches = [match for match in matches if match.group(1).casefold() == "company_code"]
    if not company_matches:
        return []

    spans = _object_spans(source)
    config_spans: set[tuple[int, int]] = set()
    unscoped = []
    for match in company_matches:
        containing = [span for span in spans if span[0] < match.start() < span[1]]
        if containing:
            config_spans.add(min(containing, key=lambda span: span[1] - span[0]))
        else:
            unscoped.append(match)
    if unscoped:
        if config_spans or len(unscoped) != 1:
            return None
        config_spans.add((0, len(source)))

    configs = []
    for start, end in sorted(config_spans):
        fields: dict[str, list[str]] = {}
        for match in matches:
            if start <= match.start() < end:
                fields.setdefault(match.group(1).casefold(), []).append(
                    _decode_script_string(match.group(3), match.group(2))
                )
        configs.append(fields)
    return configs


def _without_js_comments(source: str) -> str:
    characters = list(source)
    quote_character: str | None = None
    index = 0
    while index < len(characters):
        character = characters[index]
        if quote_character is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_character:
                quote_character = None
            index += 1
            continue
        if character in {'"', "'", "`"}:
            quote_character = character
            index += 1
            continue
        if character == "/" and index + 1 < len(characters):
            next_character = characters[index + 1]
            if next_character == "/":
                end = source.find("\n", index + 2)
                end = len(characters) if end < 0 else end
                characters[index:end] = " " * (end - index)
                index = end
                continue
            if next_character == "*":
                end = source.find("*/", index + 2)
                end = len(characters) if end < 0 else end + 2
                characters[index:end] = " " * (end - index)
                index = end
                continue
        index += 1
    return "".join(characters)


def _object_spans(source: str) -> list[tuple[int, int]]:
    spans = []
    starts = []
    quote_character: str | None = None
    index = 0
    while index < len(source):
        character = source[index]
        if quote_character is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_character:
                quote_character = None
        elif character in {'"', "'", "`"}:
            quote_character = character
        elif character == "{":
            starts.append(index)
        elif character == "}" and starts:
            spans.append((starts.pop(), index + 1))
        index += 1
    return spans


def _is_widget_script_url(url: str) -> bool:
    parsed = _safe_official_url(url, _STATIC_HOST)
    if parsed is None or parsed.query or parsed.fragment:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        len(parts) >= 2
        and parts[0].casefold() == "job-widget"
        and parts[-1].casefold().endswith(".js")
    )


def _config_api_tenant(url: str) -> str | None | bool:
    try:
        host = (urlparse(url).hostname or "").casefold()
    except (TypeError, ValueError):
        return False
    if host == _LEGACY_API_HOST:
        parsed = _safe_official_url(url, _LEGACY_API_HOST)
        if (
            parsed is not None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            return None
        return False
    parsed = _safe_official_url(url, _API_HOST)
    if parsed is None or parsed.query or parsed.fragment:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or [part.casefold() for part in parts[:2]] != ["v1", "companies"]:
        return False
    if len(parts) == 2:
        return None
    if len(parts) not in {3, 4} or (len(parts) == 4 and parts[3] != "postings"):
        return False
    return parts[2] if _TENANT.fullmatch(parts[2]) else False


def _config_job_ad_tenant(url: str) -> str | None | bool:
    parsed = _safe_official_url(url, _PUBLIC_HOST)
    if parsed is None or parsed.query or parsed.fragment:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 and _TENANT.fullmatch(parts[0]) else False


def _safe_official_url(url: str, host: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _safe_public_page_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host == "localhost"
        or host.endswith(".localhost")
    ):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    return parsed


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


def _company_identifier(posting: dict) -> str | None:
    company = posting.get("company")
    if not isinstance(company, dict):
        return None
    value = company.get("identifier")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _company_name(posting: dict) -> str | None:
    company = posting.get("company")
    if not isinstance(company, dict):
        return None
    value = company.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None


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
