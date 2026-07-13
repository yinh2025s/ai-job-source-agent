from __future__ import annotations

import re
from urllib.parse import urlparse

from .contracts import FetchClient
from .web import FetchError, Page, domain_of, normalize_url


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
        if not _same_site_host(
            urlparse(asset_url).hostname or "",
            urlparse(page_url).hostname or "",
        ):
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


def _same_site_host(first: str, second: str) -> bool:
    if first == second or first.endswith("." + second) or second.endswith("." + first):
        return True
    return _registrable_site(first) == _registrable_site(second)


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
