from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

from .contracts import FetchClient
from .web import FetchError, Page, normalize_url


MAX_INVENTORY_BYTES = 5_000_000
MAX_INVENTORY_ITEMS = 5_000
ProviderBoardIdentity = Callable[[str], tuple[str, str] | None]

_LITERAL_GET = re.compile(
    r"(?P<client>[A-Za-z_$][A-Za-z0-9_$]{0,79})\.get\(\s*"
    r"[\"'](?P<route>/[^\"']{1,200})[\"']\s*\)",
)
_IMPORT_BLOCK = re.compile(
    r"import\s*\{(?P<bindings>[^{}]{1,10000})\}\s*from\s*"
    r"[\"'](?P<source>[^\"']+\.js(?:\?[^\"']*)?)[\"']",
)
_HTTPS_LITERAL = re.compile(r"[\"'](?P<url>https://[^\"']{1,300})[\"']")
_JS_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]{0,79}"
_PUBLIC_DNS_NAME = re.compile(
    r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AssetSource:
    url: str
    body: str


@dataclass(frozen=True)
class FirstPartyInventoryProbe:
    page: Page
    trace: dict


@dataclass(frozen=True)
class _DeclaredInventoryRequest:
    endpoint_url: str
    attribute_name: str
    asset_url: str


class _DataAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        _tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        for name, value in attrs:
            normalized_name = name.casefold()
            if normalized_name.startswith("data-") and value is not None:
                self.values.append((normalized_name, value))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def probe_first_party_job_inventory(
    fetcher: FetchClient,
    page: Page,
    assets: Sequence[AssetSource],
    provider_board_identity: ProviderBoardIdentity,
) -> FirstPartyInventoryProbe | None:
    """Verify a bounded first-party literal-GET inventory and expose its ATS board."""

    page_url = page.final_url or page.url
    request = _inventory_request(assets)
    if request is None:
        declared_request = _declared_inventory_request(page, assets)
        if declared_request is None:
            return None
        return _probe_declared_inventory(fetcher, page, declared_request)
    client_name, route, importer = request
    trace: dict = {
        "method": "first_party_dynamic_inventory",
        "status": "candidate",
        "asset_urls": [asset.url for asset in assets],
        "dependency_asset_urls": [],
        "endpoint_url": None,
        "inventory_complete": False,
        "inventory_count": None,
        "provider": None,
        "board_url": None,
    }

    client_sources = [importer]
    dependency_url = _client_dependency_url(importer, client_name, page_url)
    if dependency_url is not None and all(
        source.url != dependency_url for source in client_sources
    ):
        trace["dependency_asset_urls"].append(dependency_url)
        try:
            dependency_page = fetcher.fetch(dependency_url)
        except (FetchError, OSError, TimeoutError) as exc:
            trace.update(
                status="dependency_fetch_failed",
                fetch_error=_fetch_error_trace(exc),
            )
            return FirstPartyInventoryProbe(page=page, trace=trace)
        if not _safe_same_origin_url(
            dependency_page.final_url or dependency_page.url,
            page_url,
        ):
            trace["status"] = "dependency_redirect_rejected"
            return FirstPartyInventoryProbe(page=page, trace=trace)
        client_sources.append(
            AssetSource(
                url=dependency_url,
                body=(dependency_page.html or "")[:MAX_INVENTORY_BYTES],
            )
        )

    api_base = next(
        (
            candidate
            for source in client_sources
            if (
                candidate := _public_api_base(
                    source.body[:MAX_INVENTORY_BYTES],
                    page_url,
                )
            )
            is not None
        ),
        None,
    )
    if api_base is None:
        trace["status"] = "api_base_not_verified"
        return FirstPartyInventoryProbe(page=page, trace=trace)
    endpoint_url = _endpoint_url(api_base, route, page_url)
    if endpoint_url is None:
        trace["status"] = "endpoint_rejected"
        return FirstPartyInventoryProbe(page=page, trace=trace)
    trace["endpoint_url"] = endpoint_url

    headers = _public_request_headers(endpoint_url, page_url)
    try:
        response = fetcher.fetch(endpoint_url, headers=headers or None)
    except (FetchError, OSError, TimeoutError) as exc:
        trace.update(status="inventory_fetch_failed", fetch_error=_fetch_error_trace(exc))
        return FirstPartyInventoryProbe(page=page, trace=trace)
    if not _same_normalized_url(response.final_url or response.url, endpoint_url):
        trace["status"] = "inventory_redirect_rejected"
        return FirstPartyInventoryProbe(page=page, trace=trace)

    inventory = _parse_inventory(response.html or "", provider_board_identity)
    if inventory is None:
        trace["status"] = "invalid_inventory_payload"
        return FirstPartyInventoryProbe(page=page, trace=trace)
    urls, identity = inventory
    trace.update(
        status="verified",
        inventory_complete=True,
        inventory_count=len(urls),
        response_source=response.source,
    )
    if identity is None:
        return FirstPartyInventoryProbe(page=page, trace=trace)

    provider, board_url = identity
    trace.update(provider=provider, board_url=board_url)
    embedded = json.dumps({"verified_job_urls": urls}, ensure_ascii=True)
    enriched_page = Page(
        url=page.url,
        final_url=page.final_url,
        html=f'<script type="application/json">{embedded}</script>\n{page.html}',
        source=f"{page.source}|first_party_dynamic_inventory",
        artifacts=page.artifacts,
    )
    return FirstPartyInventoryProbe(page=enriched_page, trace=trace)


def _probe_declared_inventory(
    fetcher: FetchClient,
    page: Page,
    request: _DeclaredInventoryRequest,
) -> FirstPartyInventoryProbe:
    trace: dict = {
        "method": "first_party_declared_inventory",
        "status": "candidate",
        "asset_urls": [request.asset_url],
        "dependency_asset_urls": [],
        "endpoint_url": request.endpoint_url,
        "declared_attribute": request.attribute_name,
        "inventory_complete": False,
        "inventory_count": None,
        "provider": None,
        "board_url": None,
    }
    try:
        response = fetcher.fetch(request.endpoint_url)
    except (FetchError, OSError, TimeoutError) as exc:
        trace.update(status="inventory_fetch_failed", fetch_error=_fetch_error_trace(exc))
        return FirstPartyInventoryProbe(page=page, trace=trace)
    if not _same_exact_url(response.final_url or response.url, request.endpoint_url):
        trace["status"] = "inventory_redirect_rejected"
        return FirstPartyInventoryProbe(page=page, trace=trace)

    records = _parse_declared_inventory(response.html or "")
    if records is None:
        trace["status"] = "invalid_inventory_payload"
        return FirstPartyInventoryProbe(page=page, trace=trace)
    trace["inventory_count"] = len(records)
    if not records:
        trace["status"] = "empty_inventory_unverified"
        return FirstPartyInventoryProbe(page=page, trace=trace)

    trace.update(
        status="verified",
        inventory_complete=True,
        response_source=response.source,
    )
    embedded = json.dumps(
        {
            "first_party_declared_inventory": {
                "endpoint_url": request.endpoint_url,
                "jobs": records,
            }
        },
        ensure_ascii=True,
    )
    enriched_page = Page(
        url=page.url,
        final_url=page.final_url,
        html=f'<script type="application/json">{embedded}</script>\n{page.html}',
        source=f"{page.source}|first_party_declared_inventory",
        artifacts=page.artifacts,
    )
    return FirstPartyInventoryProbe(page=enriched_page, trace=trace)


def _declared_inventory_request(
    page: Page,
    assets: Sequence[AssetSource],
) -> _DeclaredInventoryRequest | None:
    html = page.html or ""
    if len(html.encode("utf-8")) > MAX_INVENTORY_BYTES:
        return None
    parser = _DataAttributeParser()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, TypeError):
        return None

    page_url = page.final_url or page.url
    matches: set[_DeclaredInventoryRequest] = set()
    for attribute_name, endpoint_url in parser.values:
        if not _safe_declared_endpoint(endpoint_url, page_url):
            continue
        for asset in assets:
            if not _safe_first_party_asset_url(asset.url, page_url):
                continue
            if _asset_anonymously_fetches_attribute(asset.body, attribute_name):
                matches.add(
                    _DeclaredInventoryRequest(
                        endpoint_url=endpoint_url,
                        attribute_name=attribute_name,
                        asset_url=asset.url,
                    )
                )
    endpoints = {match.endpoint_url for match in matches}
    if len(endpoints) != 1:
        return None
    return sorted(
        matches,
        key=lambda match: (match.asset_url, match.attribute_name),
    )[0]


def _asset_anonymously_fetches_attribute(body: str, attribute_name: str) -> bool:
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_INVENTORY_BYTES:
        return False
    dataset_name = _dataset_name(attribute_name)
    attribute_reads = (
        rf"(?:\.dataset\s*\.\s*{re.escape(dataset_name)}\b|"
        rf"\.dataset\s*\[\s*[\"']{re.escape(dataset_name)}[\"']\s*\]|"
        rf"\.getAttribute\(\s*[\"']{re.escape(attribute_name)}[\"']\s*\))"
    )
    assignment = re.compile(
        rf"\b(?:const|let|var)\s+(?P<variable>{_JS_IDENTIFIER})\s*=\s*"
        rf"[^;\n]{{0,1000}}?{attribute_reads}\s*;?",
    )
    for match in assignment.finditer(body):
        variable = re.escape(match.group("variable"))
        suffix = body[match.end() : match.end() + 10_000]
        fetch_match = re.search(
            rf"(?:\bfetch|\bwindow\s*\.\s*fetch)\s*\(\s*{variable}"
            rf"(?:\s*,\s*(?P<options>\{{[^{{}}]{{0,300}}\}}))?\s*\)",
            suffix,
        )
        if fetch_match is None:
            continue
        if not _safe_anonymous_fetch_options(fetch_match.group("options")):
            continue
        before_fetch = suffix[: fetch_match.start()]
        if re.search(
            rf"(?<![A-Za-z0-9_$]){variable}\s*(?:=|\+=)",
            before_fetch,
        ):
            continue
        return True
    return False


def _safe_anonymous_fetch_options(options: str | None) -> bool:
    if options is None:
        return True
    body = options.strip()[1:-1].strip()
    if not body:
        return True
    allowed = {
        "credentials": "omit",
        "method": "GET",
        "mode": "cors",
    }
    seen: set[str] = set()
    for item in body.split(","):
        match = re.fullmatch(
            r"\s*(?P<key>credentials|method|mode)\s*:\s*"
            r"[\"'](?P<value>[^\"']{1,20})[\"']\s*",
            item,
        )
        if match is None:
            return False
        key = match.group("key")
        if key in seen or match.group("value") != allowed[key]:
            return False
        seen.add(key)
    return True


def _dataset_name(attribute_name: str) -> str:
    parts = attribute_name.removeprefix("data-").split("-")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _parse_declared_inventory(body: str) -> list[dict[str, str]] | None:
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_INVENTORY_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not set(payload).issubset(
        {"jobs", "source", "total"}
    ) or "jobs" not in payload:
        return None
    records = payload["jobs"]
    if not isinstance(records, list) or len(records) > MAX_INVENTORY_ITEMS:
        return None
    if "source" in payload and (
        not isinstance(payload["source"], str)
        or not payload["source"].strip()
        or len(payload["source"]) > 100
    ):
        return None
    if "total" in payload and (
        not isinstance(payload["total"], int)
        or isinstance(payload["total"], bool)
        or payload["total"] != len(records)
    ):
        return None

    output: list[dict[str, str]] = []
    family: tuple[str, str] | None = None
    for record in records:
        if not isinstance(record, dict):
            return None
        title = _unambiguous_text(record, "title", "Title")
        url = _unambiguous_text(record, "url", "Url")
        raw_locations = [
            record[name]
            for name in ("location", "Location")
            if name in record
        ]
        if any(not isinstance(value, str) for value in raw_locations):
            return None
        locations = {value.strip() for value in raw_locations if value.strip()}
        if len(locations) > 1:
            return None
        location = next(iter(locations), None)
        if (
            title is None
            or url is None
            or len(title) > 500
            or len(url) > 2_048
            or any(isinstance(value, (dict, list)) for value in record.values())
        ):
            return None
        candidate_family = _public_opening_family(url)
        if candidate_family is None:
            return None
        if family is None:
            family = candidate_family
        elif candidate_family != family:
            return None
        sanitized = {"title": title, "url": url}
        if location is not None:
            if len(location) > 500:
                return None
            sanitized["location"] = location
        output.append(sanitized)
    return list({item["url"]: item for item in output}.values())


def _unambiguous_text(
    record: dict,
    *names: str,
    required: bool = True,
) -> str | None:
    values = [record[name].strip() for name in names if isinstance(record.get(name), str)]
    if len(values) != sum(name in record for name in names):
        return None
    distinct = set(values)
    if not distinct:
        return None if required else None
    if len(distinct) != 1 or not values[0]:
        return None
    return values[0]


def _safe_declared_endpoint(candidate_url: str, page_url: str) -> bool:
    candidate = _strict_public_https_parts(candidate_url)
    page = _strict_public_https_parts(page_url, allow_path_query=True)
    if candidate is None or page is None:
        return False
    return (candidate.scheme, candidate.hostname.casefold(), candidate.port or 443) != (
        page.scheme,
        page.hostname.casefold(),
        page.port or 443,
    )


def _safe_first_party_asset_url(candidate_url: str, page_url: str) -> bool:
    try:
        candidate = urlparse(candidate_url)
        page = urlparse(page_url)
        candidate_port = candidate.port
        page_port = page.port
    except (TypeError, ValueError):
        return False
    return bool(
        candidate.scheme == "https"
        and page.scheme == "https"
        and _is_public_host(candidate.hostname)
        and _is_public_host(page.hostname)
        and (candidate.hostname or "").casefold() == (page.hostname or "").casefold()
        and candidate_port in {None, 443}
        and page_port in {None, 443}
        and candidate.username is None
        and candidate.password is None
        and page.username is None
        and page.password is None
        and not candidate.fragment
    )


def _public_opening_family(url: str) -> tuple[str, str] | None:
    parsed = _strict_public_https_parts(url)
    if parsed is None:
        return None
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        return None
    parent_path = path.rpartition("/")[0] or "/"
    origin = f"https://{parsed.hostname.casefold()}"
    return origin, parent_path


def _strict_public_https_parts(
    url: str,
    *,
    allow_path_query: bool = False,
):
    if not isinstance(url, str) or not url or url != url.strip():
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        decoded_path = unquote(parsed.path)
    except (TypeError, ValueError):
        return None
    if not (
        parsed.scheme == "https"
        and _is_strict_public_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and "\\" not in decoded_path
        and ".." not in decoded_path.split("/")
        and (allow_path_query or (not parsed.query and not parsed.fragment))
    ):
        return None
    return parsed


def _is_strict_public_host(host: str | None) -> bool:
    normalized = (host or "").casefold().rstrip(".")
    if not normalized:
        return False
    try:
        return ipaddress.ip_address(normalized).is_global
    except ValueError:
        return bool(
            "." in normalized
            and _PUBLIC_DNS_NAME.fullmatch(normalized)
            and all(len(label) <= 63 for label in normalized.split("."))
        )


def _same_exact_url(first: str, second: str) -> bool:
    return isinstance(first, str) and first == second


def _inventory_request(
    assets: Sequence[AssetSource],
) -> tuple[str, str, AssetSource] | None:
    for asset in assets:
        body = asset.body[:MAX_INVENTORY_BYTES]
        for match in _LITERAL_GET.finditer(body):
            route = match.group("route")
            if _is_job_inventory_route(route):
                return match.group("client"), route, asset
    return None


def _is_job_inventory_route(route: str) -> bool:
    try:
        parsed = urlparse(route)
    except ValueError:
        return False
    normalized = parsed.path.casefold()
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    return bool(
        route.startswith("/api/")
        and not parsed.query
        and not parsed.fragment
        and ".." not in parsed.path.split("/")
        and "list" in tokens
        and tokens.intersection(
            {"career", "careers", "job", "jobs", "posting", "position"}
        )
    )


def _client_dependency_url(
    importer: AssetSource,
    client_name: str,
    page_url: str,
) -> str | None:
    for match in _IMPORT_BLOCK.finditer(importer.body[:MAX_INVENTORY_BYTES]):
        if not _imports_local_name(match.group("bindings"), client_name):
            continue
        try:
            candidate = normalize_url(match.group("source"), importer.url)
        except (TypeError, ValueError):
            return None
        return candidate if _safe_same_origin_url(candidate, page_url) else None
    return None


def _imports_local_name(bindings: str, local_name: str) -> bool:
    for binding in bindings.split(","):
        parts = binding.strip().split()
        if parts == [local_name] or (
            len(parts) == 3 and parts[1] == "as" and parts[2] == local_name
        ):
            return True
    return False


def _public_api_base(body: str, page_url: str) -> str | None:
    page_host = (urlparse(page_url).hostname or "").casefold()
    for match in _HTTPS_LITERAL.finditer(body[:MAX_INVENTORY_BYTES]):
        candidate = match.group("url")
        try:
            parsed = urlparse(candidate)
            port = parsed.port
        except ValueError:
            continue
        path_tokens = {part.casefold() for part in parsed.path.split("/") if part}
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == page_host
            and port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and path_tokens.intersection({"api", "api-proxy"})
        ):
            return candidate.rstrip("/")
    return None


def _endpoint_url(api_base: str, route: str, page_url: str) -> str | None:
    try:
        parsed_base = urlparse(api_base)
        if parsed_base.path.rstrip("/").casefold() == "/api" and route.startswith("/api/"):
            endpoint = normalize_url(
                parsed_base._replace(path=route, query="", fragment="").geturl()
            )
        else:
            endpoint = normalize_url(f"{api_base.rstrip('/')}/{route.lstrip('/')}")
    except (TypeError, ValueError):
        return None
    return endpoint if _safe_same_origin_url(endpoint, page_url) else None


def _public_request_headers(endpoint_url: str, page_url: str) -> dict[str, str]:
    endpoint_parts = {
        part.casefold() for part in urlparse(endpoint_url).path.split("/") if part
    }
    if "api-proxy" not in endpoint_parts:
        return {}
    host = (urlparse(page_url).hostname or "").casefold()
    return {"Authorization": f"Bearer {host}"}


def _parse_inventory(
    body: str,
    provider_board_identity: ProviderBoardIdentity,
) -> tuple[list[str], tuple[str, str] | None] | None:
    if not isinstance(body, str):
        return None
    if len(body.encode("utf-8")) > MAX_INVENTORY_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    records = _inventory_records(payload)
    if records is None or len(records) > MAX_INVENTORY_ITEMS:
        return None

    urls: list[str] = []
    identity: tuple[str, str] | None = None
    for record in records:
        if not isinstance(record, dict):
            return None
        title = record.get("title")
        raw_url = record.get("url")
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 500
            or not isinstance(raw_url, str)
            or not raw_url.strip()
            or len(raw_url) > 2_048
        ):
            return None
        try:
            url = normalize_url(raw_url)
            if not _safe_public_provider_url(url):
                return None
            candidate_identity = provider_board_identity(url)
        except (TypeError, ValueError):
            return None
        if candidate_identity is None:
            return None
        if identity is None:
            identity = candidate_identity
        elif candidate_identity != identity:
            return None
        urls.append(url)
    return list(dict.fromkeys(urls)), identity


def _inventory_records(payload: object) -> list[object] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    if any(
        isinstance(value, (dict, list))
        for key, value in payload.items()
        if key != "data"
    ):
        return None
    return payload["data"]


def _safe_same_origin_url(candidate_url: str, page_url: str) -> bool:
    try:
        candidate = urlparse(candidate_url)
        page = urlparse(page_url)
        port = candidate.port
        page_port = page.port
    except (TypeError, ValueError):
        return False
    return bool(
        candidate.scheme == "https"
        and page.scheme == "https"
        and _is_public_host(candidate.hostname)
        and _is_public_host(page.hostname)
        and candidate.hostname.casefold() == (page.hostname or "").casefold()
        and candidate.username is None
        and candidate.password is None
        and page.username is None
        and page.password is None
        and port in {None, 443}
        and page_port in {None, 443}
        and not candidate.query
        and not candidate.fragment
    )


def _safe_public_provider_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and _is_public_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _is_public_host(host: str | None) -> bool:
    normalized = (host or "").casefold().rstrip(".")
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "." in normalized
    return address.is_global


def _same_normalized_url(first: str, second: str) -> bool:
    try:
        return normalize_url(first) == normalize_url(second)
    except (TypeError, ValueError):
        return False


def _fetch_error_trace(exc: BaseException) -> dict:
    return {
        "status": getattr(exc, "status", None),
        "reason_code": getattr(exc, "reason_code", None),
        "retryable": getattr(exc, "retryable", None),
    }
