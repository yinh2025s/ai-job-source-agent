from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from job_source_agent.composition import AgentConfig, FetcherConfig
from job_source_agent.extension_bridge import (
    ExtensionBridgeConfig,
    ExtensionBridgeServer,
    ExtensionRunManager,
    validate_loopback_host,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local browser-extension bridge.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("JOB_SOURCE_BRIDGE_TOKEN"))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--fetch-timeout", type=float, default=8)
    parser.add_argument("--fixtures-dir")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--render-js", action="store_true")
    parser.add_argument("--render-budget", type=int, default=3)
    parser.add_argument("--output-dir", default=str(Path.home() / ".ai-job-source-agent" / "runs"))
    parser.add_argument(
        "--company-discovery-evidence-store",
        help=(
            "Persistent verified company-discovery evidence path; defaults to "
            "company-discovery-evidence.json in the output directory."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    host = validate_loopback_host(args.host)
    token = args.token or secrets.token_urlsafe(24)
    manager = ExtensionRunManager(
        ExtensionBridgeConfig(
            fetcher=FetcherConfig(
                fixtures_dir=args.fixtures_dir,
                offline=args.offline,
                timeout=args.fetch_timeout,
                render_mode="smart" if args.render_js else "none",
                render_budget=args.render_budget,
            ),
            agent=AgentConfig(),
            workers=args.workers,
            output_dir=Path(args.output_dir),
            company_discovery_evidence_path=(
                Path(args.company_discovery_evidence_store)
                if args.company_discovery_evidence_store
                else None
            ),
        )
    )
    server = ExtensionBridgeServer((host, args.port), manager, token)
    print(f"bridge: http://{host}:{args.port}")
    print(f"token: {token}")
    print(f"runs: {args.output_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.close()


if __name__ == "__main__":
    main()
