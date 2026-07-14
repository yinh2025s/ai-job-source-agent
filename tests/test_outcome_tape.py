from __future__ import annotations

import copy
import unittest
from dataclasses import FrozenInstanceError, replace

from job_source_agent.evidence_scope import EvidenceScopeRef
from job_source_agent.outcome_tape import (
    OFFLINE_TAPE_DIVERGENCE,
    FetchFailureOutcomeTapeEntry,
    OutcomeTape,
    OutcomeTapeError,
    OutcomeTapeFetcher,
    PageOutcomeTapeEntry,
    outcome_records_sha256,
)
from job_source_agent.request_identity import build_request_identity
from job_source_agent.web import FetchError


STORE = "snapshot-store-001"
SCOPE_ID = "a" * 64
ATTEMPT = "capture-attempt-001"
EXECUTION = "b" * 64
STAGE = "career_discovery"
URL = "https://example.com/jobs?page=1"


class OutcomeTapeTests(unittest.TestCase):
    def page(self, ordinal: int, *, url: str = URL, html: str = "<p>jobs</p>"):
        return PageOutcomeTapeEntry(
            snapshot_store_id=STORE,
            scope_id=SCOPE_ID,
            capture_attempt_id=ATTEMPT,
            execution_fingerprint=EXECUTION,
            stage=STAGE,
            request_ordinal=ordinal,
            request=build_request_identity(url),
            page_url=url,
            html=html,
            final_url=url,
        )

    def failure(self, ordinal: int, *, url: str = URL):
        return FetchFailureOutcomeTapeEntry(
            snapshot_store_id=STORE,
            scope_id=SCOPE_ID,
            capture_attempt_id=ATTEMPT,
            execution_fingerprint=EXECUTION,
            stage=STAGE,
            request_ordinal=ordinal,
            request=build_request_identity(url),
            status=429,
            reason_code="RATE_LIMITED",
            retryable=True,
            message="HTTP 429 RATE_LIMITED",
        )

    def tape(self, entries):
        entries = tuple(entries)
        scope = EvidenceScopeRef(
            snapshot_store_id=STORE,
            scope_id=SCOPE_ID,
            capture_attempt_id=ATTEMPT,
            execution_fingerprint=EXECUTION,
            stage=STAGE,
            request_count=len(entries),
            records_sha256=outcome_records_sha256(entries),
            first_sequence=1 if entries else None,
            last_sequence=len(entries) if entries else None,
        )
        return OutcomeTape(scope, entries)

    def assert_divergence(self, context) -> FetchError:
        error = context.exception
        self.assertEqual(error.reason_code, OFFLINE_TAPE_DIVERGENCE)
        self.assertIs(error.retryable, False)
        return error

    def test_repeated_identity_page_failure_page_consumes_in_order(self):
        tape = self.tape(
            [self.page(1, html="first"), self.failure(2), self.page(3, html="third")]
        )
        fetcher = OutcomeTapeFetcher(tape)

        self.assertEqual(fetcher.fetch(URL).html, "first")
        with self.assertRaises(FetchError) as raised:
            fetcher.fetch(URL)
        error = raised.exception
        self.assertEqual(str(error), "HTTP 429 RATE_LIMITED")
        self.assertEqual(error.status, 429)
        self.assertEqual(error.reason_code, "RATE_LIMITED")
        self.assertIs(error.retryable, True)
        self.assertEqual(error.request_identity, build_request_identity(URL).as_dict())
        self.assertEqual(fetcher.fetch(URL).html, "third")
        self.assertIsNone(fetcher.finish())

    def test_entries_are_immutable_and_payload_round_trips_strictly(self):
        entry = self.page(1)
        with self.assertRaises(FrozenInstanceError):
            entry.html = "changed"
        tape = self.tape([entry])
        self.assertEqual(OutcomeTape.from_payload(tape.scope, tape.as_payload()), tape)

        payload = tape.as_payload()
        payload["unknown"] = True
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(tape.scope, payload)

        payload = tape.as_payload()
        payload["entries"][0]["page"]["unknown"] = True
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(tape.scope, payload)

    def test_rejects_cross_scope_entry(self):
        entry = replace(self.page(1), scope_id="c" * 64)
        scope = replace(
            self.tape([self.page(1)]).scope,
            records_sha256=outcome_records_sha256([entry]),
        )
        with self.assertRaisesRegex(OutcomeTapeError, "different evidence scope"):
            OutcomeTape(scope, [entry])

    def test_rejects_count_ordinal_and_digest_mismatch(self):
        entries = [self.page(1), self.page(2)]
        valid_scope = self.tape(entries).scope
        with self.assertRaisesRegex(OutcomeTapeError, "count"):
            OutcomeTape(replace(valid_scope, request_count=1), entries)
        with self.assertRaisesRegex(OutcomeTapeError, "ordinals"):
            OutcomeTape(valid_scope, [self.page(1), self.page(3)])
        with self.assertRaisesRegex(OutcomeTapeError, "digest"):
            OutcomeTape(replace(valid_scope, records_sha256="d" * 64), entries)

    def test_mismatched_extra_and_early_finish_raise_divergence(self):
        fetcher = OutcomeTapeFetcher(self.tape([self.page(1)]))
        with self.assertRaises(FetchError) as raised:
            fetcher.fetch("https://example.com/other")
        self.assert_divergence(raised)

        fetcher = OutcomeTapeFetcher(self.tape([self.page(1)]))
        with self.assertRaises(FetchError) as raised:
            fetcher.finish()
        self.assert_divergence(raised)

        fetcher = OutcomeTapeFetcher(self.tape([]))
        with self.assertRaises(FetchError) as raised:
            fetcher.fetch(URL)
        self.assert_divergence(raised)

    def test_mismatch_consumes_only_the_current_entry_and_never_searches_ahead(self):
        other = "https://example.com/other"
        fetcher = OutcomeTapeFetcher(self.tape([self.page(1), self.page(2, url=other)]))
        with self.assertRaises(FetchError):
            fetcher.fetch(other)
        self.assertEqual(fetcher.fetch(other).url, other)
        fetcher.finish()

    def test_rejects_unknown_kind_and_unsanitized_private_payloads(self):
        tape = self.tape([self.page(1)])
        payload = tape.as_payload()
        payload["entries"][0]["kind"] = "redirect"
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(tape.scope, payload)

        payload = tape.as_payload()
        payload["entries"][0]["request"]["sanitized_url"] = (
            "https://example.com/jobs?token=private"
        )
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(tape.scope, payload)

        payload = tape.as_payload()
        payload["entries"][0]["page"]["html"] = '{"sessionJWT":"private"}'
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(tape.scope, payload)

        failure_tape = self.tape([self.failure(1)])
        payload = copy.deepcopy(failure_tape.as_payload())
        payload["entries"][0]["failure"]["message"] = "Bearer very-private-token"
        with self.assertRaises(OutcomeTapeError):
            OutcomeTape.from_payload(failure_tape.scope, payload)

    def test_rejects_unknown_failure_reason_and_retryability_mismatch(self):
        with self.assertRaisesRegex(OutcomeTapeError, "unknown"):
            replace(self.failure(1), reason_code="NOT_REGISTERED")
        with self.assertRaisesRegex(OutcomeTapeError, "retryability"):
            replace(self.failure(1), retryable=False)


if __name__ == "__main__":
    unittest.main()
