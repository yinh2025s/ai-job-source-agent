import unittest

from job_source_agent.career_search import CareerSearchResult
from job_source_agent.career_surface_discovery import CareerSurfaceCandidateDiscovery
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.models import LinkCandidate
from job_source_agent.provider_candidates import CandidateDiscoveryRequest
from job_source_agent.web import Page


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        return self.pages[url]


class _Resolver:
    def __init__(self, lead, page):
        self.fetcher = _Fetcher({lead.url: page})
        self.lead = lead

    def search(self, *args, **kwargs):
        return CareerSearchResult(
            [self.lead],
            {"queries": [{"query": "Acme careers jobs", "candidates": [{"url": self.lead.url}]}]},
        )


class _Service:
    def __init__(self, board_url="https://jobs.lever.co/acme"):
        self.board_url = board_url
        self.calls = []

    def find_job_board_portfolio(self, url, **kwargs):
        self.calls.append((url, kwargs))
        board = DiscoveredJobBoard(
            JobBoard(self.board_url, "lever", "acme"),
            "page_evidence",
            self.board_url,
            relationship_evidence_url=url,
        )
        return self.board_url, {"selected_from": "page"}, JobBoardPortfolio((board,), True)


class CareerSurfaceCandidateDiscoveryTests(unittest.TestCase):
    def test_multi_token_company_allows_shorter_brand_host_after_page_identity(self):
        lead = LinkCandidate(
            "https://www.redlandshospital.org/careers", 180,
            "https://www.bing.com/search?q=redlands+hospital+careers",
        )
        page = Page(
            lead.url,
            "<title>Careers | Redlands Community Hospital</title>"
            "<meta content='Jobs at Redlands Community Hospital'>",
            final_url=lead.url,
        )
        result = CareerSurfaceCandidateDiscovery(
            _Resolver(lead, page), _Service()
        ).discover(
            CandidateDiscoveryRequest(company_name="Redlands Community Hospital")
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.trace["attempts"][0]["status"], "verified")

    def test_current_page_identity_and_career_metadata_can_emit_provider_lead(self):
        lead = LinkCandidate(
            "https://careers.acme.example/openings", 180,
            "https://www.bing.com/search?q=acme+careers",
        )
        page = Page(
            lead.url,
            "<html><head><title>Acme Careers</title>"
            "<meta name='description' content='Jobs at Acme'></head></html>",
            final_url=lead.url,
        )
        service = _Service()
        result = CareerSurfaceCandidateDiscovery(_Resolver(lead, page), service).discover(
            CandidateDiscoveryRequest(company_name="Acme", target_title="Engineer")
        )
        self.assertEqual([item.url for item in result.candidates], ["https://jobs.lever.co/acme"])
        self.assertEqual(result.trace["attempts"][0]["status"], "verified")
        self.assertEqual(len(service.calls), 1)

    def test_search_snippet_or_brand_host_cannot_replace_current_page_identity(self):
        lead = LinkCandidate(
            "https://careers.acme.example/openings", 180,
            "https://www.bing.com/search?q=acme+careers",
        )
        page = Page(
            lead.url,
            "<html><head><title>Careers</title><meta content='Open jobs'></head></html>",
            final_url=lead.url,
        )
        service = _Service()
        result = CareerSurfaceCandidateDiscovery(_Resolver(lead, page), service).discover(
            CandidateDiscoveryRequest(company_name="Acme")
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["attempts"][0]["reason"], "career_page_identity_mismatch")
        self.assertEqual(service.calls, [])

    def test_cross_site_redirect_is_rejected_before_board_discovery(self):
        lead = LinkCandidate(
            "https://careers.acme.example/openings", 180,
            "https://www.bing.com/search?q=acme+careers",
        )
        page = Page(
            lead.url,
            "<title>Acme Careers</title><meta content='Acme jobs'>",
            final_url="https://careers.other.example/openings",
        )
        service = _Service()
        result = CareerSurfaceCandidateDiscovery(_Resolver(lead, page), service).discover(
            CandidateDiscoveryRequest(company_name="Acme")
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["attempts"][0]["reason"], "cross_site_redirect")

    def test_existing_verified_career_input_skips_search(self):
        lead = LinkCandidate(
            "https://careers.acme.example/openings", 180,
            "https://www.bing.com/search?q=acme+careers",
        )
        page = Page(lead.url, "<title>Acme Careers</title>", final_url=lead.url)
        result = CareerSurfaceCandidateDiscovery(_Resolver(lead, page), _Service()).discover(
            CandidateDiscoveryRequest(
                company_name="Acme",
                career_page_url="https://acme.example/careers",
            )
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["reason"], "verified_career_input_available")


if __name__ == "__main__":
    unittest.main()
