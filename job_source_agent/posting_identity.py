from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from .contracts import FetchClient
from .web import FetchError


_INTERMEDIARY_NAME_MARKERS = {
    "capital",
    "consulting",
    "partners",
    "recruiting",
    "recruitment",
    "solutions",
    "staffing",
    "talent",
    "ventures",
}

_AGENCY_TEXT_MARKERS = (
    "on behalf of its partner",
    "on behalf of our client",
    "partnering with one of",
    "recruiting for our client",
    "hiring externally for",
)


@dataclass(frozen=True)
class PostingIdentityEvidence:
    classification: str
    employer_name: str | None = None
    reasons: tuple[str, ...] = ()
    employer_mentions: int = 0
    employer_contexts: int = 0

    def trace(self) -> dict:
        return {
            "classification": self.classification,
            "employer_name": self.employer_name,
            "reasons": list(self.reasons),
            "employer_mentions": self.employer_mentions,
            "employer_contexts": self.employer_contexts,
        }


class LinkedInPostingIdentityProbe:
    """Extract conservative publisher/employer evidence from a public job page."""

    def __init__(self, fetcher: FetchClient) -> None:
        self.fetcher = fetcher

    def should_probe(self, publisher_name: str) -> bool:
        tokens = set(re.findall(r"[a-z0-9]+", publisher_name.casefold()))
        return bool(tokens.intersection(_INTERMEDIARY_NAME_MARKERS))

    def probe(
        self,
        publisher_name: str,
        linkedin_job_url: str | None,
    ) -> PostingIdentityEvidence:
        if not linkedin_job_url or not self.should_probe(publisher_name):
            return PostingIdentityEvidence(
                "not_applicable",
                reasons=("publisher name did not trigger bounded intermediary probe",),
            )
        if not _is_public_linkedin_job_url(linkedin_job_url):
            return PostingIdentityEvidence(
                "unavailable",
                reasons=("job URL is not a public LinkedIn detail URL",),
            )
        try:
            page = self.fetcher.fetch(linkedin_job_url)
        except FetchError as exc:
            return PostingIdentityEvidence(
                "unavailable",
                reasons=(f"public job detail fetch failed: {exc}",),
            )

        descriptions = _job_posting_descriptions(page.html)
        if not descriptions:
            return PostingIdentityEvidence(
                "unavailable",
                reasons=("public job detail did not contain JobPosting JSON-LD",),
            )

        description = max(descriptions, key=len)
        plain_text = _plain_text(description)
        candidate = _strong_employer_candidate(
            description,
            plain_text,
            publisher_name,
        )
        if candidate is not None:
            name, mentions, contexts = candidate
            return PostingIdentityEvidence(
                "alternate_employer",
                employer_name=name,
                reasons=(
                    "description repeatedly uses a different organization in employer-owned contexts",
                ),
                employer_mentions=mentions,
                employer_contexts=contexts,
            )

        normalized_text = plain_text.casefold()
        agency_markers = [
            marker for marker in _AGENCY_TEXT_MARKERS if marker in normalized_text
        ]
        if agency_markers:
            return PostingIdentityEvidence(
                "agency_unresolved",
                reasons=tuple(
                    f"job description marker: {marker}" for marker in agency_markers
                ),
            )
        return PostingIdentityEvidence(
            "publisher_unconfirmed",
            reasons=("no alternate employer or undisclosed-client marker was verified",),
        )


def _is_public_linkedin_job_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and (host == "linkedin.com" or host.endswith(".linkedin.com"))
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 80, 443}
        and re.match(r"^/jobs/view/[^/?#]+/?$", parsed.path) is not None
    )


def _job_posting_descriptions(html: str) -> list[str]:
    descriptions: list[str] = []
    for attrs, body in re.findall(
        r"<script\b([^>]*)>(.*?)</script>",
        html,
        flags=re.I | re.S,
    ):
        if "application/ld+json" not in attrs.casefold():
            continue
        try:
            payload = json.loads(unescape(body.strip()))
        except (json.JSONDecodeError, TypeError):
            continue
        for posting in _walk_job_postings(payload):
            description = posting.get("description")
            if isinstance(description, str) and description.strip():
                descriptions.append(unescape(description.strip()))
    return descriptions


def _walk_job_postings(value):
    if isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(kind).casefold() == "jobposting" for kind in types):
            yield value
        for child in value.values():
            yield from _walk_job_postings(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_job_postings(item)


def _strong_employer_candidate(
    description: str,
    plain_text: str,
    publisher_name: str,
) -> tuple[str, int, int] | None:
    candidates = re.findall(
        r"\bAt\s+(?:<strong>\s*)?([A-Z][A-Za-z0-9&| .'-]{1,60}?)(?:\s*</strong>)?\s*,",
        description,
        flags=re.I,
    )
    publisher_key = _identity_key(publisher_name)
    for candidate in candidates:
        name = " ".join(candidate.split()).strip(" .,-")
        if not name or _identity_key(name) == publisher_key:
            continue
        mentions = len(
            re.findall(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", plain_text, re.I)
        )
        contexts = sum(
            bool(re.search(pattern.format(name=re.escape(name)), plain_text, re.I))
            for pattern in (
                r"\bAt\s+{name}\s*,",
                r"\b{name}\s+Benefits\b",
                r"\bWhen\s+You\s+Join\s+{name}\b",
                r"\b{name}\s+will\s+not\s+ask\b",
            )
        )
        has_employer_owned_context = bool(
            re.search(
                rf"\b{re.escape(name)}\s+(?:Benefits|will\s+not\s+ask)\b",
                plain_text,
                re.I,
            )
        )
        if mentions >= 3 and contexts >= 2 and has_employer_owned_context:
            return name, mentions, contexts
    return None


def _identity_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _plain_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)
