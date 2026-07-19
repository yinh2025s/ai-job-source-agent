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

from job_source_agent.company_discovery_evidence import (
    COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
    VerifiedCareerEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)


class FilesystemCompanyDiscoveryEvidenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = (
            Path(self.temporary_directory.name)
            / "nested"
            / "company-discovery-evidence.json"
        )
        self.now = [1_000.0]
        self.store = FilesystemCompanyDiscoveryEvidenceStore(
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
            self.path.write_text(payload, encoding="utf-8")
        else:
            self.path.write_text(json.dumps(payload), encoding="utf-8")

    def _record(self, payload: dict | None = None) -> dict:
        source = self._payload() if payload is None else payload
        self.assertEqual(len(source["records"]), 1)
        return next(iter(source["records"].values()))

    def _temporary_files(self) -> list[Path]:
        if not self.path.parent.exists():
            return []
        return list(self.path.parent.glob(f".{self.path.name}.*.tmp"))

    def _website(
        self,
        *,
        url: str = "https://acme.example",
        evidence_url: str = "https://www.linkedin.com/company/acme/about",
        observed_at: float = 1_000.0,
    ) -> VerifiedWebsiteEvidence:
        return VerifiedWebsiteEvidence(
            url=url,
            source="linkedin_official_website",
            evidence_url=evidence_url,
            observed_at=observed_at,
        )

    def _career(
        self,
        *,
        url: str = "https://acme.example/careers",
        website_url: str = "https://acme.example",
        evidence_url: str = "https://acme.example/about",
        observed_at: float = 1_000.0,
    ) -> VerifiedCareerEvidence:
        return VerifiedCareerEvidence(
            url=url,
            website_url=website_url,
            source="first_party_navigation",
            evidence_url=evidence_url,
            observed_at=observed_at,
        )

    def _board(
        self,
        *,
        provider: str = "greenhouse",
        tenant: str = "acme",
        canonical_board_url: str = "https://boards.greenhouse.io/acme",
        relationship_evidence_url: str = "https://acme.example/careers",
        verification_method: str = "career-page-link",
        observed_at: float = 1_000.0,
    ) -> VerifiedProviderBoardEvidence:
        return VerifiedProviderBoardEvidence(
            provider=provider,
            tenant=tenant,
            canonical_board_url=canonical_board_url,
            relationship_evidence_url=relationship_evidence_url,
            verification_method=verification_method,
            source="first_party_handoff",
            observed_at=observed_at,
        )

    def _save_complete_record(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(),
            career=self._career(),
            provider_board=self._board(),
        )

    def test_key_requires_normalized_company_and_linkedin_identity(self) -> None:
        identities = (
            ("Acme", "https://www.linkedin.com/company/acme", "acme.example"),
            ("Acme Labs", "https://www.linkedin.com/company/acme", "labs.example"),
            ("Acme", "https://www.linkedin.com/company/acme-labs", "brand.example"),
        )
        for company, linkedin_url, host in identities:
            self.store.save(
                company,
                linkedin_url,
                website=self._website(
                    url=f"https://{host}",
                    evidence_url=linkedin_url,
                ),
            )

        self.assertEqual(len(self._payload()["records"]), 3)
        for company, linkedin_url, host in identities:
            loaded = self.store.load(company, linkedin_url)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.website.url, f"https://{host}")
        self.assertIsNone(
            self.store.load("Different Company", "https://www.linkedin.com/company/acme-labs")
        )

    def test_equivalent_identity_is_normalized_to_one_key(self) -> None:
        self.store.save(
            "  ACME   CORP ",
            "https://linkedin.com/company/acme/?utm_source=test#about",
            website=self._website(),
        )
        self.store.save(
            "acme corp",
            self.linkedin_url,
            career=self._career(),
        )

        self.assertEqual(len(self._payload()["records"]), 1)
        loaded = self.store.load("ACME CORP", self.linkedin_url + "/")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.company_name, "acme corp")
        self.assertEqual(loaded.linkedin_company_url, self.linkedin_url)
        self.assertIsNotNone(loaded.website)
        self.assertIsNotNone(loaded.career)

    def test_older_seed_cannot_replace_newer_discovery_layers(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(url="https://new.example", observed_at=1_000.0),
            career=self._career(
                url="https://new.example/careers",
                website_url="https://new.example",
                observed_at=1_000.0,
            ),
            provider_board=self._board(
                canonical_board_url="https://boards.greenhouse.io/new-acme",
                observed_at=1_000.0,
            ),
        )

        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(url="https://old.example", observed_at=900.0),
            career=self._career(
                url="https://old.example/jobs",
                website_url="https://old.example",
                observed_at=900.0,
            ),
            provider_board=self._board(
                canonical_board_url="https://boards.greenhouse.io/old-acme",
                observed_at=900.0,
            ),
        )

        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.website.url, "https://new.example")
        self.assertEqual(loaded.career.url, "https://new.example/careers")
        self.assertEqual(
            [board.canonical_board_url for board in loaded.provider_boards],
            ["https://boards.greenhouse.io/new-acme"],
        )

    def test_career_must_belong_to_current_website_layer(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(),
        )

        self.store.save(
            self.company_name,
            self.linkedin_url,
            career=self._career(
                url="https://other.example/careers",
                website_url="https://other.example",
                observed_at=1_001.0,
            ),
        )

        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded.career)

    def test_each_layer_has_an_independent_ttl_with_exact_boundary_valid(self) -> None:
        cases = {
            "website expired invalidates descendants": (
                {"website": 939.999, "career": 1_000.0, "board": 1_000.0},
                (False, False, 0),
            ),
            "career expired invalidates boards": (
                {"website": 1_000.0, "career": 939.999, "board": 1_000.0},
                (True, False, 0),
            ),
            "provider board expires independently": (
                {"website": 1_000.0, "career": 1_000.0, "board": 939.999},
                (True, True, 0),
            ),
            "exact ttl boundary is valid": (
                {"website": 940.0, "career": 940.0, "board": 940.0},
                (True, True, 1),
            ),
        }
        for case, (timestamps, expected) in cases.items():
            with self.subTest(case=case):
                self.path.unlink(missing_ok=True)
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    website=self._website(observed_at=timestamps["website"]),
                    career=self._career(observed_at=timestamps["career"]),
                    provider_board=self._board(observed_at=timestamps["board"]),
                )
                loaded = self.store.load(self.company_name, self.linkedin_url)
                if loaded is None:
                    actual = (False, False, 0)
                else:
                    actual = (
                        loaded.website is not None,
                        loaded.career is not None,
                        len(loaded.provider_boards),
                    )
                self.assertEqual(actual, expected)

    def test_future_timestamp_is_invalid_per_layer(self) -> None:
        cases = {
            "website": (1_000.001, 1_000.0, 1_000.0, None),
            "career": (1_000.0, 1_000.001, 1_000.0, (True, False, 0)),
            "provider board": (1_000.0, 1_000.0, 1_000.001, (True, True, 0)),
        }
        for case, (website_at, career_at, board_at, expected) in cases.items():
            with self.subTest(layer=case):
                self.path.unlink(missing_ok=True)
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    website=self._website(observed_at=website_at),
                    career=self._career(observed_at=career_at),
                    provider_board=self._board(observed_at=board_at),
                )
                loaded = self.store.load(self.company_name, self.linkedin_url)
                if expected is None:
                    self.assertIsNone(loaded)
                else:
                    self.assertIsNotNone(loaded)
                    self.assertEqual(
                        (
                            loaded.website is not None,
                            loaded.career is not None,
                            len(loaded.provider_boards),
                        ),
                        expected,
                    )

    def test_non_finite_timestamps_and_clock_are_safe_misses(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timestamp=value):
                with self.assertRaisesRegex(ValueError, "observed_at must be finite"):
                    self._website(observed_at=value)

        self._save_complete_record()
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(clock=value):
                store = FilesystemCompanyDiscoveryEvidenceStore(
                    self.path,
                    max_age_seconds=60,
                    clock=lambda value=value: value,
                )
                self.assertIsNone(store.load(self.company_name, self.linkedin_url))

    def test_corrupt_and_incompatible_payloads_are_misses_and_next_save_recovers(self) -> None:
        payloads = {
            "invalid json": "{broken",
            "non-object root": [],
            "missing schema": {"records": {}},
            "wrong schema": {
                "schema_version": COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION + 1,
                "records": {"legacy": {"website": {"url": "https://old.example"}}},
            },
            "non-object records": {
                "schema_version": COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
                "records": [],
            },
        }
        for case, payload in payloads.items():
            with self.subTest(case=case):
                self._write_payload(payload)
                self.assertIsNone(self.store.load(self.company_name, self.linkedin_url))
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    website=self._website(),
                )
                persisted = self._payload()
                self.assertEqual(
                    persisted["schema_version"],
                    COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
                )
                self.assertNotIn("legacy", persisted["records"])
                self.assertIsNotNone(self.store.load(self.company_name, self.linkedin_url))

    def test_malformed_layers_fail_closed_without_discarding_valid_ancestors(self) -> None:
        self._save_complete_record()
        valid_payload = self._payload()
        key = next(iter(valid_payload["records"]))
        mutations = {
            "non-object record": (
                lambda payload: payload["records"].__setitem__(key, []),
                None,
            ),
            "wrong stored company": (
                lambda payload: payload["records"][key].__setitem__(
                    "company_name", "another company"
                ),
                None,
            ),
            "invalid website source": (
                lambda payload: payload["records"][key]["website"].__setitem__(
                    "source", "untrusted"
                ),
                None,
            ),
            "missing website timestamp": (
                lambda payload: payload["records"][key]["website"].pop("observed_at"),
                None,
            ),
            "malformed career url": (
                lambda payload: payload["records"][key]["career"].__setitem__(
                    "url", "not a URL"
                ),
                (True, False, 0),
            ),
            "non-list boards": (
                lambda payload: payload["records"][key].__setitem__(
                    "provider_boards", {"provider": "greenhouse"}
                ),
                (True, True, 0),
            ),
            "malformed board timestamp": (
                lambda payload: payload["records"][key]["provider_boards"][0].__setitem__(
                    "observed_at", "not-a-number"
                ),
                (True, True, 0),
            ),
        }
        for case, (mutate, expected) in mutations.items():
            with self.subTest(case=case):
                payload = copy.deepcopy(valid_payload)
                mutate(payload)
                self._write_payload(payload)
                loaded = self.store.load(self.company_name, self.linkedin_url)
                if expected is None:
                    self.assertIsNone(loaded)
                else:
                    self.assertIsNotNone(loaded)
                    self.assertEqual(
                        (
                            loaded.website is not None,
                            loaded.career is not None,
                            len(loaded.provider_boards),
                        ),
                        expected,
                    )

    def test_valid_save_recovers_when_existing_provider_records_are_malformed(self) -> None:
        self._save_complete_record()
        payload = self._payload()
        record = self._record(payload)
        record["provider_boards"].extend(
            [
                "not-an-object",
                {"provider": "broken", "observed_at": "not-a-number"},
            ]
        )
        self._write_payload(payload)

        replacement = self._board(
            provider="lever",
            tenant="acme-inc",
            canonical_board_url="https://jobs.lever.co/acme-inc",
            observed_at=999.0,
        )
        self.store.save(
            self.company_name,
            self.linkedin_url,
            provider_board=replacement,
        )

        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(
            {(item.provider, item.tenant) for item in loaded.provider_boards},
            {("greenhouse", "acme"), ("lever", "acme-inc")},
        )

    def test_save_merges_independently_verified_layers(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(observed_at=980.0),
        )
        self.store.save(
            self.company_name,
            self.linkedin_url,
            career=self._career(observed_at=990.0),
        )
        self.store.save(
            self.company_name,
            self.linkedin_url,
            provider_board=self._board(observed_at=1_000.0),
        )

        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.website.observed_at, 980.0)
        self.assertEqual(loaded.career.observed_at, 990.0)
        self.assertEqual(len(loaded.provider_boards), 1)

    def test_website_and_career_replacement_cascade_to_descendants(self) -> None:
        self._save_complete_record()
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(url="https://new-acme.example"),
        )
        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.website.url, "https://new-acme.example")
        self.assertIsNone(loaded.career)
        self.assertEqual(loaded.provider_boards, ())

        self.store.save(
            self.company_name,
            self.linkedin_url,
            career=self._career(
                url="https://new-acme.example/jobs",
                website_url="https://new-acme.example",
            ),
            provider_board=self._board(
                relationship_evidence_url="https://new-acme.example/jobs"
            ),
        )
        self.store.save(
            self.company_name,
            self.linkedin_url,
            career=self._career(
                url="https://new-acme.example/work-with-us",
                website_url="https://new-acme.example",
            ),
        )
        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.career.url, "https://new-acme.example/work-with-us")
        self.assertEqual(loaded.provider_boards, ())

    def test_invalidate_matches_urls_and_cascades_only_downstream(self) -> None:
        self._save_complete_record()
        self.store.invalidate(
            self.company_name,
            self.linkedin_url,
            layer="career",
            evidence_url="https://unrelated.example",
        )
        self.assertEqual(len(self.store.load(self.company_name, self.linkedin_url).provider_boards), 1)

        self.store.invalidate(
            self.company_name,
            self.linkedin_url,
            layer="career",
            evidence_url="https://acme.example/careers#fragment",
        )
        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertIsNotNone(loaded.website)
        self.assertIsNone(loaded.career)
        self.assertEqual(loaded.provider_boards, ())

        self.store.save(
            self.company_name,
            self.linkedin_url,
            career=self._career(),
            provider_board=self._board(),
        )
        self.store.invalidate(
            self.company_name,
            self.linkedin_url,
            layer="website",
            evidence_url="https://www.linkedin.com/company/acme/about",
        )
        self.assertIsNone(self.store.load(self.company_name, self.linkedin_url))
        self.assertEqual(self._payload()["records"], {})

    def test_provider_board_deduplicates_case_insensitive_identity_and_keeps_newest_eight(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(),
            career=self._career(),
        )
        for index in range(10):
            self.store.save(
                self.company_name,
                self.linkedin_url,
                provider_board=self._board(
                    provider="greenhouse",
                    tenant=f"tenant-{index}",
                    canonical_board_url=f"https://boards.greenhouse.io/tenant-{index}",
                    observed_at=990.0 + index,
                ),
            )
        self.store.save(
            self.company_name,
            self.linkedin_url,
            provider_board=self._board(
                provider="GREENHOUSE",
                tenant="TENANT-9",
                canonical_board_url="https://boards.greenhouse.io/tenant-9",
                verification_method="refreshed-link",
                observed_at=1_000.0,
            ),
        )

        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.provider_boards), 8)
        self.assertEqual(
            {board.tenant.casefold() for board in loaded.provider_boards},
            {f"tenant-{index}" for index in range(2, 10)},
        )
        refreshed = [board for board in loaded.provider_boards if board.tenant == "TENANT-9"]
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0].verification_method, "refreshed-link")

    def test_sensitive_queries_and_fragments_are_removed_from_every_url_field(self) -> None:
        secret = "must-not-persist"
        suffix = f"?team=ai&ToKeN={secret}&state={secret}#private"
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(
                url="https://acme.example/" + suffix,
                evidence_url="https://linkedin.com/company/acme/about" + suffix,
            ),
            career=self._career(
                url="https://acme.example/careers" + suffix,
                website_url="https://acme.example/" + suffix,
                evidence_url="https://acme.example/about" + suffix,
            ),
            provider_board=self._board(
                canonical_board_url="https://boards.greenhouse.io/acme" + suffix,
                relationship_evidence_url="https://acme.example/careers" + suffix,
            ),
        )

        serialized = self.path.read_text(encoding="utf-8")
        self.assertNotIn(secret, serialized)
        self.assertNotIn("#private", serialized)
        loaded = self.store.load(self.company_name, self.linkedin_url)
        self.assertIsNotNone(loaded)
        urls = (
            loaded.website.url,
            loaded.website.evidence_url,
            loaded.career.url,
            loaded.career.website_url,
            loaded.career.evidence_url,
            loaded.provider_boards[0].canonical_board_url,
            loaded.provider_boards[0].relationship_evidence_url,
        )
        self.assertTrue(all(url.endswith("?team=ai") for url in urls))

    def test_every_url_field_rejects_credentials_private_hosts_and_nonstandard_ports(self) -> None:
        unsafe_urls = {
            "credentials": "https://user:password@public.example/path",
            "localhost": "http://localhost/path",
            "private ipv4": "http://10.0.0.1/path",
            "private ipv6": "https://[::1]/path",
            "private suffix": "https://service.internal/path",
            "nonstandard port": "https://public.example:8443/path",
        }
        factories = {
            "website url": lambda url: {"website": self._website(url=url)},
            "website evidence": lambda url: {
                "website": self._website(evidence_url=url)
            },
            "career url": lambda url: {"career": self._career(url=url)},
            "career website": lambda url: {
                "career": self._career(website_url=url)
            },
            "career evidence": lambda url: {
                "career": self._career(evidence_url=url)
            },
            "board url": lambda url: {
                "provider_board": self._board(canonical_board_url=url)
            },
            "board relationship": lambda url: {
                "provider_board": self._board(relationship_evidence_url=url)
            },
        }
        for field, factory in factories.items():
            for case, unsafe_url in unsafe_urls.items():
                with self.subTest(field=field, case=case):
                    with self.assertRaises(ValueError):
                        self.store.save(
                            self.company_name,
                            self.linkedin_url,
                            **factory(unsafe_url),
                        )

    def test_atomic_replace_failure_preserves_previous_payload_and_cleans_temp_file(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(url="https://old.example"),
        )
        previous_bytes = self.path.read_bytes()

        with patch(
            "job_source_agent.company_discovery_evidence_store.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                self.store.save(
                    self.company_name,
                    self.linkedin_url,
                    website=self._website(url="https://new.example"),
                )

        self.assertEqual(self.path.read_bytes(), previous_bytes)
        self.assertEqual(
            self.store.load(self.company_name, self.linkedin_url).website.url,
            "https://old.example",
        )
        self.assertEqual(self._temporary_files(), [])

    def test_replacement_uses_complete_same_directory_payload(self) -> None:
        self.store.save(
            self.company_name,
            self.linkedin_url,
            website=self._website(url="https://old.example"),
        )
        previous_bytes = self.path.read_bytes()
        real_replace = os.replace
        replacements: list[dict] = []

        def inspect_then_replace(source: str, destination: str | Path) -> None:
            source_path = Path(source)
            self.assertEqual(source_path.parent, self.path.parent)
            self.assertEqual(Path(destination), self.path)
            replacements.append(json.loads(source_path.read_text(encoding="utf-8")))
            self.assertEqual(self.path.read_bytes(), previous_bytes)
            real_replace(source, destination)

        with patch(
            "job_source_agent.company_discovery_evidence_store.os.replace",
            side_effect=inspect_then_replace,
        ):
            self.store.save(
                self.company_name,
                self.linkedin_url,
                website=self._website(url="https://new.example"),
            )

        self.assertEqual(len(replacements), 1)
        self.assertEqual(
            replacements[0]["schema_version"],
            COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
        )
        self.assertEqual(self._temporary_files(), [])

    def test_concurrent_store_instances_do_not_lose_records(self) -> None:
        writer_count = 16
        barrier = Barrier(writer_count)

        def write_record(index: int) -> None:
            store = FilesystemCompanyDiscoveryEvidenceStore(
                self.path,
                max_age_seconds=60,
                clock=lambda: self.now[0],
            )
            barrier.wait()
            store.save(
                f"Company {index}",
                f"https://www.linkedin.com/company/company-{index}",
                website=self._website(
                    url=f"https://company-{index}.example",
                    evidence_url=f"https://www.linkedin.com/company/company-{index}",
                ),
            )

        with ThreadPoolExecutor(max_workers=writer_count) as executor:
            list(executor.map(write_record, range(writer_count)))

        self.assertEqual(len(self._payload()["records"]), writer_count)
        for index in range(writer_count):
            loaded = self.store.load(
                f"Company {index}",
                f"https://www.linkedin.com/company/company-{index}",
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.website.url, f"https://company-{index}.example")
        self.assertEqual(self._temporary_files(), [])


if __name__ == "__main__":
    unittest.main()
