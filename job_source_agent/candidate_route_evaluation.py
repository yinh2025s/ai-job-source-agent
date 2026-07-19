from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .result_identity import canonicalize_identity_url, identity_urls_equivalent


CANDIDATE_ROUTE_EVALUATION_SCHEMA_VERSION = "1.0"
CANDIDATE_ROUTES = (
    "external_apply",
    "provider_search",
    "website_career",
)

_ROUTE_BITS = {route: 1 << index for index, route in enumerate(CANDIDATE_ROUTES)}


def evaluate_candidate_routes(
    result: Mapping[str, Any],
    trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate independently attributable outcomes for the three candidate routes.

    A route receives exact credit only when its own trace contains a verified
    provider/tenant/board and relationship, and that identity agrees with the
    typed selected opening identity. Search snippets and a bare final exact URL
    are deliberately insufficient.
    """
    if not isinstance(result, Mapping):
        raise TypeError("candidate route result must be a mapping")
    effective_trace = trace if trace is not None else result.get("trace")
    if not isinstance(effective_trace, Mapping):
        effective_trace = {}
        trace_error = "trace_not_mapping"
    else:
        trace_error = None

    stage_trace = _job_board_stage_trace(effective_trace)
    route_trace = stage_trace.get("route_evaluation")
    legacy = not isinstance(route_trace, Mapping)
    malformed_reasons: list[str] = []
    route_metrics: dict[str, dict[str, Any]] = {}

    if trace_error is not None or not stage_trace:
        malformed_reasons.append(trace_error or "job_board_trace_missing")
        for route in CANDIDATE_ROUTES:
            route_metrics[route] = _failed_route("malformed_trace", malformed=True)
    elif legacy:
        route_metrics = _evaluate_legacy_routes(result, stage_trace)
    else:
        raw_routes = route_trace.get("routes", route_trace)
        if not isinstance(raw_routes, Mapping):
            malformed_reasons.append("route_evaluation_routes_not_mapping")
            raw_routes = {}
        for route in CANDIDATE_ROUTES:
            raw = raw_routes.get(route)
            if not isinstance(raw, Mapping):
                malformed_reasons.append(f"{route}:route_trace_missing_or_invalid")
                route_metrics[route] = _failed_route(
                    "malformed_trace", malformed=True
                )
                continue
            metrics, errors = _evaluate_modern_route(
                route,
                raw,
                result,
                legacy_selected=bool(stage_trace.get("candidate_route_probe")),
            )
            route_metrics[route] = metrics
            malformed_reasons.extend(f"{route}:{error}" for error in errors)

    exact_bitmask = sum(
        _ROUTE_BITS[route]
        for route, metrics in route_metrics.items()
        if metrics["exact_attributable"]
    )
    return {
        "schema_version": CANDIDATE_ROUTE_EVALUATION_SCHEMA_VERSION,
        "record_id": _record_id(result),
        "trace_mode": (
            "malformed"
            if trace_error is not None or not stage_trace
            else "legacy_website"
            if legacy
            else "three_route"
        ),
        "malformed_trace": bool(malformed_reasons),
        "malformed_reasons": sorted(set(malformed_reasons)),
        "routes": route_metrics,
        "union_exact": exact_bitmask != 0,
        "exact_bitmask": exact_bitmask,
        "exact_bitmask_binary": format(exact_bitmask, "03b"),
    }


def summarize_candidate_route_metrics(
    records: Iterable[Any],
) -> dict[str, Any]:
    """Aggregate result/trace pairs or already-evaluated route records."""
    evaluated = [_coerce_evaluated_record(record) for record in records]
    total = len(evaluated)
    route_summary: dict[str, dict[str, Any]] = {}

    for route in CANDIDATE_ROUTES:
        values = [record["routes"][route] for record in evaluated]
        input_count = sum(item["input_coverage"] for item in values)
        candidate_count = sum(item["candidate_produced"] for item in values)
        board_count = sum(item["provider_tenant_board_verified"] for item in values)
        relationship_count = sum(item["relationship_verified"] for item in values)
        attributable_count = sum(item["exact_attributable"] for item in values)
        route_summary[route] = {
            "input_coverage": _metric(input_count, total),
            "candidate_produced": _metric(candidate_count, input_count),
            "provider_tenant_board_verified": _metric(board_count, candidate_count),
            "relationship_verified": _metric(relationship_count, board_count),
            "exact_attributable": _metric(attributable_count, relationship_count),
            "exact_attributable_overall": _metric(attributable_count, total),
            "reason_counts": dict(sorted(Counter(item["reason"] for item in values).items())),
            "malformed_trace_count": sum(item["malformed_trace"] for item in values),
        }

    bitmask_counts = Counter(record["exact_bitmask"] for record in evaluated)
    overlaps = []
    for bitmask in range(8):
        routes = [
            route for route in CANDIDATE_ROUTES if bitmask & _ROUTE_BITS[route]
        ]
        overlaps.append(
            {
                "bitmask": bitmask,
                "binary": format(bitmask, "03b"),
                "routes": routes,
                "count": bitmask_counts[bitmask],
                "rate": _rate(bitmask_counts[bitmask], total),
                "denominator": total,
            }
        )

    union_count = sum(record["union_exact"] for record in evaluated)
    malformed_count = sum(record["malformed_trace"] for record in evaluated)
    return {
        "schema_version": CANDIDATE_ROUTE_EVALUATION_SCHEMA_VERSION,
        "record_count": total,
        "routes": route_summary,
        "union_exact": _metric(union_count, total),
        "malformed_trace": _metric(malformed_count, total),
        "overlaps": overlaps,
        "overlap_bitmask_counts": {
            format(bitmask, "03b"): bitmask_counts[bitmask]
            for bitmask in range(8)
        },
        "records": evaluated,
    }


# Descriptive aliases make the small public surface easy to discover.
evaluate_candidate_route_record = evaluate_candidate_routes
aggregate_candidate_route_metrics = summarize_candidate_route_metrics


def _evaluate_modern_route(
    route: str,
    raw: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    legacy_selected: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    input_coverage = _strict_bool_alias(
        raw, ("input_coverage", "input_available"), errors
    )
    candidate_produced = _strict_bool_or_count(
        raw, "candidate_produced", ("candidate_count",), errors
    )
    board_verified = _strict_bool_or_count(
        raw,
        "provider_tenant_board_verified",
        ("provider_tenant_board_verified_count", "provider_verified_count"),
        errors,
    )
    relationship_verified = _strict_bool_or_count(
        raw,
        "relationship_verified",
        ("relationship_verified_count",),
        errors,
    )
    boards = _verified_boards(raw, errors)

    if (
        route == "website_career"
        and raw.get("legacy_status") == "success"
        and legacy_selected
        and not boards
    ):
        identity = result.get("identity_assertion")
        provider = identity.get("provider") if isinstance(identity, Mapping) else None
        if (
            isinstance(provider, Mapping)
            and provider.get("relationship_verified") is True
            and all(
                isinstance(provider.get(field), str) and provider.get(field)
                for field in ("provider", "tenant", "canonical_board_url")
            )
            and _same_url(
                result.get("job_list_page_url"),
                provider.get("canonical_board_url"),
            )
        ):
            boards = [
                {
                    "provider": provider["provider"],
                    "tenant": provider["tenant"],
                    "canonical_board_url": provider["canonical_board_url"],
                }
            ]
            candidate_produced = True
            board_verified = True
            relationship_verified = True

    if (
        route == "website_career"
        and raw.get("legacy_status") == "success"
        and board_verified
    ):
        candidate_produced = True

    if relationship_verified and not boards:
        errors.append("verified_board_identity_missing")
    if candidate_produced and not input_coverage:
        errors.append("candidate_without_input_coverage")
    if board_verified and not candidate_produced:
        errors.append("board_without_candidate")
    if relationship_verified and not board_verified:
        errors.append("relationship_without_board")

    malformed = bool(errors)
    if malformed:
        return _failed_route("malformed_trace", malformed=True), errors

    target = _typed_exact_target(result, require_selection=True)
    board_can_produce_selected = bool(
        relationship_verified
        and target is not None
        and any(_board_matches(board, target) for board in boards)
    )
    exact_attributable = bool(board_can_produce_selected and target is not None)
    reason = _route_reason(
        input_coverage,
        candidate_produced,
        board_verified,
        relationship_verified,
        board_can_produce_selected,
        exact_attributable,
        bool(result.get("open_position_url")),
    )
    return {
        "input_coverage": input_coverage,
        "candidate_produced": candidate_produced,
        "provider_tenant_board_verified": board_verified,
        "relationship_verified": relationship_verified,
        "board_can_produce_selected": board_can_produce_selected,
        "exact_attributable": exact_attributable,
        "reason": reason,
        "malformed_trace": False,
        "verified_board_count": len(boards),
    }, []


def _evaluate_legacy_routes(
    result: Mapping[str, Any],
    stage_trace: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    routes = {
        "external_apply": _failed_route("legacy_route_not_evaluated"),
        "provider_search": _failed_route("legacy_route_not_evaluated"),
        "website_career": _failed_route("input_not_covered"),
    }
    website_input = bool(result.get("career_page_url") or result.get("company_website_url"))
    if not website_input:
        return routes

    method = stage_trace.get("method")
    board_url = stage_trace.get("job_list_page_url")
    provider_detection = stage_trace.get("provider_detection")
    candidate_produced = bool(
        method
        and method not in {"parallel_candidate_discovery", "external_apply_url"}
        and isinstance(board_url, str)
        and board_url
    )
    identity = result.get("identity_assertion")
    provider = identity.get("provider") if isinstance(identity, Mapping) else None
    board_verified = bool(
        candidate_produced
        and isinstance(provider, Mapping)
        and _same_url(provider.get("canonical_board_url"), board_url)
        and isinstance(provider.get("provider"), str)
        and isinstance(provider.get("tenant"), str)
        and (
            not isinstance(provider_detection, Mapping)
            or provider_detection.get("provider") == provider.get("provider")
        )
    )
    relationship_verified = bool(
        board_verified and provider.get("relationship_verified") is True
    )
    target = _typed_exact_target(result, require_selection=False)
    board = provider if isinstance(provider, Mapping) else {}
    can_produce = bool(
        relationship_verified
        and target is not None
        and _board_matches(board, target)
    )
    exact = bool(can_produce and target is not None)
    routes["website_career"] = {
        "input_coverage": True,
        "candidate_produced": candidate_produced,
        "provider_tenant_board_verified": board_verified,
        "relationship_verified": relationship_verified,
        "board_can_produce_selected": can_produce,
        "exact_attributable": exact,
        "reason": _route_reason(
            True,
            candidate_produced,
            board_verified,
            relationship_verified,
            can_produce,
            exact,
            bool(result.get("open_position_url")),
        ),
        "malformed_trace": False,
        "verified_board_count": int(board_verified),
    }
    return routes


def _typed_exact_target(
    result: Mapping[str, Any], *, require_selection: bool
) -> dict[str, str] | None:
    identity = result.get("identity_assertion")
    if not isinstance(identity, Mapping) or identity.get("verdict") != "verified":
        return None
    provider = identity.get("provider")
    opening = identity.get("opening")
    selection = identity.get("selection")
    if not isinstance(provider, Mapping) or not isinstance(opening, Mapping):
        return None
    if require_selection and not isinstance(selection, Mapping):
        return None

    values = {
        "provider": provider.get("provider"),
        "tenant": provider.get("tenant"),
        "canonical_board_url": provider.get("canonical_board_url"),
        "canonical_opening_url": opening.get("canonical_opening_url"),
    }
    if not all(isinstance(value, str) and value for value in values.values()):
        return None
    if provider.get("relationship_verified") is not True:
        return None
    if not _identity_components_match(provider, opening, include_opening=False):
        return None
    if isinstance(selection, Mapping):
        if not _identity_components_match(provider, selection, include_opening=False):
            return None
        selected_opening = selection.get("canonical_opening_url")
        if not _same_url(selected_opening, values["canonical_opening_url"]):
            return None
    if not _same_url(result.get("job_list_page_url"), values["canonical_board_url"]):
        return None
    if not _same_url(result.get("open_position_url"), values["canonical_opening_url"]):
        return None
    return values  # type: ignore[return-value]


def _identity_components_match(
    expected: Mapping[str, Any], actual: Mapping[str, Any], *, include_opening: bool
) -> bool:
    if expected.get("provider") != actual.get("provider"):
        return False
    if expected.get("tenant") != actual.get("tenant"):
        return False
    if not _same_url(
        expected.get("canonical_board_url"), actual.get("canonical_board_url")
    ):
        return False
    return not include_opening or _same_url(
        expected.get("canonical_opening_url"), actual.get("canonical_opening_url")
    )


def _verified_boards(
    raw: Mapping[str, Any], errors: list[str]
) -> list[dict[str, str]]:
    values = raw.get("verified_boards")
    if values is None:
        values = raw.get("verified_relationship_boards")
    if values is None:
        singular = {
            "provider": raw.get("provider"),
            "tenant": raw.get("tenant"),
            "canonical_board_url": raw.get("canonical_board_url"),
        }
        values = [singular] if any(value is not None for value in singular.values()) else []
    if not isinstance(values, list):
        errors.append("verified_boards_not_list")
        return []
    boards: list[dict[str, str]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            errors.append(f"verified_boards[{index}]_not_mapping")
            continue
        board = {
            "provider": value.get("provider"),
            "tenant": value.get("tenant"),
            "canonical_board_url": value.get("canonical_board_url")
            or value.get("url"),
        }
        if not all(isinstance(item, str) and item for item in board.values()):
            errors.append(f"verified_boards[{index}]_identity_invalid")
            continue
        boards.append(board)  # type: ignore[arg-type]
    return boards


def _strict_bool(raw: Mapping[str, Any], field: str, errors: list[str]) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        errors.append(f"{field}_not_boolean")
        return False
    return value


def _strict_bool_alias(
    raw: Mapping[str, Any], fields: tuple[str, ...], errors: list[str]
) -> bool:
    for field in fields:
        if field in raw:
            return _strict_bool(raw, field, errors)
    errors.append(f"{'_or_'.join(fields)}_missing")
    return False


def _strict_bool_or_count(
    raw: Mapping[str, Any],
    bool_field: str,
    count_fields: tuple[str, ...],
    errors: list[str],
) -> bool:
    if bool_field in raw:
        return _strict_bool(raw, bool_field, errors)
    for count_field in count_fields:
        if count_field not in raw:
            continue
        value = raw[count_field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"{count_field}_invalid")
            return False
        return value > 0
    errors.append(f"{bool_field}_and_{'_or_'.join(count_fields)}_missing")
    return False


def _board_matches(board: Mapping[str, Any], target: Mapping[str, str]) -> bool:
    if board.get("provider") != target["provider"]:
        return False
    if (
        board.get("tenant") == target["tenant"]
        and _same_url(
            board.get("canonical_board_url"), target["canonical_board_url"]
        )
    ):
        return True
    return _generic_board_lineage_matches(board, target)


def _generic_board_lineage_matches(
    board: Mapping[str, Any], target: Mapping[str, str]
) -> bool:
    """Allow a generic first-party board to refine its URL for selection."""
    if board.get("provider") != "generic" or target["provider"] != "generic":
        return False
    board_url = board.get("canonical_board_url")
    target_url = target["canonical_board_url"]
    board_tenant = board.get("tenant")
    target_tenant = target["tenant"]
    if not all(
        isinstance(value, str) and value
        for value in (board_url, target_url, board_tenant, target_tenant)
    ):
        return False
    if not board_tenant.startswith("url:") or not target_tenant.startswith("url:"):
        return False
    if not _same_url(board_tenant[4:], board_url):
        return False
    if not _same_url(target_tenant[4:], target_url):
        return False
    try:
        board_parts = urlsplit(canonicalize_identity_url(board_url))
        target_parts = urlsplit(canonicalize_identity_url(target_url))
    except ValueError:
        return False
    if (
        board_parts.scheme,
        board_parts.hostname,
        board_parts.port,
    ) != (
        target_parts.scheme,
        target_parts.hostname,
        target_parts.port,
    ):
        return False
    board_path = board_parts.path.rstrip("/")
    target_path = target_parts.path.rstrip("/")
    return bool(
        board_path == target_path
        or not board_path
        or target_path.startswith(f"{board_path}/")
    )


def _same_url(left: Any, right: Any) -> bool:
    return bool(
        isinstance(left, str)
        and isinstance(right, str)
        and left
        and right
        and identity_urls_equivalent(left, right)
    )


def _route_reason(
    input_coverage: bool,
    candidate_produced: bool,
    board_verified: bool,
    relationship_verified: bool,
    board_can_produce_selected: bool,
    exact_attributable: bool,
    has_final_opening: bool,
) -> str:
    if not input_coverage:
        return "input_not_covered"
    if not candidate_produced:
        return "candidate_not_produced"
    if not board_verified:
        return "provider_tenant_board_not_verified"
    if not relationship_verified:
        return "relationship_not_verified"
    if not has_final_opening:
        return "final_exact_not_available"
    if not board_can_produce_selected:
        return "selected_exact_not_attributable"
    return "exact_attributable" if exact_attributable else "final_exact_not_verified"


def _failed_route(reason: str, *, malformed: bool = False) -> dict[str, Any]:
    return {
        "input_coverage": False,
        "candidate_produced": False,
        "provider_tenant_board_verified": False,
        "relationship_verified": False,
        "board_can_produce_selected": False,
        "exact_attributable": False,
        "reason": reason,
        "malformed_trace": malformed,
        "verified_board_count": 0,
    }


def _job_board_stage_trace(trace: Mapping[str, Any]) -> Mapping[str, Any]:
    stages = trace.get("stages")
    if isinstance(stages, Mapping):
        stage = stages.get("job_board_discovery")
        if isinstance(stage, Mapping):
            return stage
    if "route_evaluation" in trace or "method" in trace:
        return trace
    steps = trace.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, Mapping) and step.get("name") == "find_job_board":
                return step
    return {}


def _record_id(result: Mapping[str, Any]) -> str | None:
    for field in ("record_id", "execution_fingerprint", "company_name"):
        value = result.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "count": numerator,
        "denominator": denominator,
        "rate": _rate(numerator, denominator),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _coerce_evaluated_record(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping) and _is_evaluated_record(record):
        return dict(record)
    if isinstance(record, (tuple, list)) and len(record) == 2:
        result, trace = record
        return evaluate_candidate_routes(result, trace)
    if isinstance(record, Mapping) and isinstance(record.get("result"), Mapping):
        return evaluate_candidate_routes(record["result"], record.get("trace"))
    if isinstance(record, Mapping):
        return evaluate_candidate_routes(record)
    raise TypeError("candidate route aggregate record has an unsupported shape")


def _is_evaluated_record(record: Mapping[str, Any]) -> bool:
    routes = record.get("routes")
    return bool(
        record.get("schema_version") == CANDIDATE_ROUTE_EVALUATION_SCHEMA_VERSION
        and isinstance(routes, Mapping)
        and all(route in routes for route in CANDIDATE_ROUTES)
        and isinstance(record.get("exact_bitmask"), int)
    )
