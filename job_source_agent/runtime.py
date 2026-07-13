from __future__ import annotations

from dataclasses import dataclass
import platform
import sys
from typing import Sequence


MIN_SUPPORTED = (3, 10)
MAX_EXCLUSIVE = (3, 14)
RELEASE_VERSION = (3, 12)


@dataclass(frozen=True)
class RuntimeStatus:
    implementation: str
    version: tuple[int, int, int]
    supported: bool
    release_compatible: bool
    detail: str


def inspect_runtime(
    version_info: Sequence[int] | None = None,
    implementation: str | None = None,
) -> RuntimeStatus:
    raw_version = version_info or sys.version_info
    version = tuple(int(part) for part in raw_version[:3])
    runtime = implementation or platform.python_implementation()
    major_minor = version[:2]
    supported = runtime == "CPython" and MIN_SUPPORTED <= major_minor < MAX_EXCLUSIVE
    release_compatible = supported and major_minor == RELEASE_VERSION
    if runtime != "CPython":
        detail = f"Unsupported Python implementation: {runtime}; CPython is required."
    elif not supported:
        detail = "Supported Python range is >=3.10,<3.14."
    elif not release_compatible:
        detail = "Runtime is supported, but release gates are pinned to CPython 3.12."
    else:
        detail = "Runtime matches the CPython 3.12 release baseline."
    return RuntimeStatus(runtime, version, supported, release_compatible, detail)
