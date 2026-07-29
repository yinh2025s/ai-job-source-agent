#!/usr/bin/env python3.12
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


SCHEMA_VERSION = "1.0"


class ArtifactPrivacyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialPattern:
    name: str
    expression: re.Pattern[bytes]


def _compile(expression: bytes) -> re.Pattern[bytes]:
    return re.compile(expression, re.ASCII)


CREDENTIAL_PATTERNS = (
    CredentialPattern(
        "jwt",
        _compile(
            rb"(?<![A-Za-z0-9_-])"
            rb"eyJ[A-Za-z0-9_-]{5,1024}\."
            rb"[A-Za-z0-9_-]{5,8192}\."
            rb"[A-Za-z0-9_-]{5,2048}"
            rb"(?![A-Za-z0-9_-])"
        ),
    ),
    CredentialPattern(
        "aws_access_key_id",
        _compile(
            rb"(?<![A-Z0-9])"
            rb"(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)"
            rb"[A-Z0-9]{16}"
            rb"(?![A-Z0-9])"
        ),
    ),
    CredentialPattern(
        "google_api_key",
        _compile(
            rb"(?<![A-Za-z0-9_-])"
            rb"AIza[A-Za-z0-9_-]{35}"
            rb"(?![A-Za-z0-9_-])"
        ),
    ),
    CredentialPattern(
        "private_key_header",
        _compile(
            rb"-----BEGIN (?:"
            rb"(?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY"
            rb"|PGP PRIVATE KEY BLOCK"
            rb"|SSH2 ENCRYPTED PRIVATE KEY"
            rb")-----"
        ),
    ),
    CredentialPattern(
        "github_token",
        _compile(
            rb"(?<![A-Za-z0-9_])(?:"
            rb"gh[pousr]_[A-Za-z0-9]{36,255}"
            rb"|github_pat_[A-Za-z0-9_]{20,255}"
            rb")(?![A-Za-z0-9_])"
        ),
    ),
    CredentialPattern(
        "slack_token",
        _compile(
            rb"(?<![A-Za-z0-9-])(?:"
            rb"xox[baprs]-[A-Za-z0-9-]{10,255}"
            rb"|xapp-[A-Za-z0-9-]{10,255}"
            rb")(?![A-Za-z0-9-])"
        ),
    ),
    CredentialPattern(
        "openai_api_key",
        _compile(
            rb"(?<![A-Za-z0-9_-])(?:"
            rb"sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{20,240}"
            rb"|sk-[A-Za-z0-9]{32,64}"
            rb")(?![A-Za-z0-9_-])"
        ),
    ),
    CredentialPattern(
        "anthropic_api_key",
        _compile(
            rb"(?<![A-Za-z0-9_-])"
            rb"sk-ant-[A-Za-z0-9_-]{20,240}"
            rb"(?![A-Za-z0-9_-])"
        ),
    ),
    CredentialPattern(
        "stripe_secret_key",
        _compile(
            rb"(?<![A-Za-z0-9_])"
            rb"sk_live_[A-Za-z0-9]{16,128}"
            rb"(?![A-Za-z0-9_])"
        ),
    ),
)
COMBINED_CREDENTIAL_PATTERN = _compile(
    b"|".join(
        b"(?P<"
        + pattern.name.encode("ascii")
        + b">"
        + pattern.expression.pattern
        + b")"
        for pattern in CREDENTIAL_PATTERNS
    )
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively scan a completed artifact directory for structured "
            "credential shapes without printing matched values."
        )
    )
    parser.add_argument("artifact_root")
    parser.add_argument(
        "--fail-on-match",
        action="store_true",
        help="exit with status 1 when one or more credential shapes are found",
    )
    parser.add_argument(
        "--output",
        help="optional atomic JSON report path outside the scanned file set",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = scan_artifact_root(Path(args.artifact_root))
    except (ArtifactPrivacyError, OSError) as error:
        print(f"artifact privacy scan failed: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        _write_text_atomic(Path(args.output), rendered + "\n")
    print(rendered)
    if args.fail_on_match and report["total_matches"]:
        return 1
    return 0


def scan_artifact_root(artifact_root: Path) -> dict[str, object]:
    root_stat = _root_stat(artifact_root)
    if stat.S_ISLNK(root_stat.st_mode):
        raise ArtifactPrivacyError("artifact root cannot be a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ArtifactPrivacyError("artifact root must be a directory")

    root = Path(os.path.abspath(os.fspath(artifact_root)))
    file_paths = _regular_files(root)
    manifest_rows: list[dict[str, object]] = []
    match_rows: list[dict[str, object]] = []
    bytes_scanned = 0
    total_matches = 0

    for path in file_paths:
        relative_path = path.relative_to(root).as_posix()
        size, file_sha256, counts = _scan_regular_file(path)
        bytes_scanned += size
        manifest_rows.append(
            {
                "path": relative_path,
                "sha256": file_sha256,
                "size": size,
            }
        )
        safe_path = _redact_path(relative_path)
        for credential_type in sorted(counts):
            count = counts[credential_type]
            total_matches += count
            match_rows.append(
                {
                    "count": count,
                    "relative_path": safe_path,
                    "type": credential_type,
                }
            )

    manifest_bytes = json.dumps(
        manifest_rows,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return {
        "bytes_scanned": bytes_scanned,
        "file_set_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "files_scanned": len(manifest_rows),
        "matches": match_rows,
        "schema_version": SCHEMA_VERSION,
        "total_matches": total_matches,
    }


def _root_stat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as error:
        raise ArtifactPrivacyError("artifact root does not exist") from error
    except OSError as error:
        raise ArtifactPrivacyError("artifact root is not readable") from error


def _regular_files(root: Path) -> list[Path]:
    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: os.fsencode(entry.name))
        except OSError as error:
            raise ArtifactPrivacyError("artifact directory is not readable") from error

        directories: list[Path] = []
        for entry in ordered:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ArtifactPrivacyError(
                    "artifact entry cannot be inspected"
                ) from error
            mode = entry_stat.st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISDIR(mode):
                directories.append(Path(entry.path))
            elif stat.S_ISREG(mode):
                files.append(Path(entry.path))
        pending.extend(reversed(directories))
    return sorted(
        files,
        key=lambda path: os.fsencode(path.relative_to(root).as_posix()),
    )


def _scan_regular_file(
    path: Path,
) -> tuple[int, str, dict[str, int]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ArtifactPrivacyError("artifact file changed type during scan")
    if stat.S_IMODE(before.st_mode) & 0o444 == 0:
        raise ArtifactPrivacyError("artifact file is not readable")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactPrivacyError("artifact file is not readable") from error

    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ArtifactPrivacyError("artifact file changed during scan")
            size, digest, counts = _scan_open_file(stream, opened.st_size)
            after = os.fstat(stream.fileno())
    except Exception:
        # os.fdopen owns and closes the descriptor after construction.
        raise

    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    ):
        raise ArtifactPrivacyError("artifact file changed during scan")
    return size, digest, counts


def _scan_open_file(
    stream: BinaryIO,
    size: int,
) -> tuple[int, str, dict[str, int]]:
    if size == 0:
        return 0, hashlib.sha256(b"").hexdigest(), {}

    try:
        with mmap.mmap(stream.fileno(), length=0, access=mmap.ACCESS_READ) as content:
            digest = hashlib.sha256(content).hexdigest()
            counts = Counter(
                match.lastgroup
                for match in COMBINED_CREDENTIAL_PATTERN.finditer(content)
            )
    except (BufferError, OSError, ValueError) as error:
        raise ArtifactPrivacyError("artifact file could not be scanned") from error
    return size, digest, dict(counts)


def _redact_path(relative_path: str) -> str:
    value = os.fsencode(relative_path)
    for pattern in CREDENTIAL_PATTERNS:
        value = pattern.expression.sub(b"[REDACTED]", value)
    return os.fsdecode(value)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
