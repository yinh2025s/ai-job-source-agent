from __future__ import annotations

import json
import re
from dataclasses import fields
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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
        company = CompanyInput(**_normalize_input_record(record))
        if company.linkedin_html_path:
            html_data = parse_linkedin_html(Path(company.linkedin_html_path))
            company.company_name = company.company_name or html_data.get("company_name", "")
            company.company_website_url = company.company_website_url or html_data.get("company_website_url", "")
            company.linkedin_company_url = company.linkedin_company_url or html_data.get("linkedin_company_url", "")
            company.external_apply_url = company.external_apply_url or html_data.get("external_apply_url")
        if not company.company_name:
            company.company_name = infer_company_name_from_url(company.company_website_url)
        if not company.company_website_url and not company.linkedin_company_url:
            raise ValueError(
                "company_website_url or linkedin_company_url is required unless linkedin_html_path contains a discoverable website URL"
            )
        inputs.append(company)
    return inputs


def _normalize_input_record(record: dict) -> dict:
    """Accept either source records or a prior results/trace output for reruns."""
    normalized = dict(record)
    normalized.setdefault("job_title", normalized.get("linkedin_job_title"))
    normalized.setdefault("job_location", normalized.get("linkedin_job_location"))
    if not normalized.get("career_root_url") and normalized.get("career_page_url"):
        normalized["career_root_url"] = normalized["career_page_url"]
    if "trace" in normalized and not normalized.get("source_trace"):
        trace = normalized["trace"]
        if isinstance(trace, dict):
            normalized["source_trace"] = trace.get("source_trace", {})

    allowed_fields = {field.name for field in fields(CompanyInput)}
    return {key: value for key, value in normalized.items() if key in allowed_fields}


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
    payload_data = _extract_public_payload_evidence(parser.script_texts)
    company_name = parser.company_name or payload_data.get("company_name", "") or _guess_company_name_from_text(html)
    company_website_url = parser.company_website_url or payload_data.get("company_website_url", "")
    linkedin_company_url = parser.linkedin_company_url or payload_data.get("linkedin_company_url", "")
    external_apply_url = parser.external_apply_url or payload_data.get("external_apply_url", "")
    data: dict[str, str] = {}
    if company_name:
        data["company_name"] = company_name
    if company_website_url:
        data["company_website_url"] = company_website_url
    if linkedin_company_url:
        data["linkedin_company_url"] = linkedin_company_url
    if external_apply_url:
        data["external_apply_url"] = external_apply_url
    return data


class _LinkedInHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.company_name = ""
        self.company_website_url = ""
        self.linkedin_company_url = ""
        self.external_apply_url = ""
        self.script_texts: list[str] = []
        self._active_href = ""
        self._active_text: list[str] = []
        self._in_script = False
        self._script_text: list[str] = []

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
        elif tag.lower() == "script":
            self._in_script = True
            self._script_text = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_text.append(data)
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._in_script = False
            script = "".join(self._script_text).strip()
            if script:
                self.script_texts.append(script)
            self._script_text = []
            return
        if tag.lower() != "a" or not self._active_href:
            return
        text = " ".join("".join(self._active_text).split())
        href = self._active_href
        linkedin_company_url = _safe_linkedin_company_url(href)
        if not self.company_name and linkedin_company_url and text:
            self.company_name = text
        if not self.linkedin_company_url and linkedin_company_url:
            self.linkedin_company_url = linkedin_company_url
        if not self.company_website_url and _is_website_label(text):
            self.company_website_url = _safe_company_website_url(href)
        if not self.external_apply_url and _is_apply_label(text):
            self.external_apply_url = _safe_external_apply_url(href)
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


def _extract_public_payload_evidence(scripts: list[str]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for script in scripts:
        candidates = [script, unescape(script)]
        for candidate in candidates:
            payload = _load_json_payload(candidate)
            if payload is not None:
                _collect_payload_evidence(payload, evidence)
        if all(evidence.get(key) for key in ("company_name", "company_website_url", "linkedin_company_url")):
            break
    return evidence


def _load_json_payload(text: str):
    text = text.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _collect_payload_evidence(value, evidence: dict[str, str]) -> None:
    if isinstance(value, dict):
        normalized_items = {
            key.rsplit(".", 1)[-1].lower(): item
            for key, item in value.items()
        }
        has_company_context = any(
            key in normalized_items
            for key in ("companyname", "company_name", "companypageurl", "linkedincompanyurl")
        )
        for key, item in value.items():
            normalized_key = key.rsplit(".", 1)[-1].lower()
            if isinstance(item, str):
                if normalized_key in {"companyname", "company_name"} and not evidence.get("company_name"):
                    evidence["company_name"] = " ".join(item.split())
                elif normalized_key in {"companypageurl", "linkedincompanyurl"} and not evidence.get("linkedin_company_url"):
                    safe_url = _safe_linkedin_company_url(item)
                    if safe_url:
                        evidence["linkedin_company_url"] = safe_url
                elif (
                    normalized_key in {"websiteurl", "companywebsiteurl"}
                    and has_company_context
                    and not evidence.get("company_website_url")
                ):
                    safe_url = _safe_company_website_url(item)
                    if safe_url:
                        evidence["company_website_url"] = safe_url
                elif normalized_key in {"externalapplyurl", "external_apply_url"} and not evidence.get(
                    "external_apply_url"
                ):
                    safe_url = _safe_external_apply_url(item)
                    if safe_url:
                        evidence["external_apply_url"] = safe_url
            _collect_payload_evidence(item, evidence)
    elif isinstance(value, list):
        for item in value:
            _collect_payload_evidence(item, evidence)


def _is_website_label(text: str) -> bool:
    return " ".join(text.lower().split()) in {"website", "company website", "visit website", "company site"}


def _is_apply_label(text: str) -> bool:
    return " ".join(text.casefold().split()) in {"apply", "apply now", "apply for this job"}


def _safe_external_apply_url(url: str) -> str:
    normalized = _safe_http_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").casefold()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return ""
    if host == "licdn.com" or host.endswith(".licdn.com") or host == "lnkd.in":
        return ""
    return normalized


def _safe_linkedin_company_url(url: str) -> str:
    normalized = _safe_http_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    if not re.match(r"^/company/[^/?#]+/?$", parsed.path):
        return ""
    return urlunparse((parsed.scheme, "www.linkedin.com", parsed.path, "", "", ""))


def _safe_company_website_url(url: str) -> str:
    normalized = _safe_http_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    blocked_hosts = (
        "linkedin.com",
        "licdn.com",
        "lnkd.in",
        "myworkdayjobs.com",
    )
    exact_job_hosts = {
        "apply.workable.com",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "jobs.ashbyhq.com",
        "jobs.lever.co",
        "jobs.smartrecruiters.com",
        "ats.rippling.com",
    }
    tenant_job_hosts = (
        ".bamboohr.com",
        ".icims.com",
    )
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in blocked_hosts):
        return ""
    if host in exact_job_hosts:
        return ""
    if any(host.endswith(suffix) and not host.startswith("www.") for suffix in tenant_job_hosts):
        return ""
    return normalized


def _safe_http_url(url: str) -> str:
    try:
        normalized = normalize_url(unescape(url).strip())
        parsed = urlparse(normalized)
        _ = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.port not in {None, 80, 443}:
        return ""
    return normalized
