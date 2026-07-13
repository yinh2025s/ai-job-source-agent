from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse

from .contracts import FetchClient
from .web import FetchError, Page, domain_of, normalize_url


_RAW_WEB_URL = re.compile(r"https?:(?:\\?/){2}[^\"'<>\s]+", flags=re.I)


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
    asset_urls.sort(key=lambda url: _provider_asset_priority(url, route_token))

    fetched: list[str] = []
    bundles: list[str] = []
    provider_urls: list[str] = []
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
        provider_urls.extend(_recognized_provider_urls(bundle, recognizes_provider_url))
        if provider_urls:
            break

    if not provider_urls:
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
        },
    )


def _provider_asset_priority(url: str, route_token: str) -> tuple[int, str]:
    filename = urlparse(url).path.rsplit("/", 1)[-1].casefold()
    if route_token and route_token in filename:
        return 0, filename
    if filename.startswith("page-"):
        return 1, filename
    if filename.startswith("index-"):
        return 2, filename
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
