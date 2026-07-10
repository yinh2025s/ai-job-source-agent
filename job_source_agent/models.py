from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CompanyInput:
    linkedin_job_url: str = ""
    company_name: str = ""
    company_website_url: str = ""
    hiring_entity_name: str | None = None
    career_root_url: str | None = None
    linkedin_html_path: str | None = None
    linkedin_company_url: str | None = None
    job_title: str | None = None
    job_location: str | None = None
    source: str = "input"
    source_trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class LinkCandidate:
    url: str
    text: str
    source_url: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    company_name: str
    company_website_url: str
    hiring_entity_name: str | None = None
    career_root_url: str | None = None
    linkedin_job_url: str = ""
    linkedin_company_url: str | None = None
    linkedin_job_title: str | None = None
    linkedin_job_location: str | None = None
    career_page_url: str | None = None
    job_list_page_url: str | None = None
    open_position_url: str | None = None
    status: str = "failed"
    error: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def result_record(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "company_website_url": self.company_website_url,
            "hiring_entity_name": self.hiring_entity_name,
            "career_root_url": self.career_root_url,
            "linkedin_job_url": self.linkedin_job_url,
            "linkedin_company_url": self.linkedin_company_url,
            "linkedin_job_title": self.linkedin_job_title,
            "linkedin_job_location": self.linkedin_job_location,
            "career_page_url": self.career_page_url,
            "job_list_page_url": self.job_list_page_url,
            "open_position_url": self.open_position_url,
            "status": self.status,
            "error": self.error,
        }

    def trace_record(self) -> dict[str, Any]:
        record = self.result_record()
        record["trace"] = self.trace
        return record


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items()}
    return value
