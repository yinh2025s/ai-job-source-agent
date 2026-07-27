from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from urllib.parse import urlparse


_SAFE_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")


@dataclass(frozen=True)
class JobSearchInteraction:
    """A bounded browser action discovered from one public job-search form."""

    form_ordinal: int
    query_name: str | None
    target_title: str
    submit_text: str
    query_id: str | None = None
    query_placeholder: str | None = None
    submit_tag: str = "button"
    declared_action_url: str | None = None
    kind: str = "job_search_form"

    def __post_init__(self) -> None:
        if self.kind != "job_search_form":
            raise ValueError("unsupported browser interaction kind")
        if not isinstance(self.form_ordinal, int) or not 0 <= self.form_ordinal < 32:
            raise ValueError("form ordinal must be between 0 and 31")
        if self.query_name is not None and (
            not isinstance(self.query_name, str)
            or not _SAFE_FIELD.fullmatch(self.query_name)
        ):
            raise ValueError("query field name is unsafe")
        if self.query_id is not None and (
            not isinstance(self.query_id, str)
            or not _SAFE_FIELD.fullmatch(self.query_id)
        ):
            raise ValueError("query field id is unsafe")
        if self.query_placeholder is not None and (
            not isinstance(self.query_placeholder, str)
            or not _SAFE_TEXT.fullmatch(self.query_placeholder.strip())
        ):
            raise ValueError("query field placeholder is unsafe")
        if not any((self.query_name, self.query_id, self.query_placeholder)):
            raise ValueError("a semantic query field locator is required")
        if self.submit_tag not in {"a", "button", "input", "span"}:
            raise ValueError("submit tag is unsupported")
        if self.declared_action_url is not None:
            try:
                parsed_action = urlparse(self.declared_action_url)
                action_port = parsed_action.port
            except (TypeError, ValueError):
                raise ValueError("declared action URL is unsafe") from None
            if (
                len(self.declared_action_url) > 2048
                or parsed_action.scheme.casefold() != "https"
                or not parsed_action.hostname
                or parsed_action.username is not None
                or parsed_action.password is not None
                or action_port not in {None, 443}
                or parsed_action.fragment
            ):
                raise ValueError("declared action URL is unsafe")
        for name, value in (
            ("target title", self.target_title),
            ("submit text", self.submit_text),
        ):
            if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value.strip()):
                raise ValueError(f"{name} is unsafe")

    def fingerprint(self) -> str:
        values = asdict(self)
        if values["declared_action_url"] is None:
            values.pop("declared_action_url")
        payload = json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


BrowserInteraction = JobSearchInteraction
