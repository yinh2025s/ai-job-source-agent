from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

from .contracts import FetchClient
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
    asset_urls.sort(key=lambda url: _provider_asset_priority(url, ""))
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
