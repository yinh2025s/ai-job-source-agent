from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal


ExistingPolicy = Literal["replace", "require-identical"]


class ArtifactIntegrityError(RuntimeError):
    """Raised when publishing would violate artifact integrity."""


class ArtifactCollisionError(ArtifactIntegrityError):
    """Raised when an existing artifact differs from the requested artifact."""


class AttemptArtifactTransaction:
    """Stage and atomically publish files beneath one canonical artifact root."""

    _SAFE_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

    def __init__(self, canonical_root: str | Path, attempt_id: str) -> None:
        if not isinstance(attempt_id, str) or not self._SAFE_ATTEMPT_ID.fullmatch(attempt_id):
            raise ValueError("attempt_id must contain only safe filename characters")
        if attempt_id in {".", ".."}:
            raise ValueError("attempt_id must not be a relative path marker")

        root = Path(canonical_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        self.canonical_root = root.resolve(strict=True)
        self.attempt_id = attempt_id
        self.staging_root = self.canonical_root / ".attempts" / attempt_id
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._require_within(self.staging_root, self.canonical_root, "staging root")

    def stage_path(self, relative_path: str | Path) -> Path:
        """Return a writable staging path after validating it cannot escape."""
        relative = Path(relative_path)
        self._require_safe_relative(relative, "staging path")
        path = self.staging_root / relative
        self._require_within(path, self.staging_root, "staging path")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._require_within(path, self.staging_root, "staging path")
        return path

    def publish_file(
        self,
        source: str | Path,
        destination: str | Path,
        existing: ExistingPolicy = "replace",
    ) -> Path:
        """Copy and atomically publish source bytes to a canonical destination."""
        if existing not in {"replace", "require-identical"}:
            raise ValueError("existing must be 'replace' or 'require-identical'")

        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"artifact source is not a file: {source_path}")

        destination_path = Path(destination)
        if ".." in destination_path.parts:
            raise ValueError("destination must not contain '..' traversal")
        if not destination_path.is_absolute():
            self._require_safe_relative(destination_path, "destination")
            destination_path = self.canonical_root / destination_path

        self._require_within(destination_path, self.canonical_root, "destination")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        self._require_within(destination_path, self.canonical_root, "destination")

        if existing == "require-identical" and destination_path.exists():
            if destination_path.is_file() and _files_identical(source_path, destination_path):
                return destination_path
            raise ArtifactCollisionError(
                f"artifact destination already exists with different bytes: {destination_path}"
            )

        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
                dir=destination_path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output, source_path.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, destination_path)
            temporary_path = None
            _fsync_directory(destination_path.parent)
            return destination_path
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def abort(self) -> None:
        """Remove this attempt's staging tree without touching canonical artifacts."""
        if self.staging_root.is_symlink():
            self.staging_root.unlink(missing_ok=True)
        else:
            shutil.rmtree(self.staging_root, ignore_errors=True)

    @staticmethod
    def _require_safe_relative(path: Path, label: str) -> None:
        if path.is_absolute():
            raise ValueError(f"{label} must be relative")
        if not path.parts or path == Path(".") or ".." in path.parts:
            raise ValueError(f"{label} must be a non-empty path without '..' traversal")

    @staticmethod
    def _require_within(path: Path, root: Path, label: str) -> None:
        resolved_path = path.resolve(strict=False)
        resolved_root = root.resolve(strict=True)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as error:
            raise ValueError(f"{label} escapes its allowed root") from error


def _files_identical(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as first_file, second.open("rb") as second_file:
        while True:
            first_chunk = first_file.read(1024 * 1024)
            second_chunk = second_file.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
