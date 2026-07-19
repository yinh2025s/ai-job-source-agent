from __future__ import annotations

from collections.abc import Mapping

from .browser_interaction import BrowserInteraction
from .evidence_scope import EvidenceScopeRef
from .models import PIPELINE_STAGES
from .outcome_tape import (
    OFFLINE_TAPE_DIVERGENCE,
    OutcomeTape,
    OutcomeTapeFetcher,
)
from .web import FetchError, Page


class ScopedReplayController:
    """Expose one strict outcome tape at a time across canonical stage boundaries."""

    timeout = None

    def __init__(
        self,
        stage_tapes: Mapping[str, OutcomeTape],
        *,
        execution_fingerprint: str,
    ) -> None:
        self._execution_fingerprint = execution_fingerprint
        self._stage_tapes = dict(stage_tapes)
        if not self._stage_tapes:
            raise ValueError("Scoped replay requires at least one stage tape")
        for stage, tape in self._stage_tapes.items():
            if stage not in PIPELINE_STAGES or tape.scope.stage != stage:
                raise ValueError("Scoped replay tape stage does not match its plan key")
            if tape.scope.execution_fingerprint != execution_fingerprint:
                raise ValueError("Scoped replay tape execution fingerprint does not match")
        self._active_stage: str | None = None
        self._active_fetcher: OutcomeTapeFetcher | None = None
        self._completed_stages: set[str] = set()

    @property
    def supports_forced_render(self) -> bool:
        return bool(
            self._active_fetcher is not None
            and self._active_fetcher.supports_forced_render
        )

    def begin_stage(
        self,
        attempt_id: str,
        execution_fingerprint: str,
        stage: str,
    ) -> str:
        del attempt_id
        if self._active_stage is not None:
            raise _divergence("A scoped replay stage is already active")
        if execution_fingerprint != self._execution_fingerprint:
            raise _divergence("Scoped replay execution fingerprint changed")
        tape = self._stage_tapes.get(stage)
        if tape is None:
            raise _divergence(f"Scoped replay has no outcome tape for stage {stage}")
        if stage in self._completed_stages:
            raise _divergence(f"Scoped replay stage {stage} was started more than once")
        self._active_stage = stage
        self._active_fetcher = OutcomeTapeFetcher(tape)
        return tape.scope.scope_id

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        if self._active_fetcher is None:
            raise _divergence("Scoped replay received a request outside an active stage")
        return self._active_fetcher.fetch(
            url,
            data=data,
            headers=headers,
            interaction=interaction,
        )

    def finalize(self) -> EvidenceScopeRef:
        if self._active_stage is None or self._active_fetcher is None:
            raise _divergence("Scoped replay has no active stage to finalize")
        stage = self._active_stage
        self._active_fetcher.finish()
        scope = self._stage_tapes[stage].scope
        self._completed_stages.add(stage)
        self._active_stage = None
        self._active_fetcher = None
        return scope

    def abort_stage(self) -> None:
        self._active_stage = None
        self._active_fetcher = None

    def assert_all_consumed(self) -> None:
        if self._active_stage is not None:
            raise _divergence("Scoped replay ended with an active stage")
        missing = [stage for stage in self._stage_tapes if stage not in self._completed_stages]
        if missing:
            raise _divergence(
                "Scoped replay did not execute planned stage tapes: " + ", ".join(missing)
            )

    def remaining_fetch_seconds(self) -> float | None:
        return None


def _divergence(message: str) -> FetchError:
    return FetchError(
        message,
        reason_code=OFFLINE_TAPE_DIVERGENCE,
        retryable=False,
    )
