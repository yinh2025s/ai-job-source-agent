from __future__ import annotations

from typing import Any, Literal, TypedDict

from .reasons import classify_fetch_error, reason_spec
from .request_identity import request_identity_from_dict
from .web import TRANSPORT_PHASES, FetchError


class FetchFailureProjection(TypedDict):
    """JSON-safe evidence fields projected from a fetch exception."""

    error: str
    status: int | None
    reason_code: str
    reason_code_source: Literal["exception", "classified_message"]
    retryable: bool
    request_identity: dict[str, Any] | None
    transport_phase: str | None


def project_fetch_error(error: FetchError) -> FetchFailureProjection:
    """Project a FetchError without discarding its typed transport evidence."""
    if not isinstance(error, FetchError):
        raise TypeError("error must be a FetchError")

    status = _validated_status(error.status)
    reason_code, reason_code_source = _reason_code(error)
    retryable = _retryable(error.retryable, reason_code)
    request_identity = _request_identity(error.request_identity)
    return {
        "error": str(error),
        "status": status,
        "reason_code": reason_code,
        "reason_code_source": reason_code_source,
        "retryable": retryable,
        "request_identity": request_identity,
        "transport_phase": _transport_phase(error.transport_phase),
    }


def _transport_phase(value: object) -> str | None:
    if value is None:
        return None
    if value not in TRANSPORT_PHASES:
        raise TypeError("FetchError.transport_phase is invalid")
    return str(value)


def _validated_status(status: object) -> int | None:
    if status is None:
        return None
    if type(status) is not int:
        raise TypeError("FetchError.status must be an int or None")
    return status


def _reason_code(error: FetchError) -> tuple[str, Literal["exception", "classified_message"]]:
    if error.reason_code is None:
        return classify_fetch_error(str(error)), "classified_message"
    if not isinstance(error.reason_code, str):
        raise TypeError("FetchError.reason_code must be a string or None")
    return error.reason_code, "exception"


def _retryable(retryable: object, reason_code: str) -> bool:
    if retryable is None:
        return reason_spec(reason_code).retryable
    if type(retryable) is not bool:
        raise TypeError("FetchError.retryable must be a bool or None")
    return retryable


def _request_identity(request_identity: object) -> dict[str, Any] | None:
    if request_identity is None:
        return None
    if not isinstance(request_identity, dict):
        raise TypeError("FetchError.request_identity must be a dict or None")
    return request_identity_from_dict(request_identity).as_dict()
