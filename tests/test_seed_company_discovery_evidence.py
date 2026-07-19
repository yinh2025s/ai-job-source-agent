from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)
from scripts.seed_company_discovery_evidence import seed_company_discovery_evidence


OBSERVED_AT = 1_750_000_000.0


class SeedCompanyDiscoveryEvidenceTests(unittest.TestCase):
    def test_seeds_only_verified_layers_and_emits_auditable_manifest(self):
        result, trace = _verified_records()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, [result], [trace])
            record = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            ).load("Example Co", "https://www.linkedin.com/company/example-co")

            self.assertIsNotNone(record)
            self.assertEqual(record.website.url, "https://example.com/")
            self.assertEqual(record.career.url, "https://example.com/careers")
            self.assertEqual(len(record.provider_boards), 1)
            self.assertEqual(record.provider_boards[0].tenant, "example")
            self.assertEqual(
                manifest["authority"],
                "discovery_candidates_requiring_current_revalidation",
            )
            self.assertEqual(
                manifest["summary"]["seeded_layer_counts"],
                {"career": 1, "provider_board": 1, "website": 1},
            )
            self.assertEqual(len(manifest["inputs"]["results"]["sha256"]), 64)
            serialized = json.dumps(manifest)
            self.assertNotIn("open_position_url", serialized)
            self.assertNotIn("secret opening", serialized)
            self.assertEqual(
                manifest["excluded_data_classes"],
                [
                    "cookies_and_tokens",
                    "html_and_response_bodies",
                    "inventory",
                    "exact_opening",
                ],
            )

    def test_rejects_failed_mismatched_and_unverified_urls(self):
        failed, failed_trace = _verified_records(company="Failed")
        _stage(failed, "website_resolution")["status"] = "failed"
        mismatched, mismatched_trace = _verified_records(company="Mismatch")
        _stage(mismatched, "career_discovery")["evidence"][0]["url"] = (
            "https://attacker.example/careers"
        )
        unverified, unverified_trace = _verified_records(company="Unverified")
        unverified_trace["trace"]["stages"]["job_board_discovery"][
            "relationship_evidence"
        ]["verified"] = False
        invalid, invalid_trace = _verified_records(company="Invalid")
        invalid["company_website_url"] = "https://user:pass@example.com/"
        _stage(invalid, "website_resolution")["evidence"][0]["url"] = (
            invalid["company_website_url"]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(
                root,
                [failed, mismatched, unverified, invalid],
                [failed_trace, mismatched_trace, unverified_trace, invalid_trace],
            )
            store = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            )

            self.assertIsNone(
                store.load("Failed", "https://www.linkedin.com/company/failed")
            )
            mismatch = store.load(
                "Mismatch", "https://www.linkedin.com/company/mismatch"
            )
            self.assertIsNotNone(mismatch.website)
            self.assertIsNone(mismatch.career)
            unverified_record = store.load(
                "Unverified", "https://www.linkedin.com/company/unverified"
            )
            self.assertIsNotNone(unverified_record.career)
            self.assertEqual(unverified_record.provider_boards, ())
            self.assertIsNone(
                store.load("Invalid", "https://www.linkedin.com/company/invalid")
            )
            self.assertEqual(
                manifest["summary"]["rejection_counts"],
                {
                    "career_not_verified": 1,
                    "provider_relationship_unverified": 1,
                    "website_not_verified": 1,
                    "website_url_rejected": 1,
                },
            )

    def test_is_idempotent_for_repeated_records_and_repeated_runs(self):
        first, first_trace = _verified_records(job_url="https://linkedin/jobs/1")
        second, second_trace = _verified_records(job_url="https://linkedin/jobs/2")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root, [first, second], [first_trace, second_trace])
            first_store_bytes = (root / "store.json").read_bytes()
            manifest = _seed(root, [first, second], [first_trace, second_trace])

            self.assertEqual((root / "store.json").read_bytes(), first_store_bytes)
            self.assertEqual(manifest["summary"]["seeded_identity_count"], 1)
            self.assertEqual(
                manifest["summary"]["seeded_layer_counts"],
                {"career": 1, "provider_board": 1, "website": 1},
            )
            payload = json.loads((root / "store.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["records"]), 1)
            only_record = next(iter(payload["records"].values()))
            self.assertEqual(len(only_record["provider_boards"]), 1)

    def test_seeds_gucci_like_verified_result_identity_without_trace_relationship(self):
        result, trace = _verified_records(company="Gucci")
        trace["trace"]["stages"]["job_board_discovery"].pop(
            "relationship_evidence"
        )
        result["identity_assertion"] = _verified_provider_identity()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, [result], [trace])
            record = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            ).load("Gucci", "https://www.linkedin.com/company/gucci")

            self.assertIsNotNone(record)
            self.assertEqual(len(record.provider_boards), 1)
            self.assertEqual(
                record.provider_boards[0].canonical_board_url,
                "https://jobs.ashbyhq.com/example",
            )
            self.assertEqual(
                record.provider_boards[0].verification_method,
                "verified_first_party_provider_page",
            )
            self.assertEqual(manifest["summary"]["rejection_count"], 0)

    def test_seeds_gucci_not_applicable_result_identity_without_trace_relationship(self):
        result, trace = _verified_records(company="Gucci .120")
        trace["trace"]["stages"]["job_board_discovery"].pop(
            "relationship_evidence"
        )
        result["identity_assertion"] = _verified_provider_identity(
            verdict="not_applicable"
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, [result], [trace])
            record = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            ).load("Gucci .120", "https://www.linkedin.com/company/gucci-.120")

            self.assertIsNotNone(record)
            self.assertEqual(len(record.provider_boards), 1)
            self.assertEqual(manifest["summary"]["rejection_count"], 0)

    def test_trace_relationship_evidence_remains_preferred_over_result_identity(self):
        result, trace = _verified_records(company="Trace preferred")
        result["identity_assertion"] = _verified_provider_identity()
        trace["trace"]["stages"]["job_board_discovery"]["relationship_evidence"][
            "verified"
        ] = False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, [result], [trace])
            record = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            ).load("Trace preferred", "https://www.linkedin.com/company/trace-preferred")

            self.assertIsNotNone(record)
            self.assertEqual(record.provider_boards, ())
            self.assertEqual(
                manifest["summary"]["rejection_counts"],
                {"provider_relationship_unverified": 1},
            )

    def test_rejects_unverified_incomplete_mismatched_and_unsupported_result_identity(self):
        unverified, unverified_trace = _verified_records(company="Unverified identity")
        incomplete, incomplete_trace = _verified_records(company="Incomplete identity")
        mismatched, mismatched_trace = _verified_records(company="Mismatched identity")
        unsupported, unsupported_trace = _verified_records(company="Unsupported identity")
        records = (unverified, incomplete, mismatched, unsupported)
        traces = (unverified_trace, incomplete_trace, mismatched_trace, unsupported_trace)
        for result, trace in zip(records, traces):
            trace["trace"]["stages"]["job_board_discovery"].pop(
                "relationship_evidence"
            )
            result["identity_assertion"] = _verified_provider_identity()
        unverified["identity_assertion"]["provider"]["relationship_verified"] = False
        incomplete["identity_assertion"]["provider"].pop("evidence_url")
        mismatched["identity_assertion"]["provider"]["tenant"] = "other-tenant"
        unsupported["identity_assertion"]["provider"]["verification_method"] = (
            "tenant_name_match"
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, list(records), list(traces))
            store = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            )

            for company in (
                "Unverified identity",
                "Incomplete identity",
                "Mismatched identity",
                "Unsupported identity",
            ):
                record = store.load(
                    company,
                    f"https://www.linkedin.com/company/{company.casefold().replace(' ', '-')}",
                )
                self.assertIsNotNone(record)
                self.assertEqual(record.provider_boards, ())
            self.assertEqual(
                manifest["summary"]["rejection_counts"],
                {
                    "provider_identity_incomplete_or_unsupported": 2,
                    "provider_identity_mismatch": 1,
                    "provider_relationship_unverified": 1,
                },
            )

    def test_rejects_not_applicable_result_identity_without_verified_hiring_or_clean_codes(self):
        unverified_hiring, unverified_hiring_trace = _verified_records(
            company="Unverified hiring"
        )
        failed_identity, failed_identity_trace = _verified_records(
            company="Failed identity"
        )
        records = (unverified_hiring, failed_identity)
        traces = (unverified_hiring_trace, failed_identity_trace)
        for result, trace in zip(records, traces):
            trace["trace"]["stages"]["job_board_discovery"].pop(
                "relationship_evidence"
            )
            result["identity_assertion"] = _verified_provider_identity(
                verdict="not_applicable"
            )
        unverified_hiring["identity_assertion"]["hiring"]["verified"] = False
        failed_identity["identity_assertion"]["failure_codes"] = ["OPENING_NOT_FOUND"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _seed(root, list(records), list(traces))
            store = FilesystemCompanyDiscoveryEvidenceStore(
                root / "store.json", clock=lambda: OBSERVED_AT
            )

            for company in ("Unverified hiring", "Failed identity"):
                record = store.load(
                    company,
                    f"https://www.linkedin.com/company/{company.casefold().replace(' ', '-')}",
                )
                self.assertIsNotNone(record)
                self.assertEqual(record.provider_boards, ())
            self.assertEqual(
                manifest["summary"]["rejection_counts"],
                {"provider_relationship_unverified": 2},
            )


def _seed(root: Path, results: list[dict], traces: list[dict]) -> dict:
    results_path = root / "results.json"
    trace_path = root / "trace.json"
    results_path.write_text(json.dumps(results), encoding="utf-8")
    trace_path.write_text(json.dumps(traces), encoding="utf-8")
    return seed_company_discovery_evidence(
        results_path=results_path,
        trace_path=trace_path,
        store_path=root / "store.json",
        manifest_path=root / "manifest.json",
        source_run="fixture-run",
        observed_at=OBSERVED_AT,
        clock=lambda: OBSERVED_AT,
    )


def _verified_records(
    *, company: str = "Example Co", job_url: str = "https://linkedin/jobs/1"
) -> tuple[dict, dict]:
    slug = company.casefold().replace(" ", "-")
    linkedin_url = f"https://www.linkedin.com/company/{slug}"
    result = {
        "company_name": company,
        "linkedin_company_url": linkedin_url,
        "linkedin_job_url": job_url,
        "company_website_url": "https://example.com/",
        "career_page_url": "https://example.com/careers",
        "job_list_page_url": "https://jobs.ashbyhq.com/example",
        "open_position_url": "https://jobs.ashbyhq.com/example/secret-opening",
        "inventory": {"html": "<html>do not migrate</html>", "token": "secret"},
        "stages": [
            _success_stage("website_resolution", "company_website_url", "https://example.com/"),
            _success_stage("career_discovery", "career_page_url", "https://example.com/careers"),
            {
                **_success_stage(
                    "job_board_discovery",
                    "job_list_page_url",
                    "https://jobs.ashbyhq.com/example",
                ),
                "provider": "ashby",
            },
            _success_stage(
                "opening_match",
                "open_position_url",
                "https://jobs.ashbyhq.com/example/secret-opening",
            ),
        ],
    }
    trace = {
        **result,
        "trace": {
            "cookies": "do not migrate",
            "stages": {
                "website_resolution": {"selected": {"reasons": ["homepage verified"]}},
                "career_discovery": {"selected": {"origin": "verified_homepage_navigation"}},
                "job_board_discovery": {
                    "relationship_evidence": {
                        "verified": True,
                        "provider": "ashby",
                        "tenant": "example",
                        "evidence_type": "first_party_handoff",
                        "evidence_url": "https://example.com/careers",
                    }
                },
            },
        },
    }
    return result, trace


def _success_stage(name: str, field: str, url: str) -> dict:
    return {
        "stage": name,
        "status": "success",
        "evidence": [{"field": field, "url": url}],
    }


def _verified_provider_identity(*, verdict: str = "verified") -> dict:
    return {
        "verdict": verdict,
        "hiring": {"verified": True},
        "failure_codes": [],
        "provider": {
            "provider": "ashby",
            "tenant": "example",
            "canonical_board_url": "https://jobs.ashbyhq.com/example",
            "evidence_url": "https://example.com/careers",
            "verification_method": "verified_first_party_provider_page",
            "relationship_verified": True,
        },
    }


def _stage(record: dict, name: str) -> dict:
    return next(item for item in record["stages"] if item["stage"] == name)


if __name__ == "__main__":
    unittest.main()
