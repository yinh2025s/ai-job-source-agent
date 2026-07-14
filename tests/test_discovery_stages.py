import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.errors import DiscoveryError
from job_source_agent.homepage_navigation import HomepageNavigationEvidence
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.models import (
    STAGE_HIRING_IDENTITY_RESOLUTION,
    CompanyInput,
    StageResult,
)
from job_source_agent.stages import (
    CareerDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    PipelineStageRunner,
)


class FakeDiscoveryService:
    def find_career_page(
        self,
        company_website_url,
        company_name=None,
        preferred_url=None,
        target_title=None,
        target_location=None,
    ):
        return f"{company_website_url}/careers", {"method": "fake-career"}

    def find_job_board(self, career_page_url, company_name=None, target_location=None):
        return "https://boards.greenhouse.io/acme", {"method": "fake-board"}

    def match_opening(self, job_list_url, target_title=None, target_location=None):
        return f"{job_list_url}/jobs/123", job_list_url, {"method": "fake-match"}


class DiscoveryStageTests(unittest.TestCase):
    def test_career_stage_passes_saved_homepage_navigation_evidence(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.evidence = None

            def find_career_page(self, *args, homepage_navigation_evidence=None, **kwargs):
                self.evidence = homepage_navigation_evidence
                return super().find_career_page(*args, **kwargs)

        evidence = HomepageNavigationEvidence(
            homepage_url="https://acme.example",
            candidate_urls=("https://acme.example/careers",),
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url="https://acme.example")
        )
        context.homepage_navigation_evidence = evidence
        service = CapturingCareer()

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertIs(service.evidence, evidence)

    def test_career_stage_does_not_search_publisher_after_identity_failure(self):
        class MustNotSearch(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("publisher career site must not be searched")

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Recruiting Publisher",
                company_website_url="https://publisher.example",
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="failed",
                reason_code="COMPANY_IDENTITY_AMBIGUOUS",
            )
        )

        execution = CareerDiscoveryStage(MustNotSearch()).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(
            execution.trace["scheduler"]["reason"],
            "hiring_identity_unresolved",
        )

    def test_s4_s5_s6_can_run_through_versioned_context(self):
        service = FakeDiscoveryService()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                job_title="Data Analyst",
            )
        )
        runner = PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        )

        runner.run(context)

        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.job_list_page_url, "https://boards.greenhouse.io/acme")
        self.assertEqual(context.open_position_url, "https://boards.greenhouse.io/acme/jobs/123")
        self.assertEqual([result.status for result in context.stage_results], ["success", "success", "success"])

    def test_job_board_stage_can_run_independently_from_saved_career_context(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["job_list_page_url"], "https://boards.greenhouse.io/acme")

    def test_job_board_stage_passes_target_location(self):
        class LocationAwareService(FakeDiscoveryService):
            def __init__(self):
                self.target_location = None

            def find_job_board(
                self,
                career_page_url,
                company_name=None,
                target_location=None,
            ):
                self.target_location = target_location
                return super().find_job_board(
                    career_page_url,
                    company_name=company_name,
                    target_location=target_location,
                )

        service = LocationAwareService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_location="Brussels, Belgium")
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(service).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(service.target_location, "Brussels, Belgium")

    def test_job_board_stage_uses_native_external_apply_without_career_page(self):
        external = (
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/"
            "Data-Analyst_R123"
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", external_apply_url=external)
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.result.provider, "workday")
        self.assertEqual(
            execution.updates["job_list_page_url"],
            "https://company.wd5.myworkdayjobs.com/en-US/acme",
        )
        self.assertEqual(execution.trace["method"], "external_apply_url")

    def test_job_board_stage_rejects_unknown_external_apply_provider(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                external_apply_url="https://apply.untrusted.example/jobs/123",
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "unsupported")
        self.assertEqual(execution.result.reason_code, "PROVIDER_UNSUPPORTED")
        self.assertEqual(execution.updates, {})

    def test_direct_input_career_root_is_trusted_without_network_revalidation(self):
        class MustNotFetchCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("trusted direct root should not be re-fetched")

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://jobs.lever.co/acme",
                source="input",
            )
        )

        execution = CareerDiscoveryStage(MustNotFetchCareer()).run(context)

        self.assertEqual(execution.updates["career_page_url"], "https://jobs.lever.co/acme")
        self.assertEqual(execution.trace["preferred_root_validation"], "trusted_provenance")

    def test_replay_career_root_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://job-boards.greenhouse.io/acme", {"validated": True}

        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://wrong.example/careers",
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, "https://wrong.example/careers")
        self.assertEqual(execution.updates["career_page_url"], "https://job-boards.greenhouse.io/acme")

    def test_replay_career_root_from_fresh_matching_identity_evidence_is_trusted(self):
        class MustNotFetchCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("freshly resolved identity root should not be re-fetched")

        career_root = "https://careers.example-health.test/jobs"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example Health",
                company_website_url="https://example-health.test",
                career_root_url="https://stale.example/careers",
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.career_root_url = career_root
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": career_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "selected": {"career_root_url": career_root}
        }

        execution = CareerDiscoveryStage(MustNotFetchCareer()).run(context)

        self.assertEqual(execution.updates["career_page_url"], career_root)
        self.assertEqual(execution.trace["preferred_root_validation"], "trusted_provenance")

    def test_replay_career_root_passed_through_s3_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://careers.example.test/jobs", {"validated": True}

        stale_root = "https://stale.example/careers"
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example",
                company_website_url="https://example.test",
                career_root_url=stale_root,
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": stale_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "matched_rule": None
        }

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, stale_root)
        self.assertEqual(execution.updates["career_page_url"], "https://careers.example.test/jobs")

    def test_replay_career_root_mismatching_identity_evidence_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                self.preferred_url = preferred_url
                return "https://careers.example-health.test/jobs", {"validated": True}

        stale_root = "https://stale.example/careers"
        resolved_root = "https://careers.example-health.test/jobs"
        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Example Health",
                company_website_url="https://example-health.test",
                career_root_url=stale_root,
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )
        context.stage_results.append(
            StageResult(
                stage=STAGE_HIRING_IDENTITY_RESOLUTION,
                status="success",
                evidence=[{"field": "career_root_url", "url": resolved_root}],
            )
        )
        context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION] = {
            "selected": {"career_root_url": resolved_root}
        }

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, stale_root)
        self.assertEqual(execution.updates["career_page_url"], resolved_root)

    def test_job_board_stage_accepts_provider_from_verified_page_evidence(self):
        class PageAwareService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                return career_page_url, {
                    "provider": "icims",
                    "provider_detection": {"method": "page_evidence"},
                }

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://jobs.acme.example/region/jobs"

        execution = JobBoardDiscoveryStage(PageAwareService()).run(context)

        self.assertEqual(execution.result.provider, "icims")
        self.assertEqual(execution.updates["provider"], "icims")

    def test_page_aware_board_handoff_flows_from_s5_to_s6(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.acme.example/careers",
                provider="phenom",
                identifier="ACME",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.acme.example/careers",
        )

        class PageAwareService(FakeDiscoveryService):
            def __init__(self):
                self.received = None

            def find_job_board_with_evidence(
                self, career_page_url, company_name=None, target_location=None
            ):
                return discovered.board.url, {"provider": "phenom"}, discovered

            def match_discovered_board(
                self, board_evidence, target_title=None, target_location=None
            ):
                self.received = board_evidence
                return (
                    board_evidence.board.url + "/job/123",
                    board_evidence.board.url,
                    {"provider_detection": {"method": "typed_stage_handoff"}},
                )

        service = PageAwareService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.career_page_url = discovered.evidence_url

        PipelineStageRunner(
            [JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        ).run(context)

        self.assertEqual(context.discovered_job_board, discovered)
        self.assertIs(service.received, discovered)
        self.assertEqual(context.open_position_url, discovered.board.url + "/job/123")

    def test_opening_portfolio_continues_after_board_local_empty_to_exact(self):
        early = DiscoveredJobBoard(
            board=JobBoard(
                url="https://early.example.test/search-results",
                provider="phenom",
            ),
            detection_method="url_evidence",
            evidence_url="https://early.example.test/search-results",
        )
        general = DiscoveredJobBoard(
            board=JobBoard(
                url="https://general.example.test/search-results",
                provider="phenom",
            ),
            detection_method="url_evidence",
            evidence_url="https://general.example.test/search-results",
        )

        class PortfolioService(FakeDiscoveryService):
            def __init__(self):
                self.attempted = []

            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                self.attempted.append(discovered.board.url)
                if discovered is early:
                    return None, discovered.board.url, {
                        "provider_api": {
                            "inventory": {
                                "status": "verified_filtered_empty",
                                "scope": "title_filtered",
                                "candidate_count": 0,
                            }
                        }
                    }
                return (
                    discovered.board.url + "/job/123",
                    discovered.board.url,
                    {"provider_api": {"inventory": {"status": "verified"}}},
                )

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Data Scientist")
        )
        context.job_list_page_url = early.board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=(early, general),
            eligible_set_complete=True,
        )
        service = PortfolioService()

        execution = OpeningMatchStage(
            service,
            max_job_board_attempts=2,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.result.provider, "phenom")
        self.assertEqual(service.attempted, [early.board.url, general.board.url])
        self.assertEqual(
            execution.updates["open_position_url"],
            general.board.url + "/job/123",
        )
        self.assertEqual(execution.trace["board_portfolio"]["attempted_count"], 2)

    def test_opening_portfolio_does_not_claim_no_match_with_unattempted_board(self):
        boards = tuple(
            DiscoveredJobBoard(
                board=JobBoard(
                    url=f"https://jobs{index}.example.test/search-results",
                    provider="phenom",
                ),
                detection_method="url_evidence",
                evidence_url=f"https://jobs{index}.example.test/search-results",
            )
            for index in range(2)
        )

        class EmptyPortfolioService(FakeDiscoveryService):
            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified_filtered_empty",
                            "scope": "title_filtered",
                            "complete": True,
                            "candidate_count": 0,
                        }
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.job_list_page_url = boards[0].board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=boards,
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(
            EmptyPortfolioService(),
            max_job_board_attempts=1,
        ).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "JOB_BOARD_PORTFOLIO_INCOMPLETE",
        )
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 1)

    def test_opening_portfolio_claims_no_match_only_after_complete_attempt_set(self):
        boards = tuple(
            DiscoveredJobBoard(
                board=JobBoard(
                    url=f"https://jobs{index}.example.test/search-results",
                    provider="phenom",
                ),
                detection_method="url_evidence",
                evidence_url=f"https://jobs{index}.example.test/search-results",
            )
            for index in range(2)
        )

        class EmptyPortfolioService(FakeDiscoveryService):
            def match_discovered_board(self, discovered, target_title=None, target_location=None):
                return None, discovered.board.url, {
                    "provider_api": {
                        "inventory": {
                            "status": "verified_filtered_empty",
                            "scope": "title_filtered",
                            "complete": True,
                            "candidate_count": 0,
                        }
                    }
                }

        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", job_title="Engineer")
        )
        context.job_list_page_url = boards[0].board.url
        context.job_board_portfolio = JobBoardPortfolio(
            boards=boards,
            eligible_set_complete=True,
        )

        execution = OpeningMatchStage(
            EmptyPortfolioService(),
            max_job_board_attempts=2,
        ).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")
        self.assertEqual(execution.trace["board_portfolio"]["unattempted_count"], 0)

    def test_opening_no_match_is_partial_not_failed(self):
        class NoMatchService(FakeDiscoveryService):
            def match_opening(self, job_list_url, target_title=None, target_location=None):
                return None, job_list_url, {"opening_error": "specific_opening_not_found"}

        context = PipelineContext.from_company(CompanyInput(company_name="Acme", job_title="Engineer"))
        context.job_list_page_url = "https://boards.greenhouse.io/acme"
        context.provider = "greenhouse"

        execution = OpeningMatchStage(NoMatchService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(
            execution.result.reason_code,
            "OPENING_DISCOVERY_INCOMPLETE",
        )

    def test_career_failure_makes_downstream_stages_not_run(self):
        class MissingCareerService(FakeDiscoveryService):
            def find_career_page(
                self,
                company_website_url,
                company_name=None,
                preferred_url=None,
                target_title=None,
                target_location=None,
            ):
                raise DiscoveryError("career_page_not_found", "missing", trace={"searched": True})

        service = MissingCareerService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Missing", company_website_url="https://missing.example", job_title="Engineer")
        )

        PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        ).run(context)

        self.assertEqual([result.status for result in context.stage_results], ["failed", "not_run", "not_run"])
        self.assertEqual(context.stage_results[0].reason_code, "CAREER_PAGE_NOT_FOUND")

    def test_deterministic_career_miss_reports_linkedin_native_only(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.stage_results.append(
            StageResult(
                stage="career_discovery",
                status="failed",
                reason_code="CAREER_PAGE_NOT_FOUND",
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(execution.updates, {})
        self.assertEqual(execution.result.evidence[0]["source_posting_url"], job_url)

    def test_retryable_career_failure_is_not_hidden_by_native_source(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.stage_results.append(
            StageResult(
                stage="career_discovery",
                status="failed",
                reason_code="NETWORK_TIMEOUT",
                retryable=True,
            )
        )

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "not_run")

    def test_deterministic_job_board_miss_reports_linkedin_native_only(self):
        class MissingBoardService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError("job_board_not_found", "missing", trace={"searched": True})

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(MissingBoardService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "LINKEDIN_NATIVE_ONLY")

    def test_verified_board_wins_over_native_source_evidence(self):
        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertIn("job_list_page_url", execution.updates)

    def test_incomplete_job_board_trace_is_not_hidden_by_native_source(self):
        class IncompleteBoardService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError(
                    "job_board_not_found",
                    "incomplete",
                    trace={"candidate_fetch_errors": [{"error": "request timed out"}]},
                )

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(IncompleteBoardService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "JOB_BOARD_NOT_FOUND")

    def test_career_budget_exhaustion_remains_retryable(self):
        class BudgetService(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "FETCH_BUDGET_EXHAUSTED",
                    "candidate budget exhausted",
                    trace={"candidate_fetch_budget_exhausted": {"limit": 5}},
                )

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
            )
        )

        execution = CareerDiscoveryStage(BudgetService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(execution.result.retryable)

    def test_explicit_empty_official_career_page_is_not_rewritten_as_native_only(self):
        class EmptyService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None, target_location=None):
                raise DiscoveryError(
                    "NO_PUBLIC_OPENINGS",
                    "official empty state",
                    trace={"explicit_empty_inventory": {"phrase": "no open positions"}},
                )

        job_url = "https://www.linkedin.com/jobs/view/123"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url=job_url,
                source_trace={
                    "linkedin_posting": {
                        "availability": "active",
                        "apply_mode": "linkedin_native",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": job_url,
                    }
                },
            )
        )
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(EmptyService()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "NO_PUBLIC_OPENINGS")


if __name__ == "__main__":
    unittest.main()
