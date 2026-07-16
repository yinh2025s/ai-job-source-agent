import unittest

from job_source_agent.generic_opening_inventory import collect_generic_opening_inventory
from job_source_agent.web import FetchError, Page


BASE_URL = "https://careers.example.com/jobs"


def job_card(title: str, path: str) -> str:
    return (
        '<article class="job-card">'
        f"<h2>{title}</h2><a href=\"{path}\">View job</a>"
        "</article>"
    )


def page(url: str, body: str, *, final_url: str | None = None) -> Page:
    return Page(url=url, final_url=final_url or url, html=f"<html><body>{body}</body></html>")


class RecordingFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.pages.get(url)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FetchError(f"missing page: {url}")
        return value


class GenericOpeningInventoryTests(unittest.TestCase):
    def collect(self, initial, pages=None, *, max_pages=4, max_candidates=20):
        fetcher = RecordingFetcher(pages or {})
        result = collect_generic_opening_inventory(
            fetcher,
            initial,
            max_pages=max_pages,
            max_candidates=max_candidates,
        )
        return result, fetcher

    def test_collects_target_from_second_page(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(
            BASE_URL,
            job_card("Sales Manager", "/jobs/sales-manager")
            + f'<a href="{second_url}">Next page</a>',
        )
        second = page(second_url, job_card("AI Engineer", "/jobs/ai-engineer"))

        result, fetcher = self.collect(initial, {second_url: second})

        self.assertEqual([item.title for item in result.candidates], ["Sales Manager", "AI Engineer"])
        self.assertEqual(fetcher.requests, [(second_url, None, None)])
        self.assertEqual(result.pages_fetched, 2)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.stop_reason, "complete")

    def test_follows_rel_next_without_visible_label(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(BASE_URL, f'<link rel="next" href="{second_url}">')

        result, fetcher = self.collect(initial, {second_url: page(second_url, "")})

        self.assertEqual(fetcher.requests[0][0], second_url)
        self.assertTrue(result.inventory_complete)

    def test_rejects_unsafe_next_urls(self):
        unsafe_urls = {
            "cross_origin": "https://other.example.net/jobs?page=2",
            "private": "https://127.0.0.1/jobs?page=2",
            "credentials": "https://user:password@careers.example.com/jobs?page=2",
            "port": "https://careers.example.com:8443/jobs?page=2",
            "sensitive_query": "https://careers.example.com/jobs?access_token=secret",
        }
        for reason, unsafe_url in unsafe_urls.items():
            with self.subTest(reason=reason):
                initial = page(BASE_URL, f'<a href="{unsafe_url}">Next</a>')
                result, fetcher = self.collect(initial)
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.stop_reason, "unsafe_next_url")

    def test_detects_cycle(self):
        initial = page(BASE_URL, f'<a href="{BASE_URL}">Load more</a>')

        result, fetcher = self.collect(initial)

        self.assertEqual(fetcher.requests, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "pagination_cycle")

    def test_stops_at_page_cap(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(BASE_URL, f'<a href="{second_url}">Next</a>')

        result, fetcher = self.collect(initial, max_pages=1)

        self.assertEqual(fetcher.requests, [])
        self.assertEqual(result.pages_fetched, 1)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "page_cap_reached")

    def test_returns_candidates_when_later_fetch_fails(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(
            BASE_URL,
            job_card("AI Engineer", "/jobs/ai-engineer")
            + f'<a href="{second_url}">Next</a>',
        )

        result, _fetcher = self.collect(initial, {second_url: FetchError("timeout")})

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "fetch_error")
        self.assertEqual(result.trace[-1].url, second_url)

    def test_deduplicates_candidates_across_pages(self):
        second_url = f"{BASE_URL}?page=2"
        duplicate = job_card("AI Engineer", "/jobs/ai-engineer")
        initial = page(BASE_URL, duplicate + f'<a href="{second_url}">Next</a>')
        second = page(second_url, duplicate + job_card("Designer", "/jobs/designer"))

        result, _fetcher = self.collect(initial, {second_url: second})

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer", "Designer"])

    def test_single_page_without_pagination_evidence_is_not_declared_complete(self):
        result, fetcher = self.collect(
            page(BASE_URL, job_card("AI Engineer", "/jobs/ai-engineer"))
        )

        self.assertEqual(fetcher.requests, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")
        self.assertEqual(result.trace[-1].candidate_count, 1)
        self.assertNotIn("<html", repr(result.trace))

    def test_candidate_cap_prevents_completeness_claim(self):
        initial = page(
            BASE_URL,
            job_card("AI Engineer", "/jobs/ai-engineer")
            + job_card("Designer", "/jobs/designer"),
        )

        result, _fetcher = self.collect(initial, max_candidates=1)

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "candidate_cap_reached")


if __name__ == "__main__":
    unittest.main()
