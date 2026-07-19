from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode, urlparse

from .contracts import FetchClient
from .first_party_inventory import (
    AssetSource,
    ProviderBoardIdentity,
    probe_first_party_job_inventory,
)
from .generic_opening_inventory import parse_dynamic_inventory_payload
from .web import FetchError, Page, RawLink, domain_of, normalize_url


_RAW_WEB_URL = re.compile(r"https?:(?:\\?/){2}[^\"'<>\s]+", flags=re.I)
_CAREER_LABEL = (
    r"(?:careers?|jobs?|job\s+opportunities|join\s+(?:us|our\s+team)|"
    r"work\s+with\s+us|opportunities|open\s+positions)"
)
_CAREER_LABEL_RE = re.compile(rf"^{_CAREER_LABEL}$", flags=re.I)
_ROOT_ROUTE = r"/[A-Za-z0-9][A-Za-z0-9_./-]{0,198}"
_ROUTE_THEN_LABEL = re.compile(
    rf"(?:href|path|value)\s*:\s*[\"'](?P<route>{_ROOT_ROUTE})[\"']"
    rf"[^{{}}]{{0,180}}?(?:children|label)\s*:\s*[\"'](?P<label>{_CAREER_LABEL})[\"']",
    flags=re.I,
)
_LABEL_THEN_ROUTE = re.compile(
    rf"(?:children|label)\s*:\s*[\"'](?P<label>{_CAREER_LABEL})[\"']"
    rf"[^{{}}]{{0,180}}?(?:href|path|value)\s*:\s*[\"'](?P<route>{_ROOT_ROUTE})[\"']",
    flags=re.I,
)
_ANGULAR_ROUTE_LABEL = re.compile(
    rf'''["']routerLink["']\s*,\s*["'](?P<route>{_ROOT_ROUTE})["']'''
    rf'''[^\]\[]{{0,220}}?["']aria-label["']\s*,\s*["']'''
    rf'''(?P<label>[^"']*\b(?:careers?|jobs?|open\s+positions)\b[^"']*)["']''',
    flags=re.I,
)
_MAX_DYNAMIC_ENDPOINTS = 2
_MAX_DYNAMIC_PAGE_SIZE = 1_000
_MAX_NAMED_JOB_DESTINATIONS = 32
_NAMED_JOB_DESTINATION = re.compile(
    r"\{[^{}]{0,500}?\bname\s*:\s*(?:\"(?P<label_d>[^\"]{1,120})\"|"
    r"'(?P<label_s>[^']{1,120})')"
    r"[^{}]{0,500}?\burl\s*:\s*(?:\"(?P<url_d>https://[^\"]{1,2000})\"|"
    r"'(?P<url_s>https://[^']{1,2000})')"
    r"[^{}]{0,500}?\}",
    flags=re.I,
)


@dataclass(frozen=True)
class _DynamicInventoryDeclaration:
    endpoint_url: str
    detail_url_template: str | None
    complete_hint: bool
    asset_url: str


def discover_first_party_career_navigation(
    fetcher: FetchClient,
    page: Page,
    *,
    max_assets: int = 3,
) -> tuple[list[RawLink], dict]:
    """Extract labeled same-origin career routes from bounded public JS assets."""

    page_url = page.final_url or page.url
    asset_urls = _first_party_script_assets(page, max_assets=max_assets)
    fetched: list[str] = []
    candidates: list[RawLink] = []
    seen: set[str] = set()
    for asset_url in asset_urls:
        try:
            asset_page = fetcher.fetch(asset_url)
        except (FetchError, OSError, TimeoutError):
            continue
        if not _is_safe_same_site_asset(asset_page.final_url or asset_page.url, page_url):
            continue
        fetched.append(asset_url)
        bundle = _strip_javascript_comments((asset_page.html or "")[:5_000_000])
        for pattern in (_ROUTE_THEN_LABEL, _LABEL_THEN_ROUTE):
            for match in pattern.finditer(bundle):
                route = match.group("route")
                if not _career_route(route):
                    continue
                try:
                    candidate_url = normalize_url(route, page_url)
                except (TypeError, ValueError):
                    continue
                if not _is_safe_same_site_navigation(candidate_url, page_url):
                    continue
                _append_navigation_candidate(
                    candidates,
                    seen,
                    candidate_url,
                    match.group("label"),
                    page_url,
                )
        for match in _ANGULAR_ROUTE_LABEL.finditer(bundle):
            route = match.group("route")
            if not _career_route(route):
                continue
            try:
                candidate_url = normalize_url(route, page_url)
            except (TypeError, ValueError):
                continue
            if not _is_safe_same_site_navigation(candidate_url, page_url):
                continue
            _append_navigation_candidate(
                candidates,
                seen,
                candidate_url,
                match.group("label"),
                page_url,
            )
        parser = _CareerAnchorParser()
        try:
            parser.feed(bundle)
            parser.close()
        except (AssertionError, ValueError):
            pass
        for href, label in parser.links:
            try:
                raw_target = urlparse(href)
                if raw_target.query or raw_target.fragment:
                    continue
                candidate_url = normalize_url(href, page_url)
            except (TypeError, ValueError):
                continue
            if not _is_safe_same_site_navigation(candidate_url, page_url):
                continue
            _append_navigation_candidate(
                candidates,
                seen,
                candidate_url,
                label,
                page_url,
            )

    return candidates, {
        "method": "first_party_bundle_navigation",
        "asset_urls": fetched,
        "candidate_urls": [candidate.url for candidate in candidates],
    }


def probe_first_party_cms_payload(
    fetcher: FetchClient,
    page: Page,
) -> tuple[Page, dict | None]:
    html = page.html or ""
    module_match = re.search(
        r'<script\b(?=[^>]*\btype=["\']module["\'])[^>]*\bsrc=["\']([^"\']+)["\']',
        html[:200000],
        flags=re.I,
    )
    if not module_match:
        return page, None

    page_url = page.final_url or page.url
    try:
        asset_url = normalize_url(module_match.group(1), page_url)
        if not _is_safe_same_site_asset(asset_url, page_url):
            return page, None
        asset_page = fetcher.fetch(asset_url)
    except (FetchError, TypeError, ValueError):
        return page, None

    bundle = asset_page.html or ""
    endpoint = "/.rest/delivery/marketing-pages/v1"
    if endpoint not in bundle:
        return page, None
    public_bases = re.findall(r'https://[^"\']*magnolia-public[^"\']*', bundle)
    cms_base = next(
        (
            value
            for value in public_bases
            if "sandbox" not in value.casefold() and "testing" not in value.casefold()
        ),
        None,
    )
    app_base_match = re.search(
        r'sessionStorage\.getItem\(["\']appBase["\']\)\s*\|\|\s*["\'](/[^"\']+)["\']',
        bundle,
    )
    if not cms_base or not app_base_match:
        return page, None

    parsed_cms = urlparse(cms_base)
    if parsed_cms.scheme != "https" or not parsed_cms.hostname:
        return page, None
    page_brand = _brand_label(urlparse(page_url).netloc).replace("-", "")
    normalized_cms_host = re.sub(r"[^a-z0-9]", "", parsed_cms.hostname.casefold())
    if not page_brand or page_brand not in normalized_cms_host:
        return page, None
    try:
        if parsed_cms.port not in {None, 443}:
            return page, None
    except ValueError:
        return page, None

    page_path = urlparse(page_url).path or "/"
    payload_url = normalize_url(
        f"{endpoint}{app_base_match.group(1).rstrip('/')}{page_path}",
        cms_base,
    )
    try:
        payload_page = fetcher.fetch(payload_url)
    except FetchError:
        return page, None
    if domain_of(payload_page.final_url or payload_page.url) != domain_of(cms_base):
        return page, None

    return (
        Page(
            url=page.url,
            final_url=page.final_url,
            html=f"{page.html}\n{payload_page.html}",
            source=f"{page.source}|magnolia_delivery",
            artifacts=page.artifacts,
        ),
        {
            "method": "magnolia_delivery",
            "asset_url": asset_url,
            "payload_url": payload_url,
            "payload_source": payload_page.source,
        },
    )


def probe_first_party_provider_assets(
    fetcher: FetchClient,
    page: Page,
    recognizes_provider_url: Callable[[str], bool],
    provider_board_identity: ProviderBoardIdentity | None = None,
    *,
    max_assets: int = 3,
) -> tuple[Page, dict | None]:
    """Recover ATS URLs embedded in bounded, same-site JavaScript chunks."""
    html = page.html or ""
    page_url = page.final_url or page.url
    asset_hrefs = re.findall(
        r'<(?:script|link)\b[^>]*(?:src|href)=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
        html[:300000],
        flags=re.I,
    )
    route_token = next(
        (part.casefold() for part in reversed(urlparse(page_url).path.split("/")) if part),
        "",
    )

    asset_urls: list[str] = []
    for href in asset_hrefs:
        try:
            asset_url = normalize_url(href, page_url)
        except (TypeError, ValueError):
            continue
        if not _is_safe_same_site_asset(asset_url, page_url):
            continue
        if asset_url not in asset_urls:
            asset_urls.append(asset_url)
    declaration_order = {url: index for index, url in enumerate(asset_urls)}

    def provider_asset_order(url: str) -> tuple[int, int, str]:
        priority, filename = _provider_asset_priority(url, route_token)
        return (
            priority,
            -declaration_order[url] if priority == 3 else 0,
            filename,
        )

    asset_urls.sort(key=provider_asset_order)

    fetched: list[str] = []
    bundles: list[str] = []
    asset_sources: list[AssetSource] = []
    provider_urls: list[str] = []
    job_destinations: list[dict[str, str]] = []
    for asset_url in asset_urls[:max_assets]:
        try:
            asset_page = fetcher.fetch(asset_url)
        except (FetchError, OSError, TimeoutError):
            continue
        if not _is_safe_same_site_asset(asset_page.final_url or asset_page.url, page_url):
            continue
        bundle = asset_page.html or ""
        fetched.append(asset_url)
        bundles.append(bundle)
        asset_sources.append(AssetSource(url=asset_url, body=bundle))
        provider_urls.extend(_recognized_provider_urls(bundle, recognizes_provider_url))
        job_destinations.extend(_named_job_destinations(bundle, asset_url))
        if provider_urls:
            break

    if not provider_urls:
        if provider_board_identity is None:
            return page, None
        inventory_probe = probe_first_party_job_inventory(
            fetcher,
            page,
            asset_sources,
            provider_board_identity,
        )
        if inventory_probe is not None and inventory_probe.trace.get("status") == "verified":
            return inventory_probe.page, inventory_probe.trace
        dynamic_probe = _probe_public_dynamic_inventory(
            fetcher,
            page,
            asset_sources,
        )
        if dynamic_probe is not None:
            return dynamic_probe
        if inventory_probe is not None:
            return inventory_probe.page, inventory_probe.trace
        return page, None
    return (
        Page(
            url=page.url,
            final_url=page.final_url,
            html="\n".join((html, *bundles)),
            source=f"{page.source}|first_party_provider_asset",
            artifacts=page.artifacts,
        ),
        {
            "method": "first_party_provider_asset",
            "asset_urls": fetched,
            "provider_urls": list(dict.fromkeys(provider_urls)),
            "job_destinations": _dedupe_named_job_destinations(job_destinations),
        },
    )


def _named_job_destinations(bundle: str, asset_url: str) -> list[dict[str, str]]:
    destinations: list[dict[str, str]] = []
    for match in _NAMED_JOB_DESTINATION.finditer(bundle[:5_000_000]):
        label = " ".join((match.group("label_d") or match.group("label_s")).split())
        raw_url = (match.group("url_d") or match.group("url_s")).replace(r"\/", "/")
        try:
            url = normalize_url(raw_url)
            parsed = urlparse(url)
            port = parsed.port
        except (TypeError, ValueError):
            continue
        if (
            parsed.scheme != "https"
            or not _is_public_host(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            continue
        destinations.append(
            {"label": label, "url": url, "asset_url": asset_url}
        )
        if len(destinations) >= _MAX_NAMED_JOB_DESTINATIONS:
            break
    # A single object can be unrelated application configuration. A coherent
    # list of destinations is the evidence that this is a job-destination map.
    return destinations if len(destinations) >= 2 else []


def _dedupe_named_job_destinations(
    destinations: list[dict[str, str]],
) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for destination in destinations:
        identity = (destination["label"].casefold(), destination["url"])
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(destination)
    return deduped[:_MAX_NAMED_JOB_DESTINATIONS]


def _probe_public_dynamic_inventory(
    fetcher: FetchClient,
    page: Page,
    assets: list[AssetSource],
) -> tuple[Page, dict] | None:
    declarations: list[_DynamicInventoryDeclaration] = []
    for asset in assets:
        declarations.extend(_dynamic_inventory_declarations(page, asset))
    declarations = list(
        {item.endpoint_url: item for item in declarations}.values()
    )[:_MAX_DYNAMIC_ENDPOINTS]
    if not declarations:
        return None

    attempts: list[dict] = []
    for declaration in declarations:
        try:
            response = fetcher.fetch(declaration.endpoint_url)
        except (FetchError, OSError, TimeoutError) as exc:
            attempts.append(
                {
                    "endpoint_url": declaration.endpoint_url,
                    "status": "fetch_failed",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        response_url = response.final_url or response.url
        try:
            response_matches = normalize_url(response_url) == normalize_url(
                declaration.endpoint_url
            )
        except (TypeError, ValueError):
            response_matches = False
        if not response_matches:
            attempts.append(
                {
                    "endpoint_url": declaration.endpoint_url,
                    "status": "redirect_rejected",
                }
            )
            continue
        parsed = parse_dynamic_inventory_payload(
            response.html or "",
            endpoint_url=declaration.endpoint_url,
            detail_url_template=declaration.detail_url_template,
            complete_hint=declaration.complete_hint,
        )
        if not parsed.inventory_complete or not parsed.candidates:
            attempts.append(
                {
                    "endpoint_url": declaration.endpoint_url,
                    "status": "incomplete_or_invalid_payload",
                    "candidate_count": len(parsed.candidates),
                    "reported_total": parsed.total,
                }
            )
            continue
        jobs = []
        for candidate in parsed.candidates:
            job = {"title": candidate.title, "url": candidate.url}
            if candidate.location:
                job["location"] = candidate.location
            jobs.append(job)
        envelope = json.dumps(
            {
                "endpoint_url": declaration.endpoint_url,
                "inventory_complete": True,
                "jobs": jobs,
                "total": len(jobs),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        enriched = Page(
            url=page.url,
            final_url=page.final_url,
            html=(
                '<script type="application/json" data-dynamic-job-inventory>'
                f"{envelope}</script>\n{page.html}"
            ),
            source=f"{page.source}|first_party_dynamic_inventory",
            artifacts=page.artifacts,
        )
        return enriched, {
            "method": "first_party_declared_inventory",
            "transport": "public_same_origin_get",
            "status": "verified",
            "asset_urls": [declaration.asset_url],
            "endpoint_url": declaration.endpoint_url,
            "inventory_complete": True,
            "inventory_count": len(jobs),
            "response_source": response.source,
            "attempts": attempts,
        }
    return page, {
        "method": "first_party_declared_inventory",
        "transport": "public_same_origin_get",
        "status": "unverified",
        "asset_urls": [item.asset_url for item in declarations],
        "endpoint_url": declarations[0].endpoint_url,
        "inventory_complete": False,
        "inventory_count": None,
        "attempts": attempts,
    }


def _dynamic_inventory_declarations(
    page: Page,
    asset: AssetSource,
) -> list[_DynamicInventoryDeclaration]:
    raw_body = asset.body[:5_000_000]
    body = _strip_javascript_comments(raw_body)
    # Regex literals can resemble line comments to the lightweight stripper.
    # Fall back only for single-line minified bundles that it mostly erased.
    if raw_body.count("\n") <= 1 and len(body) * 2 < len(raw_body):
        body = raw_body
    page_url = page.final_url or page.url
    declarations: list[_DynamicInventoryDeclaration] = []

    api_bases = re.findall(r'["\'](https://[^"\']{1,300}/api)/?["\']', body)
    list_routes = re.findall(
        r'\.get\(\s*["\'](?P<route>/[^"\']{1,180}[?&][^"\']{0,120}=)'
        r'["\']\s*\+\s*[A-Za-z_$][A-Za-z0-9_$]{0,79}\s*\)',
        body,
    )
    propagated_values = set()
    for match in re.finditer(
        r'\bvar\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]{0,79})\s*=\s*'
        r'["\'](?P<value>[A-Za-z0-9_-]{1,20})["\'];(?P<suffix>[\s\S]{0,500})',
        body,
    ):
        if re.search(
            rf'\.getAll\(\s*{re.escape(match.group("name"))}\s*\)',
            match.group("suffix"),
        ):
            propagated_values.add(match.group("value"))
    full_list_values = propagated_values or set(
        re.findall(r'\.getAll\(\s*["\']([A-Za-z0-9_-]{1,20})["\']\s*\)', body)
    )
    if len(set(api_bases)) == 1 and len(set(list_routes)) == 1 and len(full_list_values) == 1:
        route = list_routes[0] + next(iter(full_list_values))
        endpoint = _safe_dynamic_endpoint(
            f"{api_bases[0].rstrip('/')}/{route.lstrip('/')}", page_url
        )
        detail_template = _declared_id_detail_template(body, page_url)
        if endpoint is not None and detail_template is not None:
            declarations.append(
                _DynamicInventoryDeclaration(
                    endpoint,
                    detail_template,
                    True,
                    asset.url,
                )
            )

    jtable_endpoint = re.search(
        r'\burl\s*:\s*["\'](?P<path>/[^"\']{1,180}(?:search|jobs?)'
        r'[^"\']{0,100}\?)["\']\s*\+\s*[^;]{0,200}\.toString\(\)',
        body,
        flags=re.I,
    )
    jtable_evidence = all(
        marker in body
        for marker in ("jtStartIndex", "jtPageSize", "dataType: \"json\"")
    ) and bool(re.search(r'\.jtable\(\s*\{[\s\S]{0,8000}?listAction\s*:', body))
    if jtable_endpoint is not None and jtable_evidence:
        query = urlencode(
            {"jtStartIndex": 0, "jtPageSize": _MAX_DYNAMIC_PAGE_SIZE}
        )
        endpoint = _safe_dynamic_endpoint(
            f"{jtable_endpoint.group('path')}{query}", page_url
        )
        detail_template = _declared_slug_id_detail_template(body, page_url)
        if endpoint is not None and detail_template is not None:
            declarations.append(
                _DynamicInventoryDeclaration(
                    endpoint,
                    detail_template,
                    False,
                    asset.url,
                )
            )
    return declarations


def _declared_id_detail_template(body: str, page_url: str) -> str | None:
    routes = set(
        re.findall(
            r'\bpath\s*:\s*["\'](/?[^"\']{0,120}job/:id)["\']',
            body,
            flags=re.I,
        )
    )
    if not routes:
        literal_bases = set(
            re.findall(
                r'["\']((?:https://[^"\']{1,240})?/job/)["\']',
                body,
                flags=re.I,
            )
        )
        safe_templates = {
            template
            for base in literal_bases
            if (template := _safe_detail_template(f"{base}{{id}}", page_url))
            is not None
        }
        return safe_templates.pop() if len(safe_templates) == 1 else None
    shortest_length = min(len(route) for route in routes)
    shortest = {route for route in routes if len(route) == shortest_length}
    if len(shortest) != 1:
        return None
    route = shortest.pop()
    if not route.startswith("/"):
        route = "/" + route
    return _safe_detail_template(route.replace(":id", "{id}"), page_url)


def _declared_slug_id_detail_template(body: str, page_url: str) -> str | None:
    matches = re.findall(
        r'`(?P<prefix>/[^`$]{1,160})\$\{(?P<slug>[A-Za-z_$][A-Za-z0-9_$]*)\}'
        r'/\$\{(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)\}`',
        body,
    )
    templates = {
        f"{prefix}{{slug}}/{{id}}"
        for prefix, _slug, _identifier in matches
        if "job" in prefix.casefold()
    }
    if len(templates) != 1:
        return None
    return _safe_detail_template(templates.pop(), page_url)


def _safe_dynamic_endpoint(value: str, page_url: str) -> str | None:
    try:
        endpoint = normalize_url(value, page_url)
        parsed = urlparse(endpoint)
    except (TypeError, ValueError):
        return None
    if (
        not _is_safe_same_site_asset(endpoint, page_url)
        or (parsed.hostname or "").casefold()
        != (urlparse(page_url).hostname or "").casefold()
        or parsed.fragment
    ):
        return None
    return endpoint


def _safe_detail_template(value: str, page_url: str) -> str | None:
    try:
        template = normalize_url(value, page_url)
        parsed = urlparse(template)
    except (TypeError, ValueError):
        return None
    if (
        not _is_safe_same_site_asset(template, page_url)
        or (parsed.hostname or "").casefold()
        != (urlparse(page_url).hostname or "").casefold()
        or parsed.query
        or parsed.fragment
        or set(re.findall(r"\{[^{}]+\}", template))
        - {"{id}", "{slug}"}
    ):
        return None
    return template


def _first_party_script_assets(page: Page, *, max_assets: int) -> list[str]:
    html = page.html or ""
    page_url = page.final_url or page.url
    asset_hrefs = re.findall(
        r'<(?:script|link)\b[^>]*(?:src|href)=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
        html[:300000],
        flags=re.I,
    )
    asset_urls: list[str] = []
    for href in asset_hrefs:
        try:
            asset_url = normalize_url(href, page_url)
        except (TypeError, ValueError):
            continue
        if _is_safe_same_site_asset(asset_url, page_url) and asset_url not in asset_urls:
            asset_urls.append(asset_url)
    asset_urls.sort(key=_navigation_asset_priority)
    return asset_urls[:max_assets]


def _career_route(route: str) -> bool:
    normalized = route.casefold().rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    return bool(
        parts
        and ".." not in parts
        and any(part in {"career", "careers", "job", "jobs", "opportunities"} for part in parts)
    )


def _append_navigation_candidate(
    candidates: list[RawLink],
    seen: set[str],
    candidate_url: str,
    label: str,
    page_url: str,
) -> None:
    if candidate_url in seen:
        return
    seen.add(candidate_url)
    candidates.append(
        RawLink(
            url=candidate_url,
            text=" ".join(label.split()),
            source_url=page_url,
            origin="first_party_bundle_navigation",
        )
    )


def _is_safe_same_site_navigation(candidate_url: str, page_url: str) -> bool:
    try:
        candidate = urlparse(candidate_url)
        page = urlparse(page_url)
        candidate_port = candidate.port
    except (TypeError, ValueError):
        return False
    return bool(
        candidate.scheme.casefold() == "https"
        and _is_public_host(candidate.hostname)
        and _same_site_host(candidate.hostname or "", page.hostname or "")
        and candidate.username is None
        and candidate.password is None
        and candidate_port in {None, 443}
        and not candidate.query
        and not candidate.fragment
        and not _has_resource_extension(candidate.path)
    )


def _has_resource_extension(path: str) -> bool:
    try:
        final_segment = unquote(path).rstrip("/").rsplit("/", 1)[-1]
    except (TypeError, ValueError):
        return True
    return bool(re.search(r"\.[A-Za-z0-9]{1,12}$", final_segment))


def _is_public_host(host: str | None) -> bool:
    normalized = (host or "").casefold().rstrip(".")
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "." in normalized
    return address.is_global


def _strip_javascript_comments(source: str) -> str:
    source = re.sub(
        r"<!--[\s\S]*?-->",
        lambda match: "\n" * match.group(0).count("\n"),
        source,
    )
    output: list[str] = []
    index = 0
    quote = ""
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            output.append(character)
            if character == "\\" and following:
                output.append(following)
                index += 2
                continue
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in {'"', "'", "`"}:
            quote = character
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            newline = source.find("\n", index + 2)
            if newline < 0:
                break
            output.append("\n")
            index = newline + 1
            continue
        if character == "/" and following == "*":
            end = source.find("*/", index + 2)
            if end < 0:
                break
            output.append("\n" * source.count("\n", index, end + 2))
            index = end + 2
            continue
        output.append(character)
        index += 1
    return "".join(output)


class _CareerAnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._href is not None:
            return
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value for key, value in attrs}
        href = attributes.get("href")
        inactive = (
            "disabled" in attributes
            or "inert" in attributes
            or (attributes.get("aria-disabled") or "").casefold() == "true"
        )
        if href and not inactive:
            self._href = href
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if self._href is None or tag.casefold() != "a":
            return
        label = " ".join("".join(self._text).split())
        if _CAREER_LABEL_RE.fullmatch(label):
            self.links.append((self._href, label))
        self._href = None
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


def _provider_asset_priority(url: str, route_token: str) -> tuple[int, str]:
    filename = urlparse(url).path.rsplit("/", 1)[-1].casefold()
    if route_token and route_token in filename:
        return 0, filename
    if "job" in filename and any(
        token in filename for token in ("search", "result", "listing", "inventory")
    ):
        return 0, filename
    if filename.startswith("page-"):
        return 1, filename
    if filename.startswith("main.") or filename.startswith("main-"):
        return 1, filename
    if filename.startswith("index-"):
        return 2, filename
    if filename.startswith(("jquery", "bootstrap", "lity", "polyfills", "vendor")):
        return 4, filename
    return 3, filename


def _navigation_asset_priority(url: str) -> tuple[int, str]:
    """Prefer route-bearing app chunks over framework and library bundles."""

    path = urlparse(url).path.casefold()
    filename = path.rsplit("/", 1)[-1]
    if "/pages/" in path or filename.startswith(("page-", "index-")):
        return 0, filename
    if filename.startswith("_app-") or re.fullmatch(
        r"\d+(?:[-.][a-z0-9]+)*\.js", filename
    ):
        return 1, filename
    if filename.startswith("main.") or filename.startswith("main-"):
        return 2, filename
    if filename.startswith(("jquery", "bootstrap", "lity", "polyfills", "vendor")):
        return 4, filename
    return 3, filename


def _recognized_provider_urls(
    bundle: str,
    recognizes_provider_url: Callable[[str], bool],
) -> list[str]:
    urls: list[str] = []
    for match in _RAW_WEB_URL.finditer(bundle[:5_000_000]):
        raw_url = match.group(0).replace(r"\/", "/").rstrip("),.;]")
        try:
            normalized = normalize_url(raw_url)
        except (TypeError, ValueError):
            continue
        if recognizes_provider_url(normalized):
            urls.append(normalized)
    return urls


def _same_site_host(first: str, second: str) -> bool:
    if first == second or first.endswith("." + second) or second.endswith("." + first):
        return True
    return _registrable_site(first) == _registrable_site(second)


def _is_safe_same_site_asset(asset_url: str, page_url: str) -> bool:
    try:
        asset = urlparse(asset_url)
        page = urlparse(page_url)
        asset_port = asset.port
    except (TypeError, ValueError):
        return False
    if (
        asset.scheme.casefold() != "https"
        or not _is_public_host(asset.hostname)
        or asset.username is not None
        or asset.password is not None
        or asset_port not in {None, 443}
    ):
        return False
    return _same_site_host(asset.hostname or "", page.hostname or "")


def _registrable_site(host: str) -> str:
    parts = host.casefold().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    two_level_suffixes = {"co.uk", "com.au", "com.br", "com.sg", "co.jp", "co.nz"}
    suffix = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix in two_level_suffixes else suffix


def _brand_label(host: str) -> str:
    label = host.casefold().split(":", 1)[0].removeprefix("www.").split(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "-", label).strip("-")
