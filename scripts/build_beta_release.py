#!/usr/bin/env python3.12
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.scan_artifact_privacy import scan_artifact_root


PRODUCT_VERSION = "0.1.0-beta.2"
ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".python-version",
        "CHANGELOG.md",
        "DEVELOPMENT_GOVERNANCE.md",
        "IMPLEMENTATION_PLAN.md",
        "Makefile",
        "README.md",
        "SUBMISSION.md",
        "pyproject.toml",
    }
)
SOURCE_PREFIXES = (
    ".github/workflows/",
    "deploy/searxng/",
    "extension/",
    "job_source_agent/",
    "scripts/",
    "tests/",
)
SAMPLE_FILES = frozenset(
    {
        "samples/beta_demo_input.json",
        "samples/linkedin_jobs.json",
    }
)
SAMPLE_PREFIXES = ("samples/sites/",)
DOC_FILES = frozenset(
    {
        "docs/ARCHITECTURE.md",
        "docs/BETA_DEMO_EVIDENCE.md",
        "docs/BETA_DEMO_SCRIPT.md",
        "docs/BETA_PROJECT_SUMMARY.md",
        "docs/EXTENSION_ACCEPTANCE.md",
        "docs/FRESH_100_CURRENT_CLOSURE_MATRIX.md",
        "docs/FRESH_100_V283_CURRENT_COLD_GATE_REPORT.md",
        "docs/FROZEN_100_FINAL_REPORT.md",
        "docs/LI_KAI_MESSAGE.md",
    }
)
FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "artifacts",
        "cache",
        "checkpoints",
        "completion",
        "cookies",
        "snapshots",
    }
)


class BetaReleaseError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a privacy-scanned, source-only beta review package."
    )
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--version", default=PRODUCT_VERSION)
    parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report = build_beta_release(
        repo_root=Path(args.repo_root),
        output_dir=Path(args.output_dir),
        version=args.version,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


def build_beta_release(
    *,
    repo_root: Path,
    output_dir: Path,
    version: str,
) -> dict[str, object]:
    root = repo_root.resolve()
    commit = _clean_git_commit(root)
    tracked = _tracked_files(root)
    selected = select_release_files(tracked)
    missing = sorted((ROOT_FILES | DOC_FILES) - set(selected))
    if missing:
        raise BetaReleaseError(f"required release files are not tracked: {missing}")

    destination = (root / output_dir).resolve() if not output_dir.is_absolute() else output_dir
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"ai-job-source-agent-{version}.zip"
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if archive.exists() or checksum.exists():
        raise BetaReleaseError("release output already exists")

    with tempfile.TemporaryDirectory(prefix="ai-job-source-beta-") as directory:
        stage = Path(directory) / f"ai-job-source-agent-{version}"
        stage.mkdir()
        manifest_rows = []
        for relative in selected:
            source = root / relative
            if source.is_symlink() or not source.is_file():
                raise BetaReleaseError(f"release source is not a regular file: {relative}")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest_rows.append(
                {
                    "path": relative,
                    "sha256": _sha256(target),
                    "size": target.stat().st_size,
                }
            )
        manifest = {
            "schema_version": "1.0",
            "product_version": version,
            "git_commit": commit,
            "source_only": True,
            "excluded_runtime_artifacts": True,
            "files": manifest_rows,
        }
        (stage / "RELEASE_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        privacy = scan_artifact_root(stage)
        if privacy["total_matches"]:
            raise BetaReleaseError(
                f"release privacy scan found {privacy['total_matches']} credential shapes"
            )
        _write_deterministic_zip(stage, archive)

    digest = _sha256(archive)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return {
        "archive": str(archive),
        "archive_sha256": digest,
        "checksum": str(checksum),
        "file_count": len(selected) + 1,
        "git_commit": commit,
        "privacy_matches": 0,
        "product_version": version,
    }


def select_release_files(paths: Iterable[str]) -> list[str]:
    selected = []
    for raw in paths:
        value = PurePosixPath(raw)
        if value.is_absolute() or ".." in value.parts:
            raise BetaReleaseError(f"unsafe tracked path: {raw}")
        relative = value.as_posix()
        if any(part in FORBIDDEN_PARTS for part in value.parts):
            continue
        if relative.endswith(".DS_Store") or relative.endswith(".env"):
            continue
        if (
            relative in ROOT_FILES
            or relative in DOC_FILES
            or relative in SAMPLE_FILES
            or relative.startswith("docs/adr/")
            or relative.startswith(SOURCE_PREFIXES)
            or relative.startswith(SAMPLE_PREFIXES)
        ):
            selected.append(relative)
    return sorted(set(selected))


def _clean_git_commit(root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise BetaReleaseError("worktree must be clean before packaging")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _tracked_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [os.fsdecode(value) for value in output.split(b"\0") if value]


def _write_deterministic_zip(stage: Path, archive: Path) -> None:
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as handle:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                relative = path.relative_to(stage.parent).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(2026, 7, 31, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                handle.writestr(info, path.read_bytes())
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
