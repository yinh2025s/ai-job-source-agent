from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.providers import (
    ProviderAdapter,
    build_default_provider_registry,
    discover_native_adapters,
)


def validate_architecture() -> dict:
    native_adapters = discover_native_adapters()
    registry = build_default_provider_registry()
    issues: list[dict[str, str]] = []
    native_names = [adapter.name for adapter in native_adapters]
    registry_names = [adapter.name for adapter in registry.adapters]

    for name in sorted(set(native_names)):
        if native_names.count(name) > 1:
            issues.append({"provider": name, "code": "DUPLICATE_NATIVE_ADAPTER"})
    for name in sorted(set(registry_names)):
        if registry_names.count(name) > 1:
            issues.append({"provider": name, "code": "DUPLICATE_REGISTRY_ADAPTER"})

    for adapter in native_adapters:
        if not isinstance(adapter, ProviderAdapter):
            issues.append({"provider": adapter.name, "code": "PROVIDER_CONTRACT_MISMATCH"})
        if not adapter.supports_listing:
            issues.append({"provider": adapter.name, "code": "NATIVE_LISTING_NOT_SUPPORTED"})
        module_name = adapter.__class__.__module__.rsplit(".", 1)[-1]
        if module_name != adapter.name:
            issues.append(
                {
                    "provider": adapter.name,
                    "code": "PROVIDER_MODULE_NAME_MISMATCH",
                    "detail": module_name,
                }
            )
        selected = next(
            (registered for registered in registry.adapters if registered.name == adapter.name),
            None,
        )
        if selected is None or selected is not adapter or not selected.supports_listing:
            issues.append({"provider": adapter.name, "code": "NATIVE_ADAPTER_NOT_SELECTED"})

    return {
        "valid": not issues,
        "native_adapter_count": len(native_adapters),
        "native_adapters": native_names,
        "registry_adapter_count": len(registry.adapters),
        "issues": issues,
    }
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate architecture extension contracts.")
    parser.add_argument("--output", help="Optional JSON report path.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = validate_architecture()
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"architecture validation: {report['native_adapter_count']} native adapters, "
        f"{len(report['issues'])} issues",
        flush=True,
    )
    if report["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
