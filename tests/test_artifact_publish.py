import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_source_agent.artifact_publish import (
    ArtifactCollisionError,
    AttemptArtifactTransaction,
)


class AttemptArtifactTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.canonical_root = self.root / "canonical"
        self.transaction = AttemptArtifactTransaction(self.canonical_root, "attempt-1")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_stage_and_replace_existing_artifact(self) -> None:
        source = self.transaction.stage_path("nested/result.json")
        source.write_bytes(b"new bytes")
        destination = self.canonical_root / "published" / "result.json"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old bytes")

        published = self.transaction.publish_file(source, destination)

        self.assertEqual(published, destination)
        self.assertEqual(destination.read_bytes(), b"new bytes")
        self.assertEqual(source.read_bytes(), b"new bytes")

    def test_require_identical_is_noop_and_rejects_collision(self) -> None:
        source = self.transaction.stage_path("result.bin")
        source.write_bytes(b"same")
        destination = self.canonical_root / "result.bin"
        destination.write_bytes(b"same")

        with patch("job_source_agent.artifact_publish.os.replace") as replace:
            self.assertEqual(
                self.transaction.publish_file(source, destination, existing="require-identical"),
                destination,
            )
        replace.assert_not_called()

        source.write_bytes(b"different")
        with self.assertRaises(ArtifactCollisionError):
            self.transaction.publish_file(source, destination, existing="require-identical")
        self.assertEqual(destination.read_bytes(), b"same")

    def test_publication_copies_to_destination_filesystem_before_replace(self) -> None:
        source_root = self.root / "distinct-source-root"
        source_root.mkdir()
        source = source_root / "payload"
        source.write_bytes(b"payload")
        destination = self.canonical_root / "outputs" / "payload"
        real_replace = os.replace
        replace_inputs: list[tuple[Path, Path]] = []

        def inspect_replace(temporary: str | Path, target: str | Path) -> None:
            temporary_path = Path(temporary)
            target_path = Path(target)
            replace_inputs.append((temporary_path, target_path))
            self.assertEqual(temporary_path.parent, destination.parent)
            self.assertNotEqual(temporary_path, source)
            self.assertEqual(temporary_path.read_bytes(), source.read_bytes())
            real_replace(temporary_path, target_path)

        with patch("job_source_agent.artifact_publish.os.replace", side_effect=inspect_replace):
            self.transaction.publish_file(source, destination)

        self.assertEqual(len(replace_inputs), 1)
        self.assertEqual(destination.read_bytes(), b"payload")
        self.assertTrue(source.exists())

    def test_rejects_absolute_and_traversing_staging_paths(self) -> None:
        for path in (self.root / "absolute", Path("../outside"), Path("a/../../outside")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.transaction.stage_path(path)

    def test_rejects_destinations_outside_root_and_symlink_escapes(self) -> None:
        source = self.transaction.stage_path("source")
        source.write_bytes(b"payload")
        outside = self.root / "outside"
        outside.mkdir()

        for destination in (outside / "artifact", Path("../outside/artifact")):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.transaction.publish_file(source, destination)

        staging_link = self.transaction.staging_root / "escape"
        staging_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.transaction.stage_path("escape/staged")

        destination_link = self.canonical_root / "escape"
        destination_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.transaction.publish_file(source, destination_link / "artifact")

    def test_failed_replace_preserves_old_destination_and_cleans_temp(self) -> None:
        source = self.transaction.stage_path("source")
        source.write_bytes(b"new")
        destination = self.canonical_root / "artifact"
        destination.write_bytes(b"old")

        with patch(
            "job_source_agent.artifact_publish.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                self.transaction.publish_file(source, destination)

        self.assertEqual(destination.read_bytes(), b"old")
        self.assertEqual(list(self.canonical_root.glob(".artifact.*.tmp")), [])

    def test_abort_removes_only_current_staging_tree(self) -> None:
        staged = self.transaction.stage_path("nested/staged")
        staged.write_bytes(b"staged")
        canonical = self.canonical_root / "canonical-artifact"
        canonical.write_bytes(b"keep")
        other_attempt = self.canonical_root / ".attempts" / "attempt-2" / "other"
        other_attempt.parent.mkdir(parents=True)
        other_attempt.write_bytes(b"keep too")

        self.transaction.abort()

        self.assertFalse(self.transaction.staging_root.exists())
        self.assertEqual(canonical.read_bytes(), b"keep")
        self.assertEqual(other_attempt.read_bytes(), b"keep too")


if __name__ == "__main__":
    unittest.main()
