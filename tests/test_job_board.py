import json
import unittest

from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.providers.peoplesoft import PeopleSoftAdapter


class DiscoveredJobBoardTests(unittest.TestCase):
    def test_adp_public_locator_round_trips_only_with_bound_tenant_identity(self):
        cid = "6d761223-04f6-4d39-a498-276f6ca9389f"
        url = (
            "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
            f"recruitment.html?cid={cid}&ccId=19000101_000001&type=MP&"
            "lang=en_US&selectedMenuKey=CurrentOpenings"
        )
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url=url,
                provider="adp",
                identifier=f"wfn|{cid}|19000101_000001|en_US",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url=url,
        )

        payload = discovered.to_checkpoint_payload()

        self.assertEqual(DiscoveredJobBoard.from_checkpoint_payload(payload), discovered)
        for invalid_url in (
            url + "&token=secret",
            url + f"&cid={cid}",
            url.replace("selectedMenuKey=CurrentOpenings", "selectedMenuKey=Other"),
        ):
            with self.subTest(invalid_url=invalid_url):
                invalid = DiscoveredJobBoard(
                    board=JobBoard(
                        url=invalid_url,
                        provider="adp",
                        identifier=discovered.board.identifier,
                        replay_safe=True,
                    ),
                    detection_method="page_evidence",
                    evidence_url=invalid_url,
                )
                with self.assertRaises(ValueError):
                    invalid.to_checkpoint_payload()

    def test_verified_first_party_action_is_runtime_only_portfolio_evidence(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://opaque-hiring.example/jobs",
                provider="generic",
            ),
            detection_method="verified_first_party_action",
            evidence_url="https://opaque-hiring.example/jobs",
            relationship_evidence_url="https://acme.example/careers",
        )

        portfolio = JobBoardPortfolio((discovered,), eligible_set_complete=True)

        self.assertEqual(portfolio.primary, discovered)
        self.assertIsNone(discovered.to_checkpoint_payload())

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
                "url": "https://careers.example.test/en/annonces",
                "provider": "digitalrecruiters",
                "identifier": (
                    '{"api_base":"https://api.digitalrecruiters.com/public/v1",'
                    '"board_url":"https://careers.example.test/en/annonces",'
                    '"locale":"en","tenant":"careers.example.test"}'
                ),
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
                "url": "https://example-search.app.loxo.co/example-search",
                "provider": "loxo",
                "identifier": (
                    '{"path":"/example-search","tenant":"example-search","v":1}'
                ),
            },
            {
                "url": "https://acme.ripplehire.com/ripplehire/careers",
                "provider": "ripplehire",
                "identifier": "acme.ripplehire.com",
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
                "url": "https://jobs.example.test/en/search-jobs",
                "provider": "talentbrew",
                "identifier": (
                    '{"host":"jobs.example.test","locale":"en",'
                    '"site_id":"62886","tenant_id":"47263"}'
                ),
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
                "url": "https://careers.example.test:443/en/annonces",
                "provider": "digitalrecruiters",
                "identifier": (
                    '{"api_base":"https://api.digitalrecruiters.com/public/v1",'
                    '"board_url":"https://careers.example.test:443/en/annonces",'
                    '"locale":"en","tenant":"careers.example.test"}'
                ),
            },
            {
                "url": "https://other.example.test/en/annonces",
                "provider": "digitalrecruiters",
                "identifier": (
                    '{"api_base":"https://api.digitalrecruiters.com/public/v1",'
                    '"board_url":"https://careers.example.test/en/annonces",'
                    '"locale":"en","tenant":"careers.example.test"}'
                ),
            },
            {
                "url": "https://jobs.smartrecruiters.com/Other",
                "provider": "smartrecruiters",
                "identifier": "Visa",
            },
            {
                "url": "https://other.app.loxo.co/example-search",
                "provider": "loxo",
                "identifier": (
                    '{"path":"/example-search","tenant":"example-search","v":1}'
                ),
            },
            {
                "url": "https://other.ripplehire.com/ripplehire/careers",
                "provider": "ripplehire",
                "identifier": "acme.ripplehire.com",
            },
            {
                "url": "https://acme.ripplehire.com/ripplehire/careers?lang=en",
                "provider": "ripplehire",
                "identifier": "acme.ripplehire.com",
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
            {
                "url": "https://jobs.example.test/de/search-jobs",
                "provider": "talentbrew",
                "identifier": (
                    '{"host":"jobs.example.test","locale":"en",'
                    '"site_id":"62886","tenant_id":"47263"}'
                ),
            },
            {
                "url": "https://other.example.test/en/search-jobs",
                "provider": "talentbrew",
                "identifier": (
                    '{"host":"jobs.example.test","locale":"en",'
                    '"site_id":"62886","tenant_id":"47263"}'
                ),
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

    def test_peoplesoft_replay_locator_round_trips_and_rejects_tampering(self):
        url = (
            "https://www.cnd.nd.gov/psc/recruit/EMPLOYEE/HRMS/c/"
            "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL?Page=HRS_APP_SCHJOB_FL&"
            "Action=U&SiteId=11000&FOCUS=Applicant"
        )
        board = PeopleSoftAdapter().identify_board(url)
        self.assertIsNotNone(board)
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="url_evidence",
            evidence_url=url,
        )

        payload = discovered.to_checkpoint_payload()

        self.assertIsNotNone(payload)
        self.assertEqual(
            DiscoveredJobBoard.from_checkpoint_payload(payload),
            discovered,
        )
        tampered = json.loads(board.identifier)
        tampered["site_id"] = "22000"
        with self.assertRaisesRegex(ValueError, "not replay-safe"):
            DiscoveredJobBoard(
                board=JobBoard(
                    url=board.url,
                    provider="peoplesoft",
                    identifier=json.dumps(
                        tampered,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    replay_safe=True,
                ),
                detection_method="url_evidence",
                    evidence_url=board.url,
            ).to_checkpoint_payload()

    def test_healthcaresource_replay_locator_is_tenant_bound(self):
        board_url = "https://redlandscommunityhospital.hcshiring.com/jobs"
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url=board_url,
                provider="healthcaresource",
                identifier="redlandscommunityhospital",
                replay_safe=True,
            ),
            detection_method="url_evidence",
            evidence_url=board_url,
        )

        payload = discovered.to_checkpoint_payload()

        self.assertIsNotNone(payload)
        self.assertEqual(DiscoveredJobBoard.from_checkpoint_payload(payload), discovered)
        for url, identifier in (
            ("https://other.hcshiring.com/jobs", "redlandscommunityhospital"),
            (board_url + "/123", "redlandscommunityhospital"),
            (board_url + "?tenant=other", "redlandscommunityhospital"),
            (board_url, "other"),
        ):
            with self.subTest(url=url, identifier=identifier), self.assertRaisesRegex(
                ValueError, "not replay-safe"
            ):
                DiscoveredJobBoard(
                    board=JobBoard(
                        url=url,
                        provider="healthcaresource",
                        identifier=identifier,
                        replay_safe=True,
                    ),
                    detection_method="url_evidence",
                    evidence_url=url,
                ).to_checkpoint_payload()

    def test_pinpoint_replay_locator_is_tenant_bound(self):
        board_url = "https://skims.pinpointhq.com/"
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url=board_url,
                provider="pinpoint",
                identifier="skims",
                replay_safe=True,
            ),
            detection_method="url_evidence",
            evidence_url=board_url,
        )

        payload = discovered.to_checkpoint_payload()

        self.assertIsNotNone(payload)
        self.assertEqual(DiscoveredJobBoard.from_checkpoint_payload(payload), discovered)
        for url, identifier in (
            ("https://other.pinpointhq.com/", "skims"),
            (board_url + "jobs", "skims"),
            (board_url + "?tenant=other", "skims"),
            (board_url, "other"),
        ):
            with self.subTest(url=url, identifier=identifier), self.assertRaisesRegex(
                ValueError, "not replay-safe"
            ):
                DiscoveredJobBoard(
                    board=JobBoard(
                        url=url,
                        provider="pinpoint",
                        identifier=identifier,
                        replay_safe=True,
                    ),
                    detection_method="url_evidence",
                    evidence_url=url,
                ).to_checkpoint_payload()

    def test_cws_replay_locator_accepts_frozen_search_protocol_and_rejects_tampering(self):
        identity = {
            "api_url": "https://jobsapi-google.m-cloud.io/api/",
            "board_url": "https://jobs.example.test/job-search-results/",
            "boost": "description:0,title:100",
            "detail_path": "/job-description",
            "filters": ["brand:Example Health~Example"],
            "limit": 12,
            "org_id": "companies/example",
            "smartpost_org": "1962",
            "sort": ["open_date", "ascending"],
        }
        board = DiscoveredJobBoard(
            board=JobBoard(
                url=identity["board_url"],
                provider="cws",
                identifier=json.dumps(
                    identity,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url=identity["board_url"],
        )

        portfolio = JobBoardPortfolio(boards=(board,), eligible_set_complete=True)
        self.assertEqual(
            JobBoardPortfolio.from_checkpoint_payload(portfolio.to_checkpoint_payload()),
            portfolio,
        )

        legacy_identity = {**identity}
        legacy_identity.pop("smartpost_org")
        legacy_board = DiscoveredJobBoard(
            board=JobBoard(
                url=legacy_identity["board_url"],
                provider="cws",
                identifier=json.dumps(
                    legacy_identity,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url=legacy_identity["board_url"],
        )
        legacy_portfolio = JobBoardPortfolio(
            boards=(legacy_board,),
            eligible_set_complete=True,
        )
        self.assertEqual(
            JobBoardPortfolio.from_checkpoint_payload(
                legacy_portfolio.to_checkpoint_payload()
            ),
            legacy_portfolio,
        )

        for key, value in (
            ("filters", ["brand:Example", "brand:Example"]),
            ("boost", "title:100\nunsafe"),
            ("smartpost_org", "1962/other"),
            ("sort", ["open_date", "sideways"]),
        ):
            tampered = {**identity, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, "not replay-safe"
            ):
                JobBoardPortfolio(
                    boards=(
                        DiscoveredJobBoard(
                            board=JobBoard(
                                url=identity["board_url"],
                                provider="cws",
                                identifier=json.dumps(
                                    tampered,
                                    ensure_ascii=True,
                                    separators=(",", ":"),
                                    sort_keys=True,
                                ),
                                replay_safe=True,
                            ),
                            detection_method="page_evidence",
                            evidence_url=identity["board_url"],
                        ),
                    ),
                    eligible_set_complete=True,
                )

    def test_runtime_only_suffix_preserves_replay_safe_primary_as_incomplete(self):
        portfolio = JobBoardPortfolio(
            boards=(self._board(), self._board(
                url="https://jobs.example.test/runtime",
                identifier='{"api_key":"runtime-only"}',
                replay_safe=False,
            )),
            eligible_set_complete=False,
        )

        payload = portfolio.to_checkpoint_payload()

        self.assertIsNotNone(payload)
        restored = JobBoardPortfolio.from_checkpoint_payload(payload)
        self.assertEqual(restored.boards, (portfolio.primary,))
        self.assertFalse(restored.eligible_set_complete)

    def test_runtime_only_primary_does_not_promote_lower_ranked_replay_board(self):
        runtime_primary = self._board(
            url="https://jobs.example.test/runtime",
            identifier='{"api_key":"runtime-only"}',
            replay_safe=False,
        )
        portfolio = JobBoardPortfolio(
            boards=(runtime_primary, self._board()),
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
