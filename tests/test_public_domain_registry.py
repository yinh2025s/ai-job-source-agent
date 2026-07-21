from __future__ import annotations

import csv
import hashlib
import io
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_source_agent.public_domain_registry import (
    CISA_PUBLIC_DOMAIN_CSV_URL,
    PUBLIC_DOMAIN_REGISTRY_SOURCE,
    PUBLIC_DOMAIN_REGISTRY_SCHEMA_VERSION,
    PublicDomainRegistry,
)


FIXTURES = Path(__file__).parent / "fixtures" / "public_domain_registry"
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
RETRIEVED_AT = NOW - timedelta(hours=1)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load(content: bytes | None = None, **kwargs) -> PublicDomainRegistry:
    return PublicDomainRegistry.from_csv_bytes(
        content if content is not None else fixture("valid.csv"),
        retrieved_at=kwargs.pop("retrieved_at", RETRIEVED_AT),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def make_csv(rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(PublicDomainRegistry.EXPECTED_COLUMNS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


class PublicDomainRegistryTests(unittest.TestCase):
    def test_four_government_inputs_produce_only_exact_source_backed_candidates(self) -> None:
        registry = load()
        cases = (
            ("City of Pharr", "Texas", "Pharr", ("pharr-tx.gov",)),
            ("City of Sioux Falls", "SD", "Sioux Falls", ("siouxfalls.gov",)),
            ("City of College Station", "TX", "College Station", ("cstx.gov",)),
            (
                "State of Montana",
                "Montana",
                None,
                ("montana.gov", "mt.gov", "mtcloudcommunications.gov"),
            ),
        )

        for organization, state, city, expected_domains in cases:
            with self.subTest(organization=organization):
                result = registry.query(organization, state=state, city=city)
                self.assertEqual(result.status, "candidates")
                self.assertEqual(
                    tuple(candidate.domain_name for candidate in result.candidates),
                    expected_domains,
                )
                self.assertEqual(
                    tuple(candidate.url for candidate in result.candidates),
                    tuple(f"https://{domain}/" for domain in expected_domains),
                )

    def test_query_requires_exact_organization_state_type_and_city(self) -> None:
        registry = load()
        rejected = (
            ("City of Pharr", "AZ", "Pharr"),
            ("City of Pharr", "TX", "College Station"),
            ("Pharr", "TX", "Pharr"),
            ("County of Pharr", "TX", "Pharr"),
            ("State of Montana Department of Administration", "MT", None),
            ("United States Department of State", "DC", "Washington"),
        )

        for organization, state, city in rejected:
            with self.subTest(organization=organization, state=state, city=city):
                result = registry.query(organization, state=state, city=city)
                self.assertEqual(result.candidates, ())
                self.assertIn(result.status, {"not_found", "unsupported_identity"})

    def test_wrong_state_wrong_type_and_state_gov_collisions_do_not_survive(self) -> None:
        registry = load()

        city_result = registry.query("City of Pharr", state="TX", city="Pharr")
        state_result = registry.query("State of Montana", state="MT")
        all_candidates = city_result.candidates + state_result.candidates

        self.assertEqual(tuple(item.state for item in city_result.candidates), ("TX",))
        self.assertEqual(tuple(item.domain_type for item in city_result.candidates), ("City",))
        self.assertNotIn("pharr-nm.gov", {item.domain_name for item in all_candidates})
        self.assertNotIn("pharrcounty.gov", {item.domain_name for item in all_candidates})
        self.assertNotIn("pharrschools.gov", {item.domain_name for item in all_candidates})
        self.assertNotIn("state.gov", {item.domain_name for item in all_candidates})
        self.assertNotIn("montana-department.gov", {item.domain_name for item in all_candidates})

    def test_normalization_is_conservative_but_format_insensitive(self) -> None:
        result = load().query("  CITY--OF   PHARR ", state=" texas ", city=" PHARR ")

        self.assertEqual(result.status, "candidates")
        self.assertEqual(
            tuple(item.domain_name for item in result.candidates),
            ("pharr-tx.gov",),
        )

    def test_normalization_does_not_transliterate_distinct_unicode_names(self) -> None:
        content = make_csv(
            [["sao-jose.gov", "City", "City of Sao Jose", "", "Sao Jose", "CA", "(blank)"]]
        )

        result = load(content).query("City of São Jose", state="CA", city="São Jose")

        self.assertEqual(result.status, "unsupported_identity")
        self.assertEqual(result.candidates, ())

    def test_city_identity_requires_city_and_state_identity_does_not_use_capital_as_gate(self) -> None:
        registry = load()

        city_result = registry.query("City of Pharr", state="TX")
        state_result = registry.query("State of Montana", state="MT", city="Not Helena")

        self.assertEqual(city_result.status, "unsupported_identity")
        self.assertEqual(city_result.reason, "city_required")
        self.assertEqual(len(state_result.candidates), 3)

    def test_candidates_have_complete_non_authorizing_provenance_without_contact_email(self) -> None:
        content = fixture("valid.csv")
        registry = load(content)

        result = registry.query("City of Pharr", state="TX", city="Pharr")
        candidate = result.candidates[0]
        payload = asdict(candidate)

        self.assertEqual(candidate.provenance.source, PUBLIC_DOMAIN_REGISTRY_SOURCE)
        self.assertEqual(
            candidate.provenance.schema_version,
            PUBLIC_DOMAIN_REGISTRY_SCHEMA_VERSION,
        )
        self.assertEqual(candidate.provenance.source_url, CISA_PUBLIC_DOMAIN_CSV_URL)
        self.assertEqual(candidate.provenance.dataset_sha256, hashlib.sha256(content).hexdigest())
        self.assertRegex(candidate.provenance.row_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(candidate.provenance.row_number, 2)
        self.assertEqual(candidate.provenance.retrieved_at, "2026-07-21T11:00:00Z")
        self.assertNotIn("success", result.status)
        self.assertNotIn("email", repr(payload).casefold())
        self.assertNotIn("private-contact", repr(registry).casefold())

    def test_malformed_schema_changed_empty_and_invalid_utf8_datasets_fail_closed(self) -> None:
        invalid_datasets = (
            (fixture("malformed.csv"), "malformed_csv"),
            (fixture("schema_changed.csv"), "schema_mismatch"),
            (b"", "empty_dataset"),
            (b"\xff\xfe", "malformed_csv"),
        )

        for content, reason in invalid_datasets:
            with self.subTest(reason=reason):
                registry = load(content)
                result = registry.query("City of Pharr", state="TX", city="Pharr")
                self.assertFalse(registry.available)
                self.assertEqual(registry.rows, ())
                self.assertEqual(registry.load_error, reason)
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.candidates, ())

    def test_stale_future_untrusted_and_oversized_datasets_fail_closed(self) -> None:
        cases = (
            ({"retrieved_at": NOW - timedelta(days=3)}, "stale_dataset"),
            ({"retrieved_at": NOW + timedelta(minutes=6)}, "future_dataset"),
            ({"source_url": "https://example.gov/current-full.csv"}, "untrusted_source"),
        )
        for kwargs, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(load(**kwargs).load_error, reason)

        oversized = b"x" * (32 * 1024 * 1024 + 1)
        self.assertEqual(load(oversized).load_error, "dataset_too_large")

    def test_duplicate_domain_collision_invalid_domain_and_extra_cells_fail_closed(self) -> None:
        duplicate_domain = make_csv(
            [
                [
                    "shared.gov",
                    "City",
                    "City of Alpha",
                    "",
                    "Alpha",
                    "TX",
                    "a@example.gov",
                ],
                [
                    "shared.gov",
                    "City",
                    "City of Beta",
                    "",
                    "Beta",
                    "TX",
                    "b@example.gov",
                ],
            ]
        )
        invalid_domain = make_csv(
            [
                [
                    "not-a-gov.example",
                    "City",
                    "City of Alpha",
                    "",
                    "Alpha",
                    "TX",
                    "(blank)",
                ]
            ]
        )
        extra_cell = (
            ",".join(PublicDomainRegistry.EXPECTED_COLUMNS).encode("utf-8")
            + b"\nalpha.gov,City,City of Alpha,,Alpha,TX,(blank),extra\n"
        )

        self.assertEqual(load(duplicate_domain).load_error, "domain_collision")
        self.assertEqual(load(invalid_domain).load_error, "invalid_domain")
        self.assertEqual(load(extra_cell).load_error, "malformed_csv")

    def test_candidate_set_over_limit_fails_closed_instead_of_truncating(self) -> None:
        rows = [
            [
                f"montana-{index}.gov",
                "State or territory",
                "State of Montana",
                "",
                "Helena",
                "MT",
                "(blank)",
            ]
            for index in range(6)
        ]
        registry = load(make_csv(rows), candidate_limit=5)

        result = registry.query("State of Montana", state="MT")

        self.assertTrue(registry.available)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason, "candidate_limit_exceeded")
        self.assertEqual(result.candidates, ())

    def test_invalid_runtime_configuration_cannot_expand_safety_bounds(self) -> None:
        for candidate_limit in (0, 9, True):
            with self.subTest(candidate_limit=candidate_limit):
                registry = load(candidate_limit=candidate_limit)
                self.assertFalse(registry.available)
                self.assertEqual(registry.load_error, "invalid_candidate_limit")

        naive = datetime(2026, 7, 21, 11, 0)
        self.assertEqual(load(retrieved_at=naive).load_error, "invalid_retrieval_time")


if __name__ == "__main__":
    unittest.main()
