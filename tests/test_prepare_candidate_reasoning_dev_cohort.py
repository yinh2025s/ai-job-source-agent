from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_candidate_reasoning_dev_cohort import prepare


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/evaluation/live100_fresh_cohort_20260718.json"
SELECTION = ROOT / "samples/evaluation/llm_candidate_reasoning_g_dev_selection_v1.json"
LABELS = ROOT / "samples/evaluation/llm_candidate_reasoning_g_dev_labels_v1.json"


class PrepareCandidateReasoningDevCohortTest(unittest.TestCase):
    def test_prepared_input_is_fixed_public_only_and_answer_free(self):
        payload = prepare(SOURCE, SELECTION)
        self.assertEqual(payload["record_count"], 18)
        self.assertEqual(
            {record["company_name"] for record in payload["records"]},
            {
                "Caesars Entertainment", "Versana", "NYC Department of Social Services",
                "City of Pharr, TX", "SDS International, Inc.", "Benefis Health System",
                "North Dakota Information Technology (NDIT)",
                "IMG (International Medical Group)", "Necessary Ventures", "Team Royal",
                "Rider Levett Bucknall RLB", "Hays + Sons", "City of Sioux Falls",
                "WICHITA COMPANY LIMITED", "Jushi Holdings Inc.", "State of Montana",
                "Ken Garff Automotive Group", "Systematic Business Consulting",
            },
        )
        self.assertEqual(len(payload["records_sha256"]), 64)
        allowed = {
            "record_id", "company_name", "linkedin_company_url",
            "linkedin_job_url", "job_title", "job_location",
        }
        for record in payload["records"]:
            self.assertEqual(set(record), allowed)
            self.assertFalse(any("website" in field or "expected" in field for field in record))

    def test_labels_are_separate_and_cover_exactly_the_frozen_ids(self):
        payload = prepare(SOURCE, SELECTION)
        labels = json.loads(LABELS.read_text(encoding="utf-8"))
        self.assertEqual(
            {record["record_id"] for record in payload["records"]},
            {record["record_id"] for record in labels["records"]},
        )
        self.assertNotIn("labels", payload)

    def test_generation_is_deterministic_and_rejects_invalid_selection(self):
        first = prepare(SOURCE, SELECTION)
        second = prepare(SOURCE, SELECTION)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as temporary:
            selection = Path(temporary) / "selection.json"
            selection.write_text(
                json.dumps({"schema_version": "1.0", "record_ids": [3, 3]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                prepare(SOURCE, selection)


if __name__ == "__main__":
    unittest.main()
