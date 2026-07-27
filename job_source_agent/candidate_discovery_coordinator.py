"""Standalone logical coordination for independent candidate-discovery routes.

This module deliberately stops before provider, tenant, hiring-relationship, or
opening validation.  It freezes the S1 facts used to discover leads, executes
each applicable producer independently, and returns a bounded, deterministic
lead set with route provenance intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .provider_candidates import MAX_PROVIDER_CANDIDATES, ProviderCandidate


class ExternalApplyObservation(str, Enum):
    """What the S1 adapter was actually able to observe on the job detail."""

    OBSERVED = "observed"
    OBSERVED_ABSENT = "observed_absent"
    NOT_OBSERVED = "not_observed"


class CandidateDiscoveryRoute(str, Enum):
    EXTERNAL_APPLY = "external_apply"
    PROVIDER_SEARCH = "provider_search"
    WEBSITE_CAREER = "website_career"


class CandidateDiscoveryRouteStatus(str, Enum):
    COMPLETED = "completed"
    NOT_APPLICABLE = "not_applicable"
    SUPPRESSED = "suppressed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


_ROUTE_ORDER = (
    CandidateDiscoveryRoute.EXTERNAL_APPLY,
    CandidateDiscoveryRoute.PROVIDER_SEARCH,
    CandidateDiscoveryRoute.WEBSITE_CAREER,
)
_LINKEDIN_HOST_SUFFIX = ".linkedin.com"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JOB_ID = re.compile(r"^[1-9][0-9]{2,24}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryInput:
    """Immutable S1 facts.  It must not be rebuilt from S2-S4 context."""

    source_company_name: str
    target_title: str | None
    target_location: str | None
    linkedin_job_url: str | None
    linkedin_job_id: str | None
    linkedin_company_url: str | None
    source: str
    source_evidence_provenance: str
    external_apply_observation: ExternalApplyObservation
    external_apply_url: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.source_company_name, "source company name", required=True, maximum=300)
        _validate_text(self.target_title, "target title", maximum=500)
        _validate_text(self.target_location, "target location", maximum=500)
        _validate_identifier(self.source, "source")
        _validate_identifier(self.source_evidence_provenance, "source evidence provenance")
        if not isinstance(self.external_apply_observation, ExternalApplyObservation):
            raise TypeError("External Apply observation state is invalid")
        if (self.linkedin_job_url is None) != (self.linkedin_job_id is None):
            raise ValueError("LinkedIn job URL and job ID must be supplied together")
        if self.linkedin_job_id is not None:
            if not isinstance(self.linkedin_job_id, str) or not _JOB_ID.fullmatch(self.linkedin_job_id):
                raise ValueError("LinkedIn job ID is invalid")
            job_url = canonicalize_linkedin_evidence_url(
                self.linkedin_job_url,
                "LinkedIn job URL",
            )
            if self.linkedin_job_id not in _linkedin_job_ids(job_url):
                raise ValueError("LinkedIn job URL does not bind the supplied job ID")
            object.__setattr__(self, "linkedin_job_url", job_url)
        if self.linkedin_company_url is not None:
            object.__setattr__(
                self,
                "linkedin_company_url",
                canonicalize_linkedin_evidence_url(
                    self.linkedin_company_url,
                    "LinkedIn company URL",
                ),
            )

        if self.external_apply_observation is ExternalApplyObservation.OBSERVED:
            if self.external_apply_url is None:
                raise ValueError("Observed External Apply requires a sanitized URL")
            object.__setattr__(
                self,
                "external_apply_url",
                _canonical_candidate_url(self.external_apply_url, self.source_company_name),
            )
        elif self.external_apply_url is not None:
            raise ValueError("External Apply URL requires observed state")


@dataclass(frozen=True, slots=True)
class WebsiteCareerRouteInput:
    """Verified S2-S4 evidence available only to the Website/Career route."""

    company_website_url: str | None
    career_page_url: str | None
    evidence_scope: str
    evidence_urls: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.company_website_url is None and self.career_page_url is None:
            raise ValueError("Website/Career route requires a verified URL")
        _validate_identifier(self.evidence_scope, "website evidence scope")
        if not isinstance(self.evidence_urls, tuple) or not self.evidence_urls:
            raise ValueError("Website/Career route requires immutable evidence URLs")
        if self.company_website_url is not None:
            object.__setattr__(
                self,
                "company_website_url",
                _canonical_candidate_url(self.company_website_url, "website evidence"),
            )
        if self.career_page_url is not None:
            object.__setattr__(
                self,
                "career_page_url",
                _canonical_candidate_url(self.career_page_url, "career evidence"),
            )
        canonical_evidence = tuple(
            _canonical_candidate_url(url, "website evidence") for url in self.evidence_urls
        )
        if len(set(canonical_evidence)) != len(canonical_evidence):
            raise ValueError("Website/Career evidence URLs must be unique")
        if not set(self.route_urls).issubset(canonical_evidence):
            raise ValueError("Website/Career evidence must cover every route URL")
        object.__setattr__(self, "evidence_urls", canonical_evidence)

    @property
    def route_urls(self) -> tuple[str, ...]:
        return tuple(
            url
            for url in (self.company_website_url, self.career_page_url)
            if url is not None
        )


@dataclass(frozen=True, slots=True)
class WebsiteCareerRouteSuppression:
    """A typed S3 rejection that can only suppress matching Website evidence."""

    rejected_url: str
    evidence_scope: str
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rejected_url",
            _canonical_candidate_url(self.rejected_url, "suppressed website evidence"),
        )
        _validate_identifier(self.evidence_scope, "suppression evidence scope")
        _validate_identifier(self.reason_code, "suppression reason code")


@dataclass(frozen=True, slots=True)
class RouteProvenance:
    """A privacy-safe, immutable producer report; it is never identity evidence."""

    producer: str
    trace: tuple[tuple[str, str], ...] = ()
    elapsed_ms: int = 0
    request_count: int = 0
    query_count: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.producer, "route producer")
        if not isinstance(self.trace, tuple):
            raise TypeError("Route provenance trace must be immutable")
        for item in self.trace:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) and value and not _CONTROL.search(value) for value in item)
            ):
                raise ValueError("Route provenance trace is invalid")
        for value, label in (
            (self.elapsed_ms, "elapsed milliseconds"),
            (self.request_count, "request count"),
            (self.query_count, "query count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Route provenance {label} is invalid")
        if not isinstance(self.truncated, bool):
            raise TypeError("Route provenance truncation must be boolean")


@dataclass(frozen=True, slots=True)
class RouteFailure:
    reason_code: str
    retryable: bool
    error_type: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.reason_code, "route failure reason code")
        if not isinstance(self.retryable, bool):
            raise TypeError("Route failure retryability must be boolean")
        if self.error_type is not None:
            _validate_identifier(self.error_type.casefold(), "route failure type")


@dataclass(frozen=True, slots=True)
class RouteProducerOutput:
    """Safe leads and diagnostics returned by one injected route producer."""

    candidates: tuple[ProviderCandidate, ...]
    provenance: RouteProvenance
    status: CandidateDiscoveryRouteStatus = CandidateDiscoveryRouteStatus.COMPLETED
    failure: RouteFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            raise TypeError("Route producer candidates must be immutable")
        if len(self.candidates) > MAX_PROVIDER_CANDIDATES:
            raise ValueError("Route producer exceeds the candidate bound")
        if any(not isinstance(candidate, ProviderCandidate) for candidate in self.candidates):
            raise TypeError("Route producer emitted an invalid candidate")
        if not isinstance(self.provenance, RouteProvenance):
            raise TypeError("Route producer provenance is invalid")
        if self.status not in {
            CandidateDiscoveryRouteStatus.COMPLETED,
            CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
            CandidateDiscoveryRouteStatus.FAILED,
        }:
            raise ValueError("Producer output status is invalid")
        if self.status is CandidateDiscoveryRouteStatus.COMPLETED:
            if self.failure is not None:
                raise ValueError("Completed producer output cannot carry failure")
        elif self.candidates or self.failure is None:
            raise ValueError("Failed or exhausted producer output requires typed failure only")


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryRouteResult:
    route: CandidateDiscoveryRoute
    status: CandidateDiscoveryRouteStatus
    candidates: tuple[ProviderCandidate, ...]
    provenance: RouteProvenance
    failure: RouteFailure | None = None
    suppression: WebsiteCareerRouteSuppression | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.route, CandidateDiscoveryRoute):
            raise TypeError("Candidate discovery route is invalid")
        if not isinstance(self.status, CandidateDiscoveryRouteStatus):
            raise TypeError("Candidate discovery route status is invalid")
        if not isinstance(self.candidates, tuple):
            raise TypeError("Route result candidates must be immutable")
        if any(not isinstance(candidate, ProviderCandidate) for candidate in self.candidates):
            raise TypeError("Route result contains an invalid candidate")
        if not isinstance(self.provenance, RouteProvenance):
            raise TypeError("Route result provenance is invalid")
        if self.status is CandidateDiscoveryRouteStatus.COMPLETED:
            if self.failure is not None or self.suppression is not None:
                raise ValueError("Completed route cannot carry failure or suppression")
        elif self.status is CandidateDiscoveryRouteStatus.SUPPRESSED:
            if self.route is not CandidateDiscoveryRoute.WEBSITE_CAREER:
                raise ValueError("Only Website/Career route can be suppressed")
            if self.candidates or self.failure is not None or self.suppression is None:
                raise ValueError("Suppressed route must carry only typed suppression")
        else:
            if self.candidates or self.suppression is not None:
                raise ValueError("Non-completed route cannot carry candidates")
            if self.status in {
                CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
                CandidateDiscoveryRouteStatus.FAILED,
            } and self.failure is None:
                raise ValueError("Failed or exhausted route requires typed failure")
            if self.status is CandidateDiscoveryRouteStatus.NOT_APPLICABLE and self.failure is None:
                raise ValueError("Inapplicable route requires typed reason")


@dataclass(frozen=True, slots=True)
class CandidateRouteAttribution:
    """All route origins retained for one URL after coordinator-level dedupe."""

    candidate_url: str
    routes: tuple[CandidateDiscoveryRoute, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_url",
            _canonical_candidate_url(self.candidate_url, "candidate attribution URL"),
        )
        if not isinstance(self.routes, tuple) or not self.routes:
            raise ValueError("Candidate attribution requires immutable routes")
        if any(not isinstance(route, CandidateDiscoveryRoute) for route in self.routes):
            raise TypeError("Candidate attribution contains an invalid route")
        ordered = tuple(route for route in _ROUTE_ORDER if route in self.routes)
        if ordered != self.routes or len(set(self.routes)) != len(self.routes):
            raise ValueError("Candidate attribution routes are not deterministic")


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryCoordinatorResult:
    candidates: tuple[ProviderCandidate, ...]
    route_results: tuple[CandidateDiscoveryRouteResult, ...]
    attributions: tuple[CandidateRouteAttribution, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            raise TypeError("Coordinator candidates must be immutable")
        if len(self.candidates) > MAX_PROVIDER_CANDIDATES:
            raise ValueError("Coordinator candidates exceed the global bound")
        if any(not isinstance(candidate, ProviderCandidate) for candidate in self.candidates):
            raise TypeError("Coordinator emitted an invalid candidate")
        if tuple(result.route for result in self.route_results) != _ROUTE_ORDER:
            raise ValueError("Coordinator route results must be complete and ordered")
        candidate_ids = tuple(_candidate_identity(candidate) for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Coordinator candidates must be deduplicated")
        attribution_ids = tuple(_candidate_identity_from_url(item.candidate_url) for item in self.attributions)
        if candidate_ids != attribution_ids:
            raise ValueError("Coordinator attribution must align with candidates")
        if not isinstance(self.truncated, bool):
            raise TypeError("Coordinator truncation must be boolean")


ExternalApplyProducer = Callable[[CandidateDiscoveryInput], RouteProducerOutput]
ProviderSearchProducer = Callable[[CandidateDiscoveryInput], RouteProducerOutput]
WebsiteCareerProducer = Callable[[CandidateDiscoveryInput, WebsiteCareerRouteInput], RouteProducerOutput]


class CandidateDiscoveryCoordinator:
    """Run applicable discovery routes and merge untrusted leads deterministically."""

    def __init__(
        self,
        *,
        external_apply: ExternalApplyProducer,
        provider_search: ProviderSearchProducer,
        website_career: WebsiteCareerProducer,
        candidate_limit: int = MAX_PROVIDER_CANDIDATES,
    ) -> None:
        if not callable(external_apply) or not callable(provider_search) or not callable(website_career):
            raise TypeError("Candidate discovery producers must be callable")
        if (
            isinstance(candidate_limit, bool)
            or not isinstance(candidate_limit, int)
            or not 1 <= candidate_limit <= MAX_PROVIDER_CANDIDATES
        ):
            raise ValueError("Candidate coordinator limit is invalid")
        self._external_apply = external_apply
        self._provider_search = provider_search
        self._website_career = website_career
        self._candidate_limit = candidate_limit

    def discover(
        self,
        source_input: CandidateDiscoveryInput,
        *,
        website_career_input: WebsiteCareerRouteInput | None = None,
        website_career_suppression: WebsiteCareerRouteSuppression | None = None,
    ) -> CandidateDiscoveryCoordinatorResult:
        if not isinstance(source_input, CandidateDiscoveryInput):
            raise TypeError("Candidate coordinator requires immutable S1 input")
        if website_career_input is not None and not isinstance(website_career_input, WebsiteCareerRouteInput):
            raise TypeError("Website/Career input is invalid")
        if website_career_suppression is not None and not isinstance(
            website_career_suppression, WebsiteCareerRouteSuppression
        ):
            raise TypeError("Website/Career suppression is invalid")

        external_result = self._run_external_apply(source_input)
        provider_result = self._run_producer(
            CandidateDiscoveryRoute.PROVIDER_SEARCH,
            self._provider_search,
            source_input,
        )
        website_result = self._run_website_career(
            source_input,
            website_career_input,
            website_career_suppression,
        )
        route_results = (external_result, provider_result, website_result)
        candidates, attributions, truncated = self._merge(route_results)
        return CandidateDiscoveryCoordinatorResult(
            candidates=candidates,
            route_results=route_results,
            attributions=attributions,
            truncated=truncated,
        )

    def _run_external_apply(
        self,
        source_input: CandidateDiscoveryInput,
    ) -> CandidateDiscoveryRouteResult:
        if source_input.external_apply_observation is ExternalApplyObservation.NOT_OBSERVED:
            return _not_applicable(
                CandidateDiscoveryRoute.EXTERNAL_APPLY,
                "detail_not_observed",
            )
        if source_input.external_apply_observation is ExternalApplyObservation.OBSERVED_ABSENT:
            return _not_applicable(
                CandidateDiscoveryRoute.EXTERNAL_APPLY,
                "external_apply_observed_absent",
            )
        return self._run_producer(
            CandidateDiscoveryRoute.EXTERNAL_APPLY,
            self._external_apply,
            source_input,
        )

    def _run_website_career(
        self,
        source_input: CandidateDiscoveryInput,
        route_input: WebsiteCareerRouteInput | None,
        suppression: WebsiteCareerRouteSuppression | None,
    ) -> CandidateDiscoveryRouteResult:
        if route_input is None:
            return _not_applicable(
                CandidateDiscoveryRoute.WEBSITE_CAREER,
                "verified_website_career_evidence_absent",
            )
        if suppression is not None and _suppression_matches_route(suppression, route_input):
            return CandidateDiscoveryRouteResult(
                route=CandidateDiscoveryRoute.WEBSITE_CAREER,
                status=CandidateDiscoveryRouteStatus.SUPPRESSED,
                candidates=(),
                provenance=RouteProvenance("coordinator"),
                suppression=suppression,
            )
        try:
            output = self._website_career(source_input, route_input)
            return _completed(CandidateDiscoveryRoute.WEBSITE_CAREER, output)
        except Exception as exc:
            return _failed(CandidateDiscoveryRoute.WEBSITE_CAREER, exc)

    def _run_producer(
        self,
        route: CandidateDiscoveryRoute,
        producer: Callable[[CandidateDiscoveryInput], RouteProducerOutput],
        source_input: CandidateDiscoveryInput,
    ) -> CandidateDiscoveryRouteResult:
        try:
            output = producer(source_input)
            return _completed(route, output)
        except Exception as exc:
            return _failed(route, exc)

    def _merge(
        self,
        route_results: tuple[CandidateDiscoveryRouteResult, ...],
    ) -> tuple[tuple[ProviderCandidate, ...], tuple[CandidateRouteAttribution, ...], bool]:
        grouped: dict[str, list[tuple[CandidateDiscoveryRoute, ProviderCandidate]]] = {}
        route_identities: dict[CandidateDiscoveryRoute, list[str]] = {
            route: [] for route in _ROUTE_ORDER
        }
        for result in route_results:
            if result.status is not CandidateDiscoveryRouteStatus.COMPLETED:
                continue
            for candidate in result.candidates:
                identity = _candidate_identity(candidate)
                grouped.setdefault(identity, []).append((result.route, candidate))
                route_identities[result.route].append(identity)

        representatives = {
            identity: min((candidate for _, candidate in values), key=_candidate_sort_key)
            for identity, values in grouped.items()
        }
        selected: set[str] = set()
        for route in _ROUTE_ORDER:
            identities = sorted(
                set(route_identities[route]),
                key=lambda identity: _candidate_sort_key(representatives[identity]),
            )
            if identities:
                # A shared URL satisfies this route's reservation and records both origins.
                selected.add(identities[0])

        all_identities = sorted(grouped, key=lambda identity: _candidate_sort_key(representatives[identity]))
        for identity in all_identities:
            if len(selected) >= self._candidate_limit:
                break
            selected.add(identity)

        ordered_identities = sorted(
            selected,
            key=lambda identity: _candidate_sort_key(representatives[identity]),
        )[: self._candidate_limit]
        candidates = tuple(representatives[identity] for identity in ordered_identities)
        attributions = tuple(
            CandidateRouteAttribution(
                candidate_url=representatives[identity].url,
                routes=tuple(
                    route
                    for route in _ROUTE_ORDER
                    if any(origin is route for origin, _ in grouped[identity])
                ),
            )
            for identity in ordered_identities
        )
        return candidates, attributions, len(grouped) > len(candidates)


def _completed(
    route: CandidateDiscoveryRoute,
    output: RouteProducerOutput,
) -> CandidateDiscoveryRouteResult:
    if not isinstance(output, RouteProducerOutput):
        raise TypeError("Candidate discovery producer returned an invalid output")
    return CandidateDiscoveryRouteResult(
        route=route,
        status=output.status,
        candidates=output.candidates,
        provenance=output.provenance,
        failure=output.failure,
    )


def _not_applicable(
    route: CandidateDiscoveryRoute,
    reason_code: str,
) -> CandidateDiscoveryRouteResult:
    return CandidateDiscoveryRouteResult(
        route=route,
        status=CandidateDiscoveryRouteStatus.NOT_APPLICABLE,
        candidates=(),
        provenance=RouteProvenance("coordinator"),
        failure=RouteFailure(reason_code=reason_code, retryable=False),
    )


def _failed(
    route: CandidateDiscoveryRoute,
    exc: Exception,
) -> CandidateDiscoveryRouteResult:
    return CandidateDiscoveryRouteResult(
        route=route,
        status=CandidateDiscoveryRouteStatus.FAILED,
        candidates=(),
        provenance=RouteProvenance("coordinator"),
        failure=RouteFailure(
            reason_code="producer_exception",
            retryable=False,
            error_type=type(exc).__name__,
        ),
    )


def _suppression_matches_route(
    suppression: WebsiteCareerRouteSuppression,
    route_input: WebsiteCareerRouteInput,
) -> bool:
    return (
        suppression.evidence_scope == route_input.evidence_scope
        and suppression.rejected_url in route_input.route_urls
        and suppression.rejected_url in route_input.evidence_urls
    )


def _candidate_sort_key(candidate: ProviderCandidate) -> tuple[int, int, str, str]:
    return (
        -candidate.priority,
        candidate.result_rank if candidate.result_rank is not None else 0,
        candidate.url.casefold(),
        candidate.source_kind,
    )


def _candidate_identity(candidate: ProviderCandidate) -> str:
    return _candidate_identity_from_url(candidate.url)


def _candidate_identity_from_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/") or "/",
            "",
            parsed.query,
        )
    )


def _canonical_candidate_url(value: object, company_name: str) -> str:
    try:
        return ProviderCandidate(
            url=value,  # type: ignore[arg-type]
            source_kind="external_apply",
            source_url=value,  # type: ignore[arg-type]
            company_name=company_name,
        ).url
    except (TypeError, ValueError) as exc:
        raise ValueError("Candidate discovery URL is invalid") from exc


def canonicalize_linkedin_evidence_url(value: object, label: str) -> str:
    """Canonicalize strict LinkedIn evidence without weakening optional adapters."""

    canonical = _canonical_candidate_url(value, "linkedin evidence")
    host = urlsplit(canonical).hostname
    if host is None or (host != "linkedin.com" and not host.endswith(_LINKEDIN_HOST_SUFFIX)):
        raise ValueError(f"{label} must use linkedin.com")
    return canonical


def _linkedin_job_ids(url: str) -> frozenset[str]:
    parsed = urlsplit(url)
    identifiers = set(re.findall(r"/jobs/view/([1-9][0-9]{2,24})(?:/|$)", parsed.path))
    view_match = re.search(r"/jobs/view/([^/]+)(?:/|$)", parsed.path)
    if view_match is not None:
        slug_id = re.search(r"(?:^|-)([1-9][0-9]{2,24})$", view_match.group(1))
        if slug_id is not None:
            identifiers.add(slug_id.group(1))
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in {"currentjobid", "jobid"} and _JOB_ID.fullmatch(value):
            identifiers.add(value)
    return frozenset(identifiers)


def _validate_text(value: object, label: str, *, required: bool = False, maximum: int) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum or _CONTROL.search(value):
        raise ValueError(f"{label} is invalid")


def _validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is invalid")
