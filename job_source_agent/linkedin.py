from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .models import CompanyInput
from .web import normalize_url


def load_company_inputs(path: str | Path) -> list[CompanyInput]:
    """Load the mock LinkedIn extractor output.

    Direct LinkedIn scraping is intentionally isolated behind this input format.
    In production, this function can be replaced by a third-party LinkedIn
    crawler/API adapter that emits the same records.
    """
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    inputs: list[CompanyInput] = []
    for record in records:
        company = CompanyInput(**record)
        if company.linkedin_html_path:
            html_data = parse_linkedin_html(Path(company.linkedin_html_path))
            company.company_name = company.company_name or html_data.get("company_name", "")
            company.company_website_url = company.company_website_url or html_data.get("company_website_url", "")
        if not company.company_name:
            company.company_name = infer_company_name_from_url(company.company_website_url)
        if not company.company_website_url:
            raise ValueError(
                "company_website_url is required unless linkedin_html_path contains a discoverable website URL"
            )
        inputs.append(company)
    return inputs


def infer_company_name_from_url(url: str) -> str:
    if not url:
        return "Unknown Company"
    host = urlparse(normalize_url(url)).netloc.removeprefix("www.")
    label = host.split(".")[0]
    return " ".join(part.capitalize() for part in re.split(r"[-_]", label) if part)


def parse_linkedin_html(path: Path) -> dict[str, str]:
    html = path.read_text(encoding="utf-8", errors="replace")
    parser = _LinkedInHTMLParser()
    parser.feed(html)
    company_name = parser.company_name or _guess_company_name_from_text(html)
    company_website_url = parser.company_website_url or _guess_company_website_from_text(html)
    data: dict[str, str] = {}
    if company_name:
        data["company_name"] = company_name
    if company_website_url:
        data["company_website_url"] = company_website_url
    return data


class _LinkedInHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.company_name = ""
        self.company_website_url = ""
        self._active_href = ""
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            name = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            content = attrs_dict.get("content", "")
            if name in {"og:title", "twitter:title"} and not self.company_name:
                self.company_name = _clean_linkedin_title(content)
        if tag.lower() == "a":
            href = attrs_dict.get("href", "")
            if href:
                self._active_href = href
                self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        text = " ".join("".join(self._active_text).split())
        href = self._active_href
        if not self.company_name and "/company/" in href and text:
            self.company_name = text
        if not self.company_website_url and "linkedin.com" not in href and href.startswith("http"):
            self.company_website_url = normalize_url(href)
        self._active_href = ""
        self._active_text = []


def _clean_linkedin_title(title: str) -> str:
    # Common public LinkedIn job title shape:
    # "Company hiring Role in Location | LinkedIn"
    title = title.split("|")[0].strip()
    match = re.match(r"(.+?)\s+hiring\s+.+", title, flags=re.I)
    if match:
        return match.group(1).strip()
    return ""


def _guess_company_name_from_text(html: str) -> str:
    match = re.search(r"([A-Z][A-Za-z0-9&., -]{2,80})\s+hiring\s+", html)
    return match.group(1).strip() if match else ""


def _guess_company_website_from_text(html: str) -> str:
    for url in re.findall(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", html):
        if "linkedin.com" not in url and not any(
            ignored in url for ignored in ("licdn.com", "microsoft.com", "google.com")
        ):
            return normalize_url(url)
    return ""
