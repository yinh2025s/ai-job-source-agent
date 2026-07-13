from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from job_source_agent.identity_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    FilesystemLinkedInWebsiteEvidenceStore,
)


class FilesystemLinkedInWebsiteEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "nested" / "identity-evidence.json"
        self.now = [1_000.0]
        self.store = FilesystemLinkedInWebsiteEvidenceStore(
            self.path,
            max_age_seconds=60,
            clock=lambda: self.now[0],
        )
        self.company_name = "Acme Corp"
        self.linkedin_url = "https://www.linkedin.com/company/acme"

    def _payload(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_payload(self, payload: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            encoded = payload
        else:
            encoded = json.dumps(payload)
        self.path.write_text(encoded, encoding="utf-8")

    def _temporary_files(self) -> list[Path]:
        return list(self.path.parent.glob(f".{self.path.name}.*.tmp"))

    def test_ttl_configuration_must_be_finite_and_positive(self) -> None:
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite and positive"):
                    FilesystemLinkedInWebsiteEvidenceStore(
                        self.path,
                        max_age_seconds=value,
                    )

    def test_round_trip_persists_versioned_normalized_record(self) -> None:
        self.store.save(
            "  ACME   Corp ",
            "https://www.linkedin.com/company/acme/?utm_source=test#about",
            (
                " https://acme.example/?utm_campaign=hiring#about ",
                "https://acme.example/careers",
            ),
        )

        reader = FilesystemLinkedInWebsiteEvidenceStore(
            self.path,
            max_age_seconds=60,
            clock=lambda: self.now[0],
        )
        self.assertEqual(
            reader.load(self.company_name, self.linkedin_url),
            ("https://acme.example/", "https://acme.example/careers"),
        )

        payload = self._payload()
        self.assertEqual(payload["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(len(payload["records"]), 1)
        record = next(iter(payload["records"].values()))
        self.assertEqual(record["company_name"], "acme corp")
        self.assertEqual(record["linkedin_company_url"], self.linkedin_url)
        self.assertEqual(record["observed_at"], 1_000.0)
        self.assertEqual(self._temporary_files(), [])

    def test_ttl_accepts_boundary_and_rejects_expired_or_future_records(self) -> None:
        expected = ("https://acme.example",)
        self.store.save(self.company_name, self.linkedin_url, expected)

        self.now[0] = 1_060.0
        self.assertEqual(self.store.load(self.company_name, self.linkedin_url), expected)

        self.now[0] = 1_060.001
        self.assertEqual(self.store.load(self.company_name, self.linkedin_url), ())

        self.now[0] = 999.999
        self.assertEqual(self.store.load(self.company_name, self.linkedin_url), ())

    def test_company_and_linkedin_url_are_both_part_of_identity(self) -> None:
        acme_url = "https://www.linkedin.com/company/acme"
        labs_url = "https://www.linkedin.com/company/acme-labs"
        self.store.save("Acme", acme_url, ("https://acme.example",))
        self.store.save("Acme", labs_url, ("https://labs.example",))
        self.store.save("Beta", acme_url, ("https://beta.example",))

        self.assertEqual(self.store.load("Acme", acme_url), ("https://acme.example",))
        self.assertEqual(self.store.load("Acme", labs_url), ("https://labs.example",))
        self.assertEqual(self.store.load("Beta", acme_url), ("https://beta.example",))
        self.assertEqual(self.store.load("Unknown", acme_url), ())
        self.assertEqual(
            self.store.load("Acme", "https://www.linkedin.com/company/unknown"),
            (),
        )

    def test_equivalent_identity_and_official_urls_are_normalized_and_deduplicated(self) -> None:
        self.store.save(
            "  ACME   CORP ",
            "https://www.linkedin.com/company/acme/?utm_medium=social#details",
            (
                " https://acme.example/careers?utm_source=linkedin#roles ",
                "https://acme.example/careers#engineering",
                "https://acme.example/careers?utm_campaign=hiring",
                "https://acme.example/jobs?team=ai&utm_content=link",
                "https://acme.example/jobs?team=ai#openings",
            ),
        )

        expected = (
            "https://acme.example/careers",
            "https://acme.example/jobs?team=ai",
        )
        self.assertEqual(self.store.load("acme corp", self.linkedin_url), expected)
        record = next(iter(self._payload()["records"].values()))
        self.assertEqual(tuple(record["official_website_urls"]), expected)

    def test_corrupt_and_incompatible_payloads_are_cache_misses(self) -> None:
        invalid_payloads = {
            "invalid JSON": "{broken",
            "non-object root": [],
            "missing schema": {"records": {}},
            "old schema": {"schema_version": EVIDENCE_SCHEMA_VERSION - 1, "records": {}},
            "invalid records collection": {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "records": [],
            },
        }

        for case, payload in invalid_payloads.items():
            with self.subTest(case=case):
                self._write_payload(payload)
                self.assertEqual(self.store.load(self.company_name, self.linkedin_url), ())

    def test_save_recovers_from_corrupt_or_old_schema_without_retaining_old_records(self) -> None:
        incompatible_payloads = (
            "{broken",
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION - 1,
                "records": {"legacy": {"official_website_urls": ["https://old.example"]}},
            },
        )

        for payload in incompatible_payloads:
            with self.subTest(payload=payload):
                self._write_payload(payload)
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    ("https://acme.example",),
                )
                persisted = self._payload()
                self.assertEqual(persisted["schema_version"], EVIDENCE_SCHEMA_VERSION)
                self.assertNotIn("legacy", persisted["records"])
                self.assertEqual(
                    self.store.load(self.company_name, self.linkedin_url),
                    ("https://acme.example",),
                )

    def test_malformed_records_are_cache_misses(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            ("https://acme.example",),
        )
        valid_payload = self._payload()
        key = next(iter(valid_payload["records"]))

        mutations = {
            "non-object record": lambda payload: payload["records"].__setitem__(key, []),
            "missing timestamp": lambda payload: payload["records"][key].pop("observed_at"),
            "invalid timestamp": lambda payload: payload["records"][key].__setitem__(
                "observed_at", "not-a-number"
            ),
            "wrong company": lambda payload: payload["records"][key].__setitem__(
                "company_name", "different company"
            ),
            "wrong LinkedIn URL": lambda payload: payload["records"][key].__setitem__(
                "linkedin_company_url", "https://www.linkedin.com/company/different"
            ),
            "non-list URL collection": lambda payload: payload["records"][key].__setitem__(
                "official_website_urls", "https://acme.example"
            ),
        }

        for case, mutate in mutations.items():
            with self.subTest(case=case):
                payload = copy.deepcopy(valid_payload)
                mutate(payload)
                self._write_payload(payload)
                self.assertEqual(self.store.load(self.company_name, self.linkedin_url), ())

    def test_non_finite_timestamp_is_treated_as_corrupt(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            ("https://acme.example",),
        )
        payload = self._payload()
        record = next(iter(payload["records"].values()))
        record["observed_at"] = "NaN"
        self._write_payload(payload)

        self.assertEqual(self.store.load(self.company_name, self.linkedin_url), ())

    def test_identity_and_official_urls_reject_unsafe_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "company_name"):
            self.store.save(" ", self.linkedin_url, ("https://acme.example",))
        with self.assertRaisesRegex(ValueError, "LinkedIn host"):
            self.store.save(
                self.company_name,
                "https://example.com/company/acme",
                ("https://acme.example",),
            )
        with self.assertRaisesRegex(ValueError, "company page"):
            self.store.save(
                self.company_name,
                "https://www.linkedin.com/in/acme",
                ("https://acme.example",),
            )

        self.store.save(
            self.company_name,
            self.linkedin_url,
            (
                "javascript:alert(1)",
                "https://user:secret@private.example",
                "https://acme.example:8443/careers",
                "https://acme.example/careers?team=ai&token=do-not-store",
            ),
        )
        self.assertEqual(
            self.store.load(self.company_name, self.linkedin_url),
            ("https://acme.example/careers?team=ai",),
        )
        self.assertNotIn("do-not-store", self.path.read_text(encoding="utf-8"))

    def test_non_finite_clock_is_rejected_without_writing(self) -> None:
        store = FilesystemLinkedInWebsiteEvidenceStore(
            self.path,
            max_age_seconds=60,
            clock=lambda: float("inf"),
        )

        with self.assertRaisesRegex(ValueError, "finite timestamp"):
            store.save(
                self.company_name,
                self.linkedin_url,
                ("https://acme.example",),
            )

        self.assertFalse(self.path.exists())

    def test_concurrent_store_instances_do_not_lose_records(self) -> None:
        writer_count = 16
        barrier = Barrier(writer_count)

        def write_record(index: int) -> None:
            store = FilesystemLinkedInWebsiteEvidenceStore(
                self.path,
                max_age_seconds=60,
                clock=lambda: self.now[0],
            )
            barrier.wait()
            store.save(
                f"Company {index}",
                f"https://www.linkedin.com/company/company-{index}",
                (f"https://company-{index}.example",),
            )

        with ThreadPoolExecutor(max_workers=writer_count) as executor:
            list(executor.map(write_record, range(writer_count)))

        payload = self._payload()
        self.assertEqual(payload["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(len(payload["records"]), writer_count)
        for index in range(writer_count):
            self.assertEqual(
                self.store.load(
                    f"Company {index}",
                    f"https://www.linkedin.com/company/company-{index}",
                ),
                (f"https://company-{index}.example",),
            )
        self.assertEqual(self._temporary_files(), [])

    def test_atomic_replace_failure_preserves_previous_record_and_cleans_temp_file(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            ("https://old.example",),
        )
        previous_bytes = self.path.read_bytes()

        with patch(
            "job_source_agent.identity_evidence.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    ("https://new.example",),
                )

        self.assertEqual(self.path.read_bytes(), previous_bytes)
        self.assertEqual(
            self.store.load(self.company_name, self.linkedin_url),
            ("https://old.example",),
        )
        self.assertEqual(self._temporary_files(), [])

    def test_replacement_publishes_a_complete_temp_payload_atomically(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            ("https://old.example",),
        )
        previous_bytes = self.path.read_bytes()
        real_replace = os.replace
        replacement_payloads: list[dict] = []

        def inspect_then_replace(source: str, destination: str | Path) -> None:
            source_path = Path(source)
            self.assertEqual(source_path.parent, self.path.parent)
            self.assertEqual(Path(destination), self.path)
            replacement_payloads.append(json.loads(source_path.read_text(encoding="utf-8")))
            self.assertEqual(self.path.read_bytes(), previous_bytes)
            real_replace(source, destination)

        with patch(
            "job_source_agent.identity_evidence.os.replace",
            side_effect=inspect_then_replace,
        ):
            self.store.save(
                self.company_name,
                self.linkedin_url,
                ("https://new.example",),
            )

        self.assertEqual(len(replacement_payloads), 1)
        self.assertEqual(replacement_payloads[0]["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(
            self.store.load(self.company_name, self.linkedin_url),
            ("https://new.example",),
        )
        self.assertEqual(self._temporary_files(), [])


if __name__ == "__main__":
    unittest.main()
