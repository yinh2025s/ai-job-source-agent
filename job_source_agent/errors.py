from __future__ import annotations


class DiscoveryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        step_name: str = "discovery",
        trace: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.step_name = step_name
        self.trace = trace or {}

