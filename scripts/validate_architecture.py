from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.providers import (
    ProviderAdapter,
    build_default_provider_registry,
    discover_native_adapters,
)
from job_source_agent.providers.base import (
    PageAwareProviderAdapter,
    PageProbeProviderAdapter,
)
from job_source_agent.reasons import REASON_SPECS


def validate_architecture() -> dict:
    native_adapters = discover_native_adapters()
    registry = build_default_provider_registry()
    issues: list[dict[str, str]] = []
    native_names = [adapter.name for adapter in native_adapters]
    registry_names = [adapter.name for adapter in registry.adapters]
    listing_names = [adapter.name for adapter in native_adapters if adapter.supports_listing]
    detection_only_names = [
        adapter.name for adapter in native_adapters if not adapter.supports_listing
    ]

    for name in sorted(set(native_names)):
        if native_names.count(name) > 1:
            issues.append({"provider": name, "code": "DUPLICATE_NATIVE_ADAPTER"})
    for name in sorted(set(registry_names)):
        if registry_names.count(name) > 1:
            issues.append({"provider": name, "code": "DUPLICATE_REGISTRY_ADAPTER"})

    for adapter in native_adapters:
        if not isinstance(adapter, ProviderAdapter):
            issues.append({"provider": adapter.name, "code": "PROVIDER_CONTRACT_MISMATCH"})
        if not adapter.supports_listing and not isinstance(
            adapter,
            (PageAwareProviderAdapter, PageProbeProviderAdapter),
        ):
            issues.append(
                {"provider": adapter.name, "code": "DETECTION_EVIDENCE_NOT_SUPPORTED"}
            )
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
        if selected is None or selected is not adapter:
            issues.append({"provider": adapter.name, "code": "NATIVE_ADAPTER_NOT_SELECTED"})
        for reason_code in _declared_reason_codes(adapter):
            if reason_code not in REASON_SPECS:
                issues.append(
                    {
                        "provider": adapter.name,
                        "code": "UNKNOWN_PROVIDER_REASON_CODE",
                        "detail": reason_code,
                    }
                )

    return {
        "valid": not issues,
        "native_adapter_count": len(native_adapters),
        "native_adapters": native_names,
        "listing_adapters": listing_names,
        "detection_only_adapters": detection_only_names,
        "registry_adapter_count": len(registry.adapters),
        "issues": issues,
    }


def _declared_reason_codes(adapter: ProviderAdapter) -> set[str]:
    module = sys.modules.get(adapter.__class__.__module__)
    if module is None:
        return set()
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError, SyntaxError):
        return set()
    codes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "reason_code":
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            codes.add(node.value.value)
    return codes
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
