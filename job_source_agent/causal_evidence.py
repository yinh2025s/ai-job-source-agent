from __future__ import annotations

from collections import defaultdict
from typing import Iterable
from urllib.parse import urlparse
import re


CAUSAL_LEDGER_SCHEMA_VERSION = "1.1"
_JOB_ID = re.compile(r"^/jobs/view/(?:.*-)?([0-9]{6,})/?$")
_TRANSPORT_REASONS = {
    "FETCH_FAILED",
    "NETWORK_TIMEOUT",
    "DNS_FAILURE",
    "CONNECTION_FAILED",
    "TLS_FAILURE",
    "READ_TIMEOUT",
    "INCOMPLETE_READ",
    "PROVIDER_FETCH_FAILED",
}
_BUDGET_REASONS = {
    "FETCH_BUDGET_EXHAUSTED",
    "COMPANY_TIME_BUDGET_EXHAUSTED",
    "OPENING_TIME_BUDGET_EXHAUSTED",
}
_IDENTITY_REASONS = {
    "COMPANY_IDENTITY_AMBIGUOUS",
    "RESULT_IDENTITY_MISMATCH",
    "HIRING_RELATIONSHIP_UNVERIFIED",
    "TENANT_IDENTITY_MISMATCH",
}
_VERIFIED_NO_MATCH = {
    "verified_inventory_empty",
    "verified_inventory_no_match",
}
_EXTERNAL_BLOCKED = {
    "BOT_PROTECTION",
    "CAPTCHA_REQUIRED",
    "HTTP_FORBIDDEN",
    "LOGIN_REQUIRED",
    "UNVERIFIABLE_THIRD_PARTY_HANDOFF",
}
_TRUSTED_CAREER_ORIGINS = {
    "page_link",
    "verified_homepage_navigation",
    "stored_evidence",
    "linkedin_official_website",
}


def build_causal_ledger(
    artifacts: Iterable[tuple[str, list[dict]]],
    *,
    minimum_company_count: int = 3,
    cohort_records: list[dict] | None = None,
    accepted_terminals: dict[str, dict] | None = None,
    reviewed_cluster_signatures: set[str] | None = None,
) -> dict:
    if minimum_company_count < 1:
        raise ValueError("minimum_company_count must be positive")

    accepted = _validate_accepted_terminals(accepted_terminals or {})
    reviewed_clusters = set(reviewed_cluster_signatures or ())
    cohort_ids = _cohort_identities(cohort_records) if cohort_records is not None else None
    latest: dict[str, tuple[str, dict]] = {}
    durable: dict[str, str] = {}
    source_counts: list[dict] = []
    for artifact_index, (label, records) in enumerate(artifacts):
        if not isinstance(label, str) or not label.strip():
            raise ValueError("artifact label must be a non-empty string")
        if not isinstance(records, list):
            raise ValueError(f"artifact {label!r} must contain a JSON list")
        if artifact_index == 0 and cohort_ids is None:
            cohort_ids = _cohort_identities(records)
        seen: set[str] = set()
        for record in records:
            job_id = linkedin_job_id(record.get("linkedin_job_url"))
            if cohort_ids is not None and job_id not in cohort_ids:
                raise ValueError(
                    f"artifact {label!r} contains LinkedIn job id outside the frozen cohort: {job_id}"
                )
            if job_id in seen:
                raise ValueError(f"artifact {label!r} contains duplicate LinkedIn job id {job_id}")
            seen.add(job_id)
            outcome = _durable_outcome(record)
            prior_outcome = durable.get(job_id, "unresolved")
            terminal_accepted = (
                job_id in accepted
                and accepted[job_id]["durable_outcome"] == outcome
            )
            if artifact_index == 0:
                latest[job_id] = (label, record)
                durable[job_id] = outcome
            elif terminal_accepted:
                latest[job_id] = (label, record)
                durable[job_id] = outcome
            elif prior_outcome != "unresolved":
                # A later retryable or partial observation cannot erase an
                # already audited durable terminal.
                continue
            elif outcome == "unresolved":
                latest[job_id] = (label, record)
            # Unlisted focused terminal successes are intentionally ignored.
        source_counts.append({"label": label, "record_count": len(records)})

    if cohort_ids is not None:
        missing = sorted(set(cohort_ids) - set(latest))
        if missing:
            raise ValueError(
                f"projection is missing {len(missing)} frozen cohort job ids: {', '.join(missing[:5])}"
            )
    unknown_acceptance = sorted(set(accepted) - set(latest))
    if unknown_acceptance:
        raise ValueError(
            "accepted terminal manifest contains ids absent from artifacts: "
            + ", ".join(unknown_acceptance[:5])
        )

    ledger_records = []
    for job_id, (label, record) in sorted(latest.items()):
        terminal = accepted.get(job_id)
        ledger_records.append(
            classify_causal_record(
                record,
                artifact_label=label,
                durable_outcome_override=(
                    terminal["durable_outcome"] if terminal else None
                ),
                terminal_evidence_ref=(terminal["evidence_ref"] if terminal else None),
            )
        )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in ledger_records:
        if record["included_in_causal_backlog"]:
            grouped[record["cluster_signature"]].append(record)

    clusters = []
    for signature, records in grouped.items():
        companies = sorted(
            {record["company_name"] for record in records},
            key=str.casefold,
        )
        exemplar = records[0]
        clusters.append(
            {
                "cluster_signature": signature,
                "category": exemplar["category"],
                "trigger": exemplar["trigger"],
                "code_path": exemplar["code_path"],
                "record_count": len(records),
                "company_count": len({name.casefold() for name in companies}),
                "companies": companies,
                "linkedin_job_ids": sorted(record["linkedin_job_id"] for record in records),
                "meets_company_count": (
                    len({name.casefold() for name in companies}) >= minimum_company_count
                ),
                "reviewed_for_implementation": signature in reviewed_clusters,
                "qualified_for_implementation": (
                    len({name.casefold() for name in companies}) >= minimum_company_count
                    and exemplar["category"] != "unclassified"
                    and signature in reviewed_clusters
                ),
                "qualification_blockers": _qualification_blockers(
                    signature,
                    exemplar["category"],
                    len({name.casefold() for name in companies}),
                    minimum_company_count,
                    reviewed_clusters,
                ),
            }
        )
    clusters.sort(
        key=lambda item: (
            not item["qualified_for_implementation"],
            -item["company_count"],
            item["cluster_signature"],
        )
    )
    return {
        "schema_version": CAUSAL_LEDGER_SCHEMA_VERSION,
        "minimum_company_count": minimum_company_count,
        "source_precedence": source_counts,
        "frozen_cohort_count": len(cohort_ids) if cohort_ids is not None else None,
        "accepted_terminal_count": len(accepted),
        "record_count": len(ledger_records),
        "causal_backlog_count": sum(
            record["included_in_causal_backlog"] for record in ledger_records
        ),
        "qualified_cluster_count": sum(
            cluster["qualified_for_implementation"] for cluster in clusters
        ),
        "candidate_cluster_count": sum(
            cluster["meets_company_count"] and cluster["category"] != "unclassified"
            for cluster in clusters
        ),
        "records": ledger_records,
        "clusters": clusters,
        "durable_outcome_counts": _count_values(
            record["durable_outcome"] for record in ledger_records
        ),
    }


def classify_causal_record(
    record: dict,
    *,
    artifact_label: str,
    durable_outcome_override: str | None = None,
    terminal_evidence_ref: str | None = None,
) -> dict:
    if not isinstance(record, dict):
        raise ValueError("result record must be a mapping")
    job_id = linkedin_job_id(record.get("linkedin_job_url"))
    company_name = _required_string(record.get("company_name"), "company_name")
    reason = str(record.get("error_code") or "")
    outcome = durable_outcome_override or _durable_outcome(record)
    if outcome not in {"exact", "verified_no_match", "external_blocked", "unresolved"}:
        raise ValueError(f"invalid durable outcome override: {outcome!r}")

    if outcome != "unresolved":
        classification = {
            "category": "terminal",
            "trigger": outcome,
            "code_path": "terminal_evidence",
            "evidence": {},
            "contributing_causes": [],
            "bypass_opportunities": [],
            "evidence_paths": [],
            "confidence": "high",
        }
    else:
        classification = _causal_classification(record, reason)

    category = classification["category"]
    trigger = classification["trigger"]
    code_path = classification["code_path"]
    signature = "|".join((category, trigger, code_path))
    return {
        "linkedin_job_id": job_id,
        "linkedin_job_url": record["linkedin_job_url"],
        "company_name": company_name,
        "job_title": str(record.get("linkedin_job_title") or ""),
        "artifact_label": artifact_label,
        "terminal_reason_code": reason or None,
        "durable_outcome": outcome,
        "terminal_evidence_ref": terminal_evidence_ref,
        "included_in_causal_backlog": outcome == "unresolved",
        "category": category,
        "trigger": trigger,
        "code_path": code_path,
        "cluster_signature": signature,
        "evidence": classification["evidence"],
        "contributing_causes": classification["contributing_causes"],
        "bypass_opportunities": classification["bypass_opportunities"],
        "evidence_paths": classification["evidence_paths"],
        "confidence": classification["confidence"],
    }


def linkedin_job_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("linkedin_job_url must be a string")
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise ValueError(f"invalid LinkedIn job URL: {value!r}") from error
    host = (parsed.hostname or "").casefold()
    match = _JOB_ID.match(parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or host not in {"linkedin.com", "www.linkedin.com"}
        or match is None
    ):
        raise ValueError(f"invalid LinkedIn job URL: {value!r}")
    return match.group(1)


def _durable_outcome(record: dict) -> str:
    if record.get("open_position_url"):
        return "exact"
    availability = _availability_diagnostic(record)
    if availability in _VERIFIED_NO_MATCH:
        return "verified_no_match"
    reason = str(record.get("error_code") or "")
    identity = record.get("identity_assertion")
    provider = identity.get("provider") if isinstance(identity, dict) else None
    relationship_verified = (
        isinstance(provider, dict) and provider.get("relationship_verified") is True
    )
    if reason in _EXTERNAL_BLOCKED and relationship_verified:
        return "external_blocked"
    return "unresolved"


def _causal_classification(record: dict, reason: str) -> dict:
    identity = record.get("identity_assertion")
    failure_codes = sorted(
        {
            str(code)
            for code in (
                identity.get("failure_codes", [])
                if isinstance(identity, dict)
                else []
            )
            if code
        }
    )
    verdict = identity.get("verdict") if isinstance(identity, dict) else None
    if reason in _IDENTITY_REASONS or verdict == "rejected" or failure_codes:
        trigger = "+".join(failure_codes) or reason or "identity_rejected"
        path = (
            "result_validation.identity_gate"
            if reason == "RESULT_IDENTITY_MISMATCH" or verdict == "rejected"
            else "website_resolution.company_identity_gate"
        )
        return _classification(
            "candidate_identity_rejected",
            trigger,
            path,
            evidence={"identity_verdict": verdict, "failure_codes": failure_codes},
            evidence_paths=["identity_assertion.verdict", "identity_assertion.failure_codes"],
        )

    career = _stage_trace(record, "career_discovery")
    opening = _stage_trace(record, "opening_match")
    candidate_failure = _trusted_candidate_transport_failure(career)
    if candidate_failure:
        contributing = []
        if _stage_transport_budget_exhausted(career):
            contributing.append("fallback_transport_budget_exhaustion")
        return _classification(
            "correct_candidate_transport_failure",
            f"evidence_backed_career_{candidate_failure['transport_phase']}",
            "career_discovery.verify_evidence_backed_candidate",
            evidence=candidate_failure,
            contributing_causes=contributing,
            evidence_paths=[
                "trace.stages.career_discovery.candidates",
                "trace.stages.career_discovery.candidate_fetch_errors",
            ],
        )

    job_list_url = record.get("job_list_page_url")
    if job_list_url and reason in _TRANSPORT_REASONS:
        transport_events = _retryable_transport_events(record)
        phase = _first_transport_phase(transport_events) or "transport"
        return _classification(
            "correct_candidate_transport_failure",
            f"verified_job_list_{phase or 'transport'}",
            "opening_match.read_verified_inventory",
            evidence={
                "candidate_url": job_list_url,
                "provider": _provider(record),
                "transport_phase": phase,
            },
            evidence_paths=[
                "job_list_page_url",
                "trace.retry_events",
                "identity_assertion.provider",
            ],
        )

    route_evaluation = _route_evaluation(record)
    bypasses = _bypass_opportunities(route_evaluation)
    relationship_gap = _relationship_verification_gap(route_evaluation)
    if relationship_gap:
        return _classification(
            "candidate_identity_rejected",
            "provider_verified_hiring_relationship_unverified",
            "job_board_discovery.hiring_relationship_gate",
            evidence=relationship_gap,
            bypass_opportunities=bypasses,
            evidence_paths=["trace.stages.job_board_discovery.route_evaluation"],
        )

    starvation = _candidate_route_starvation(record)
    if starvation:
        return _classification(
            "budget_starvation",
            starvation["trigger"],
            "job_board_discovery.candidate_route_budget",
            evidence=starvation,
            bypass_opportunities=bypasses,
            evidence_paths=starvation["evidence_paths"],
        )

    if reason in _BUDGET_REASONS or _stage_transport_budget_exhausted(career):
        stage = _failure_stage(record) or "unknown_stage"
        trigger = (
            reason
            if reason in _BUDGET_REASONS
            else "transport_dispatch_budget_exhausted"
        )
        return _classification(
            "budget_starvation",
            trigger,
            f"{stage}.budget_controller",
            evidence={"failure_stage": stage},
            bypass_opportunities=bypasses,
            evidence_paths=[
                f"trace.stages.{stage}.transport_budget",
                "stages[].reason_code",
            ],
        )

    rejected = _coordinator_rejections(record)
    if rejected:
        rejection_reasons = sorted(
            {
                str(item.get("reason_code") or item.get("reason") or "candidate_rejected")
                for item in rejected
                if isinstance(item, dict)
            }
        )
        return _classification(
            "candidate_identity_rejected",
            "+".join(rejection_reasons),
            "job_board_discovery.provider_candidate_verification",
            evidence={
                "rejection_count": len(rejected),
                "rejection_reasons": rejection_reasons,
            },
            bypass_opportunities=bypasses,
            evidence_paths=[
                "trace.stages.job_board_discovery.candidate_verification"
            ],
        )

    rejected_sources = _rejected_sources(record)
    if rejected_sources:
        return _classification(
            "linkedin_or_search_source_rejected",
            "+".join(rejected_sources),
            "job_board_discovery.candidate_sources",
            evidence={"rejected_sources": rejected_sources},
            bypass_opportunities=bypasses,
            evidence_paths=[
                "trace.stages.job_board_discovery.candidate_discovery.sources"
            ],
        )

    generation_gap = _candidate_generation_gap(record)
    if generation_gap:
        return _classification(
            "correct_candidate_not_produced",
            generation_gap["trigger"],
            "job_board_discovery.provider_search_discovery",
            evidence=generation_gap,
            bypass_opportunities=bypasses,
            evidence_paths=generation_gap["evidence_paths"],
        )

    if reason == "JOB_BOARD_PORTFOLIO_INCOMPLETE":
        return _classification(
            "correct_candidate_not_produced",
            "eligible_board_portfolio_incomplete",
            "job_board_discovery.portfolio_completeness_gate",
            evidence={"provider": _provider(record)},
            bypass_opportunities=bypasses,
            evidence_paths=[
                "trace.stages.job_board_discovery.job_board_portfolio"
            ],
        )

    if reason == "OPENING_DISCOVERY_INCOMPLETE":
        mechanism = _opening_inventory_mechanism(opening)
        return _classification(
            mechanism.get("category", "correct_candidate_not_produced"),
            mechanism["trigger"],
            mechanism["code_path"],
            evidence={
                "provider": _provider(record),
                **mechanism["evidence"],
            },
            bypass_opportunities=bypasses,
            evidence_paths=mechanism["evidence_paths"],
            confidence=mechanism["confidence"],
        )

    return _classification(
        "unclassified",
        reason or "insufficient_causal_evidence",
        f"{_failure_stage(record) or 'pipeline'}.unknown",
        bypass_opportunities=bypasses,
        confidence="insufficient",
    )


def _classification(
    category: str,
    trigger: str,
    code_path: str,
    *,
    evidence: dict | None = None,
    contributing_causes: list[str] | None = None,
    bypass_opportunities: list[str] | None = None,
    evidence_paths: list[str] | None = None,
    confidence: str = "high",
) -> dict:
    return {
        "category": category,
        "trigger": trigger,
        "code_path": code_path,
        "evidence": evidence or {},
        "contributing_causes": contributing_causes or [],
        "bypass_opportunities": bypass_opportunities or [],
        "evidence_paths": evidence_paths or [],
        "confidence": confidence,
    }


def _stage_trace(record: dict, stage: str) -> dict:
    trace = record.get("trace")
    stages = trace.get("stages") if isinstance(trace, dict) else None
    value = stages.get(stage) if isinstance(stages, dict) else None
    if isinstance(value, dict):
        return value
    fallback = {
        "career_discovery": "find_career_page",
        "job_board_discovery": "find_job_board",
        "opening_match": "match_opening",
    }.get(stage)
    return _step(record, fallback) if fallback else {}


def _trusted_candidate_transport_failure(stage: dict) -> dict | None:
    candidates = stage.get("candidates")
    errors = stage.get("candidate_fetch_errors")
    if not isinstance(candidates, list) or not isinstance(errors, list):
        return None
    by_url = {
        str(candidate.get("url") or "").rstrip("/"): candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("url")
        and (
            candidate.get("origin") in _TRUSTED_CAREER_ORIGINS
            or _integer(candidate.get("evidence_tier")) in {1, 2}
        )
    }
    for error in errors:
        if not isinstance(error, dict) or error.get("retryable") is not True:
            continue
        url = str(error.get("url") or "").rstrip("/")
        candidate = by_url.get(url)
        if not candidate:
            continue
        reason = str(error.get("reason_code") or "")
        if reason not in _TRANSPORT_REASONS:
            continue
        return {
            "candidate_url": error.get("url"),
            "candidate_origin": candidate.get("origin"),
            "evidence_tier": error.get("evidence_tier"),
            "reason_code": reason,
            "transport_phase": _transport_phase_from_error(error),
        }
    return None


def _transport_phase_from_error(error: dict) -> str:
    phase = str(error.get("transport_phase") or "")
    if phase:
        return phase
    text = str(error.get("error") or "").casefold()
    if "ssl" in text or "tls" in text:
        return "tls"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "dns" in text or "name or service" in text:
        return "dns"
    return "transport"


def _stage_transport_budget_exhausted(stage: dict) -> bool:
    budget = stage.get("transport_budget")
    return isinstance(budget, dict) and budget.get("exhausted") is True


def _candidate_discovery(record: dict) -> dict:
    stage = _stage_trace(record, "job_board_discovery")
    direct = stage.get("candidate_discovery")
    if isinstance(direct, dict):
        return direct
    for key in ("candidate_route_probe", "parallel_candidate_fallback"):
        container = stage.get(key)
        nested = container.get("candidate_discovery") if isinstance(container, dict) else None
        if isinstance(nested, dict):
            return nested
    return {}


def _route_evaluation(record: dict) -> dict:
    value = _stage_trace(record, "job_board_discovery").get("route_evaluation")
    return value if isinstance(value, dict) else {}


def _bypass_opportunities(route_evaluation: dict) -> list[str]:
    routes = route_evaluation.get("routes")
    if not isinstance(routes, dict):
        return []
    return sorted(
        route_name
        for route_name in ("external_apply", "provider_search")
        if isinstance(routes.get(route_name), dict)
        and _integer(routes[route_name].get("candidate_count")) > 0
    )


def _relationship_verification_gap(route_evaluation: dict) -> dict | None:
    routes = route_evaluation.get("routes")
    if not isinstance(routes, dict):
        return None
    for route_name in ("external_apply", "provider_search", "website_career"):
        route = routes.get(route_name)
        if not isinstance(route, dict):
            continue
        candidate_count = _integer(route.get("candidate_count"))
        provider_count = _integer(route.get("provider_verified_count"))
        relationship_count = _integer(route.get("relationship_verified_count"))
        if candidate_count > 0 and provider_count > 0 and relationship_count == 0:
            return {
                "route": route_name,
                "candidate_count": candidate_count,
                "provider_verified_count": provider_count,
                "relationship_verified_count": relationship_count,
            }
    return None


def _candidate_route_starvation(record: dict) -> dict | None:
    career_search = _stage_trace(record, "career_discovery").get("search_discovery")
    if isinstance(career_search, dict):
        queries = career_search.get("queries")
        if (
            career_search.get("stopped_reason") == "deadline_exhausted"
            and isinstance(queries, list)
            and not queries
        ):
            return {
                "trigger": "career_search_deadline_before_execution",
                "source": "career_search_discovery",
                "stopped_reason": "deadline_exhausted",
                "fetch_budget_unavailable": (
                    career_search.get("fetch_budget_unavailable") is True
                ),
                "evidence_paths": [
                    "trace.stages.career_discovery.search_discovery.queries",
                    "trace.stages.career_discovery.search_discovery.stopped_reason",
                ],
            }
    for source_name, search in _search_traces(record):
        queries = search.get("queries")
        stopped = str(search.get("stopped_reason") or "")
        unavailable = search.get("fetch_budget_unavailable") is True
        if (
            stopped == "deadline_exhausted"
            and isinstance(queries, list)
            and not queries
        ):
            return {
                "trigger": f"{source_name}_deadline_before_execution",
                "source": source_name,
                "stopped_reason": stopped,
                "fetch_budget_unavailable": unavailable,
                "evidence_paths": [
                    "trace.stages.job_board_discovery.candidate_discovery.sources[].trace.search.queries",
                    "trace.stages.job_board_discovery.candidate_discovery.sources[].trace.search.stopped_reason",
                ],
            }
    return None


def _opening_inventory_mechanism(opening: dict) -> dict:
    provider_api = opening.get("provider_api")
    inventory = provider_api.get("inventory") if isinstance(provider_api, dict) else None
    if (
        isinstance(inventory, dict)
        and inventory.get("source") == "js_declared_inventory"
        and inventory.get("complete") is False
    ):
        return {
            "trigger": "js_declared_inventory_incomplete",
            "code_path": "opening_match.js_declared_inventory",
            "evidence": {
                "candidate_count": _integer(inventory.get("candidate_count")),
                "inventory_scope": inventory.get("scope"),
            },
            "evidence_paths": [
                "trace.stages.opening_match.provider_api.inventory"
            ],
            "confidence": "high",
        }

    actions = [
        action
        for action in opening.get("job_search_actions", [])
        if isinstance(action, dict)
    ]
    eligible_get = [
        action
        for action in actions
        if action.get("method") == "get"
        and action.get("disposition") == "eligible"
        and action.get("query_field")
    ]
    generic = [
        inventory
        for inventory in opening.get("generic_inventory", [])
        if isinstance(inventory, dict)
    ]
    stop_reasons = sorted(
        {str(item.get("stop_reason") or "unknown") for item in generic}
    )
    strongest_candidate_count = max(
        [_integer(item.get("candidate_count")) for item in generic] or [0]
    )
    stop_signature = "+".join(stop_reasons) or "unknown"
    if eligible_get:
        return {
            "trigger": f"declared_get_inventory_{stop_signature}",
            "code_path": "opening_match.execute_declared_get_search",
            "evidence": {
                "query_fields": sorted(
                    {str(action.get("query_field")) for action in eligible_get}
                ),
                "stop_reasons": stop_reasons,
                "candidate_count": strongest_candidate_count,
            },
            "evidence_paths": [
                "trace.stages.opening_match.job_search_actions",
                "trace.stages.opening_match.generic_inventory",
            ],
            "confidence": "high",
        }
    if strongest_candidate_count > 0:
        return {
            "trigger": f"generic_partial_inventory_{stop_signature}",
            "code_path": "opening_match.generic_inventory_completeness",
            "evidence": {
                "stop_reasons": stop_reasons,
                "candidate_count": strongest_candidate_count,
            },
            "evidence_paths": [
                "trace.stages.opening_match.generic_inventory"
            ],
            "confidence": "high",
        }
    ambiguous = sorted(
        {
            f"{action.get('method') or 'unknown'}:{action.get('disposition')}"
            for action in actions
            if str(action.get("disposition") or "").startswith("interactive_")
        }
    )
    if ambiguous:
        return {
            "trigger": "interactive_search_contract_" + "+".join(ambiguous),
            "code_path": "opening_match.declared_form_contract",
            "evidence": {
                "action_contracts": ambiguous,
                "stop_reasons": stop_reasons,
            },
            "evidence_paths": [
                "trace.stages.opening_match.job_search_actions"
            ],
            "confidence": "high",
        }
    js_inventory = [
        item
        for item in opening.get("js_declared_inventory", [])
        if isinstance(item, dict)
    ]
    if (
        generic
        and all(item.get("stop_reason") == "single_page_unbounded" for item in generic)
        and js_inventory
        and all(item.get("status") == "transport_not_declared" for item in js_inventory)
    ):
        integration_origin = _url_hostname(opening.get("job_list_url"))
        return {
            "category": "unclassified",
            "trigger": (
                "inventory_integration_unidentified:"
                + (integration_origin or "unknown_origin")
            ),
            "code_path": "opening_match.inventory_integration_identification",
            "evidence": {
                "integration_origin": integration_origin,
                "stop_reasons": stop_reasons,
                "js_statuses": sorted(
                    {str(item.get("status")) for item in js_inventory}
                ),
            },
            "evidence_paths": [
                "trace.stages.opening_match.generic_inventory",
                "trace.stages.opening_match.js_declared_inventory",
                "trace.stages.opening_match.job_list_url",
            ],
            "confidence": "insufficient",
        }
    return {
        "category": "unclassified",
        "trigger": f"opening_inventory_incomplete_{stop_signature}",
        "code_path": "opening_match.inventory_discovery",
        "evidence": {
            "stop_reasons": stop_reasons,
            "candidate_count": strongest_candidate_count,
        },
        "evidence_paths": ["trace.stages.opening_match"],
        "confidence": "insufficient",
    }


def _candidate_generation_gap(record: dict) -> dict | None:
    if record.get("job_list_page_url"):
        return None
    for source_name, search in _search_traces(record):
        queries = search.get("queries")
        candidates = search.get("candidates")
        if not isinstance(queries, list) or not queries:
            continue
        if not isinstance(candidates, list) or candidates:
            continue
        if _search_entirely_rejected(search):
            continue
        result_count = sum(
            _integer(query.get("result_count"))
            for query in queries
            if isinstance(query, dict)
        )
        trigger = (
            "search_results_filtered_to_zero"
            if result_count > 0
            else "executed_search_produced_no_results"
        )
        return {
            "trigger": trigger,
            "source": source_name,
            "query_count": len(queries),
            "raw_result_count": result_count,
            "candidate_count": 0,
            "stopped_reason": search.get("stopped_reason"),
            "evidence_paths": [
                "trace.stages.job_board_discovery.candidate_discovery.sources[].trace.search.queries",
                "trace.stages.job_board_discovery.candidate_discovery.sources[].trace.search.candidates",
            ],
        }
    return None


def _search_traces(record: dict) -> list[tuple[str, dict]]:
    discovery = _candidate_discovery(record)
    sources = discovery.get("sources")
    found: list[tuple[str, dict]] = []
    if not isinstance(sources, list):
        return found
    for source in sources:
        if not isinstance(source, dict):
            continue
        trace = source.get("trace")
        search = trace.get("search") if isinstance(trace, dict) else None
        if isinstance(search, dict):
            found.append((str(source.get("source") or "candidate_source"), search))
    return found


def _search_entirely_rejected(search: dict) -> bool:
    queries = search.get("queries")
    if not isinstance(queries, list) or not queries:
        return False
    observed = [query for query in queries if isinstance(query, dict)]
    if not observed:
        return False
    for query in observed:
        if _integer(query.get("result_count")) > 0:
            return False
        disposition = str(query.get("response_disposition") or "").casefold()
        if not query.get("error") and disposition not in {
            "challenge",
            "forbidden",
            "blocked",
            "fetch_failed",
        }:
            return False
    return True


def _step(record: dict, name: str) -> dict:
    trace = record.get("trace")
    steps = trace.get("steps") if isinstance(trace, dict) else None
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and step.get("name") == name:
                return step
    return {}


def _candidate_coordinator(record: dict) -> dict:
    step = _stage_trace(record, "job_board_discovery")
    coordinator = step.get("candidate_coordinator")
    if isinstance(coordinator, dict):
        return coordinator
    if step.get("method") == "candidate_discovery_coordinator_v2":
        return step
    return {}


def _coordinator_rejections(record: dict) -> list[dict]:
    stage = _stage_trace(record, "job_board_discovery")
    direct = stage.get("candidate_verification")
    coordinator = _candidate_coordinator(record)
    verification = direct if isinstance(direct, dict) else coordinator.get("candidate_verification")
    rejected: list[dict] = []
    if isinstance(verification, dict):
        for value in verification.values():
            if isinstance(value, dict) and isinstance(value.get("rejected_candidates"), list):
                rejected.extend(
                    item for item in value["rejected_candidates"] if isinstance(item, dict)
                )
    return rejected


def _rejected_sources(record: dict) -> list[str]:
    rejected: set[str] = set()
    for source_name, search in _search_traces(record):
        if _search_entirely_rejected(search):
            rejected.add(f"{source_name}:all_sources_rejected")

    coordinator = _candidate_coordinator(record)
    routes = coordinator.get("routes")
    if not isinstance(routes, dict):
        return sorted(rejected)
    for route_name in ("external_apply", "provider_search"):
        route = routes.get(route_name)
        if not isinstance(route, dict):
            continue
        failure = route.get("failure")
        reason = failure.get("reason_code") if isinstance(failure, dict) else None
        diagnostics = route.get("diagnostics")
        query_errors = _integer(diagnostics.get("query_error_count")) if isinstance(diagnostics, dict) else 0
        raw_results = _integer(diagnostics.get("raw_result_count")) if isinstance(diagnostics, dict) else 0
        query_count = _integer(route.get("query_count"))
        route_failed = route.get("status") == "failed"
        all_queries_failed = (
            query_count > 0
            and query_errors >= query_count
            and raw_results == 0
            and _integer(route.get("candidate_count")) == 0
        )
        if (
            reason in _TRANSPORT_REASONS
            or reason in _EXTERNAL_BLOCKED
            or route_failed
            or all_queries_failed
        ):
            rejected.add(
                f"{route_name}:{reason or 'search_query_error'}"
            )
    return sorted(rejected)


def _route_summary(coordinator: dict) -> dict:
    routes = coordinator.get("routes")
    if not isinstance(routes, dict):
        return {}
    return {
        name: {
            "status": route.get("status"),
            "candidate_count": int(route.get("candidate_count") or 0),
            "reason_code": (
                route.get("failure", {}).get("reason_code")
                if isinstance(route.get("failure"), dict)
                else None
            ),
        }
        for name, route in sorted(routes.items())
        if isinstance(route, dict)
    }


def _retryable_transport_events(record: dict) -> list[dict]:
    trace = record.get("trace")
    events = trace.get("retry_events") if isinstance(trace, dict) else None
    if not isinstance(events, list):
        return []
    return [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("retryable") is True
        and str(event.get("reason_code") or "") in (_TRANSPORT_REASONS | _BUDGET_REASONS)
    ]


def _transport_event_matches(events: list[dict], url: str) -> bool:
    normalized = url.rstrip("/")
    return any(str(event.get("url") or "").rstrip("/") == normalized for event in events)


def _matching_transport_phase(events: list[dict], url: str) -> str | None:
    normalized = url.rstrip("/")
    for event in events:
        if str(event.get("url") or "").rstrip("/") == normalized:
            return str(event.get("transport_phase") or "") or None
    return None


def _first_transport_phase(events: list[dict]) -> str | None:
    for event in events:
        phase = str(event.get("transport_phase") or "")
        if phase:
            return phase
    return None


def _transport_budget_exhausted(record: dict) -> bool:
    for name in ("find_career_page", "find_job_board", "match_opening"):
        budget = _step(record, name).get("transport_budget")
        if isinstance(budget, dict) and budget.get("exhausted") is True:
            return True
    return False


def _cohort_identities(records: list[dict]) -> dict[str, dict]:
    if not isinstance(records, list):
        raise ValueError("cohort must be a JSON list")
    identities: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("cohort record must be a mapping")
        job_id = linkedin_job_id(
            record.get("linkedin_job_url") or record.get("job_url")
        )
        if job_id in identities:
            raise ValueError(f"cohort contains duplicate LinkedIn job id {job_id}")
        identities[job_id] = record
    return identities


def _validate_accepted_terminals(value: dict[str, dict]) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("accepted terminal manifest must be a mapping")
    accepted: dict[str, dict] = {}
    for job_id, item in value.items():
        if not isinstance(job_id, str) or not job_id.isdigit():
            raise ValueError(f"invalid accepted terminal job id: {job_id!r}")
        if not isinstance(item, dict):
            raise ValueError(f"accepted terminal {job_id} must be a mapping")
        outcome = item.get("durable_outcome")
        if outcome not in {"exact", "verified_no_match", "external_blocked"}:
            raise ValueError(f"invalid accepted terminal outcome for {job_id}: {outcome!r}")
        evidence_ref = item.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError(f"accepted terminal {job_id} requires evidence_ref")
        accepted[job_id] = {
            "durable_outcome": outcome,
            "evidence_ref": evidence_ref.strip(),
        }
    return accepted


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _qualification_blockers(
    signature: str,
    category: str,
    company_count: int,
    minimum_company_count: int,
    reviewed_clusters: set[str],
) -> list[str]:
    blockers = []
    if company_count < minimum_company_count:
        blockers.append("minimum_independent_company_count_not_met")
    if category == "unclassified":
        blockers.append("causal_evidence_insufficient")
    if signature not in reviewed_clusters:
        blockers.extend(
            [
                "current_version_reproduction_not_reviewed",
                "batch_recovery_expectation_not_reviewed",
            ]
        )
    return blockers


def _failure_stage(record: dict) -> str | None:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    for stage in stages:
        if isinstance(stage, dict) and stage.get("status") in {"failed", "partial", "unsupported"}:
            return str(stage.get("stage") or "") or None
    return None


def _availability_diagnostic(record: dict) -> str | None:
    value = _step(record, "match_opening").get("availability_diagnostic")
    if isinstance(value, dict):
        value = value.get("disposition") or value.get("status")
    return str(value) if value else None


def _provider(record: dict) -> str | None:
    identity = record.get("identity_assertion")
    provider = identity.get("provider") if isinstance(identity, dict) else None
    value = provider.get("provider") if isinstance(provider, dict) else None
    return str(value) if value else None


def _url_hostname(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return (urlparse(value).hostname or "").casefold() or None
    except ValueError:
        return None


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
