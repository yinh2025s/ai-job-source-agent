import unittest

from job_source_agent.job_board import DiscoveredJobBoard, JobBoard


class DiscoveredJobBoardTests(unittest.TestCase):
    def test_replay_safe_locator_round_trips_strict_payload(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.example.test/search-results",
                provider="phenom",
                identifier="public_tenant",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.example.test/careers",
        )

        payload = discovered.to_checkpoint_payload()

        self.assertEqual(DiscoveredJobBoard.from_checkpoint_payload(payload), discovered)

    def test_runtime_only_locator_is_not_serialized(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.example.test/careers",
                provider="ceipal",
                identifier='{"api_key":"do-not-persist"}',
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.example.test/careers",
        )

        self.assertIsNone(discovered.to_checkpoint_payload())

    def test_checkpoint_payload_rejects_unknown_fields_and_unsafe_urls(self):
        payload = {
            "board": {
                "url": "https://jobs.example.test/careers",
                "provider": "example",
                "identifier": "tenant",
                "replay_safe": True,
            },
            "detection_method": "page_evidence",
            "evidence_url": "https://jobs.example.test/careers",
        }
        cases = [
            {**payload, "raw_html": "<html>secret</html>"},
            {**payload, "detection_method": "trace_guess"},
            {**payload, "evidence_url": "http://jobs.example.test/careers"},
            {**payload, "evidence_url": "https://localhost/careers"},
            {**payload, "evidence_url": "https://127.0.0.1/careers"},
            {
                **payload,
                "board": {**payload["board"], "url": "https://user@jobs.example.test/careers"},
            },
        ]

        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    DiscoveredJobBoard.from_checkpoint_payload(invalid)

    def test_checkpoint_payload_rejects_unregistered_provider_and_sensitive_content(self):
        valid = {
            "board": {
                "url": "https://jobs.example.test/search-results",
                "provider": "phenom",
                "identifier": "public_tenant",
                "replay_safe": True,
            },
            "detection_method": "page_evidence",
            "evidence_url": "https://jobs.example.test/search-results",
        }
        cases = [
            {**valid, "board": {**valid["board"], "provider": "unknown_provider"}},
            {**valid, "evidence_url": "https://other.example.test/search-results"},
            {**valid, "evidence_url": "https://jobs.example.test/search-results?token=secret"},
            {**valid, "evidence_url": "https://jobs.example.test/search-results?_csrf=secret"},
            {**valid, "evidence_url": "https://jobs.example.test/search-results?client%5Fsecret=secret"},
            {**valid, "evidence_url": "https://jobs.example.test/search-results?id_token=secret"},
            {**valid, "evidence_url": "https://jobs.example.test/search-results?sessionid=secret"},
            {**valid, "board": {**valid["board"], "identifier": "<html>secret</html>"}},
            {**valid, "board": {**valid["board"], "identifier": "Bearer abcdefghijk"}},
        ]

        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    DiscoveredJobBoard.from_checkpoint_payload(invalid)

    def test_checkpoint_payload_accepts_provider_bound_public_locators(self):
        sitecore_identity = {
            "origin": "https://careers.example.test",
            "path": "/jobs",
            "site": "careers",
            "language": "en",
            "country": "us",
            "brand": "Example",
            "config": {
                "baseSearchQuery": "site:careers",
                "filtersToDisplay": "Location",
                "brandFromDictionary": "Example",
            },
        }
        import json

        payloads = [
            {
                "url": "https://jobs.example.test/en_US/External/SearchJobs",
                "provider": "avature",
                "identifier": "jobs.example.test|en_US|External",
            },
            {
                "url": "https://example.eightfold.ai/careers",
                "provider": "eightfold",
                "identifier": "example.com",
            },
            {
                "url": "https://jobs.example.test/careers",
                "provider": "greenhouse",
                "identifier": "custom:jobs.example.test",
            },
            {
                "url": "https://jobs.example.test/jobs/search?q=engineer",
                "provider": "icims",
                "identifier": "jobs.example.test",
            },
            {
                "url": "https://jobs.example.test/search-results",
                "provider": "phenom",
                "identifier": "tenant_123",
            },
            {
                "url": "https://careers.example.test/jobs",
                "provider": "sitecore_next_jobs",
                "identifier": json.dumps(
                    sitecore_identity,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            {
                "url": "https://jobs.example.test/",
                "provider": "talemetry",
                "identifier": '{"career_site_id":"123","host":"jobs.example.test"}',
            },
        ]

        for board_payload in payloads:
            payload = {
                "board": {**board_payload, "replay_safe": True},
                "detection_method": "page_evidence",
                "evidence_url": board_payload["url"],
            }
            with self.subTest(provider=board_payload["provider"]):
                restored = DiscoveredJobBoard.from_checkpoint_payload(payload)
                self.assertEqual(restored.board.provider, board_payload["provider"])


if __name__ == "__main__":
    unittest.main()
