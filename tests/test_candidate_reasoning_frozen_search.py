from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_source_agent.candidate_reasoning_contracts import CandidateEvidence, SearchQuerySpec
from job_source_agent.candidate_reasoning_frozen_search import (
    FilesystemFrozenQueryStore,
    FrozenQueryFixtureError,
    FrozenQueryResponse,
    RecordingFrozenCandidateSearchBackend,
    ReplayFrozenCandidateSearchBackend,
    frozen_query_digest,
)


class CountingSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[SearchQuerySpec, str, float]] = []

    def search(self, query, *, query_id, remaining_seconds):
        self.calls.append((query, query_id, remaining_seconds))
        return (
            CandidateEvidence(
                "candidate-one",
                "https://careers.example.test/one",
                "Example careers",
                "Public result snippet",
                "test-search",
                query_id,
                1,
            ),
        )


class FrozenCandidateSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "fixtures"
        self.store = FilesystemFrozenQueryStore(self.root)
        self.query = SearchQuerySpec("Example Company careers", "career_site")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_first_capture_then_same_query_reuses_fixture_without_delegate(self):
        delegate = CountingSearch()
        backend = RecordingFrozenCandidateSearchBackend(delegate, self.store)

        first = backend.search(self.query, query_id="query-first", remaining_seconds=4.5)
        second = backend.search(self.query, query_id="query-second", remaining_seconds=1.0)

        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(delegate.calls[0][2], 4.5)
        self.assertEqual(first[0].candidate_id, second[0].candidate_id)
        self.assertEqual(first[0].url, second[0].url)
        self.assertEqual(first[0].query_id, "query-first")
        self.assertEqual(second[0].query_id, "query-second")

    def test_same_query_with_different_id_preserves_public_candidate_identity(self):
        delegate = CountingSearch()
        backend = RecordingFrozenCandidateSearchBackend(delegate, self.store)
        expected = backend.search(self.query, query_id="first-query", remaining_seconds=2.0)

        replayed = ReplayFrozenCandidateSearchBackend(self.store).search(
            self.query, query_id="second-query", remaining_seconds=2.0
        )

        self.assertEqual(
            [(item.candidate_id, item.url, item.title, item.snippet, item.source, item.rank) for item in replayed],
            [(item.candidate_id, item.url, item.title, item.snippet, item.source, item.rank) for item in expected],
        )
        self.assertEqual(replayed[0].query_id, "second-query")

    def test_missing_and_corrupt_fixtures_fail_closed(self):
        replay = ReplayFrozenCandidateSearchBackend(self.store)
        with self.assertRaises(FrozenQueryFixtureError):
            replay.search(self.query, query_id="missing-query", remaining_seconds=1.0)
        self.assertEqual(replay.missing_query_digests, {frozen_query_digest(self.query)})

        self.root.mkdir()
        path = self.root / f"{frozen_query_digest(self.query)}.json"
        path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(FrozenQueryFixtureError):
            self.store.load(self.query)

    def test_symlink_fixture_is_rejected(self):
        self.root.mkdir()
        target = Path(self.temporary.name) / "outside.json"
        target.write_text("{}", encoding="utf-8")
        fixture = self.root / f"{frozen_query_digest(self.query)}.json"
        fixture.symlink_to(target)

        with self.assertRaisesRegex(FrozenQueryFixtureError, "regular file"):
            self.store.load(self.query)

    def test_mutated_duplicate_is_rejected(self):
        first = FrozenQueryResponse.capture(
            self.query, query_id="query-one", candidates=self._candidates("first title", "query-one")
        )
        self.store.save(first)
        mutated = FrozenQueryResponse.capture(
            self.query, query_id="query-two", candidates=self._candidates("changed title", "query-two")
        )
        with self.assertRaises(FrozenQueryFixtureError):
            self.store.save(mutated)

    def test_replay_never_calls_live_backend_and_detects_unconsumed_fixtures(self):
        delegate = CountingSearch()
        recorder = RecordingFrozenCandidateSearchBackend(delegate, self.store)
        recorder.search(self.query, query_id="recorded", remaining_seconds=3.0)
        other = SearchQuerySpec("Example Company jobs", "provider_site")
        recorder.search(other, query_id="other", remaining_seconds=3.0)

        replay = ReplayFrozenCandidateSearchBackend(self.store)
        replay.search(self.query, query_id="replayed", remaining_seconds=1.0)
        self.assertEqual(len(delegate.calls), 2)
        self.assertEqual(replay.consumed_query_digests, {frozen_query_digest(self.query)})
        with self.assertRaises(FrozenQueryFixtureError):
            replay.assert_all_consumed()
        replay.search(other, query_id="other-replayed", remaining_seconds=1.0)
        replay.assert_all_consumed()

    def test_deterministic_file_bytes(self):
        response = FrozenQueryResponse.capture(
            self.query, query_id="query-one", candidates=self._candidates("first title", "query-one")
        )
        self.store.save(response)
        path = self.root / f"{response.query_digest}.json"
        first = path.read_bytes()
        self.store.save(response)
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(first, first.rstrip(b"\n") + b"\n")

    def _candidates(self, title: str, query_id: str) -> tuple[CandidateEvidence, ...]:
        return (
            CandidateEvidence(
                "candidate-one",
                "https://careers.example.test/one",
                title,
                "Public result snippet",
                "test-search",
                query_id,
                1,
            ),
        )


if __name__ == "__main__":
    unittest.main()
