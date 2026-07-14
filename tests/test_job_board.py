import unittest

from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio


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
            {
                "url": "https://jobs.smartrecruiters.com/Visa",
                "provider": "smartrecruiters",
                "identifier": "Visa",
            },
            {
                "url": "https://visa.wd1.myworkdayjobs.com/en-US/Visa_Careers",
                "provider": "workday",
                "identifier": "visa/Visa_Careers",
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

    def test_checkpoint_payload_rejects_mismatched_public_ats_locators(self):
        invalid_boards = (
            {
                "url": "https://jobs.smartrecruiters.com/Other",
                "provider": "smartrecruiters",
                "identifier": "Visa",
            },
            {
                "url": "https://attacker.wd1.myworkdayjobs.com/Visa_Careers",
                "provider": "workday",
                "identifier": "visa/Visa_Careers",
            },
            {
                "url": "https://visa.wd1.myworkdayjobs.com/Other_Careers",
                "provider": "workday",
                "identifier": "visa/Visa_Careers",
            },
        )
        for board_payload in invalid_boards:
            payload = {
                "board": {**board_payload, "replay_safe": True},
                "detection_method": "url_evidence",
                "evidence_url": board_payload["url"],
            }
            with self.subTest(provider=board_payload["provider"]):
                with self.assertRaisesRegex(ValueError, "not replay-safe"):
                    DiscoveredJobBoard.from_checkpoint_payload(payload)


class JobBoardPortfolioTests(unittest.TestCase):
    @staticmethod
    def _board(
        url="https://jobs.example.test/search-results",
        provider="phenom",
        identifier="PUBLIC-TENANT",
        replay_safe=True,
    ):
        return DiscoveredJobBoard(
            board=JobBoard(
                url=url,
                provider=provider,
                identifier=identifier,
                replay_safe=replay_safe,
            ),
            detection_method="page_evidence",
            evidence_url=url,
        )

    def test_replay_safe_portfolio_round_trips_in_priority_order(self):
        first = self._board()
        second = self._board(
            url="https://jobs.example.test/general/search-results",
            identifier="GENERAL-TENANT",
        )
        portfolio = JobBoardPortfolio(
            boards=(first, second),
            eligible_set_complete=True,
        )

        payload = portfolio.to_checkpoint_payload()

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(JobBoardPortfolio.from_checkpoint_payload(payload), portfolio)
        self.assertIs(portfolio.primary, first)

    def test_runtime_only_member_makes_whole_portfolio_non_persistable(self):
        portfolio = JobBoardPortfolio(
            boards=(self._board(), self._board(
                url="https://jobs.example.test/runtime",
                identifier='{"api_key":"runtime-only"}',
                replay_safe=False,
            )),
            eligible_set_complete=False,
        )

        self.assertIsNone(portfolio.to_checkpoint_payload())

    def test_portfolio_rejects_empty_oversized_and_non_tuple_membership(self):
        board = self._board()
        invalid_memberships = (
            (),
            tuple(board for _ in range(9)),
            [board],
        )
        for boards in invalid_memberships:
            with self.subTest(size=len(boards), type=type(boards).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    JobBoardPortfolio(boards=boards, eligible_set_complete=True)

    def test_portfolio_rejects_duplicate_public_board_identity(self):
        first = self._board()
        duplicate = self._board(identifier="OTHER-TENANT")

        with self.assertRaisesRegex(ValueError, "duplicate public board identity"):
            JobBoardPortfolio(
                boards=(first, duplicate),
                eligible_set_complete=True,
            )

    def test_portfolio_identity_preserves_case_sensitive_board_path(self):
        upper = self._board(
            url="https://jobs.example.test/US/search-results",
            identifier="UPPER",
        )
        lower = self._board(
            url="https://jobs.example.test/us/search-results",
            identifier="LOWER",
        )

        portfolio = JobBoardPortfolio(
            boards=(upper, lower),
            eligible_set_complete=True,
        )

        self.assertEqual(portfolio.boards, (upper, lower))

    def test_portfolio_payload_rejects_unknown_schema_and_fields(self):
        valid = JobBoardPortfolio(
            boards=(self._board(),),
            eligible_set_complete=True,
        ).to_checkpoint_payload()
        assert valid is not None
        invalid_payloads = (
            {**valid, "schema_version": "old"},
            {**valid, "raw_html": "secret"},
            {**valid, "eligible_set_complete": 1},
            {**valid, "boards": []},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises((TypeError, ValueError)):
                    JobBoardPortfolio.from_checkpoint_payload(payload)


if __name__ == "__main__":
    unittest.main()
