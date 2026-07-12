import unittest

import job_source_agent.rendered_fetcher as rendered_fetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.web import FetchError, Page


class FakeSmartRenderedFetcher(SmartRenderedFetcher):
    def __init__(self, static_page=None, rendered_page=None, static_error=None, **kwargs):
        super().__init__(**kwargs)
        self.static_page = static_page
        self.rendered_page = rendered_page or Page(
            url="https://example.com",
            html="<html><body><a href='/careers'>Careers</a></body></html>",
            final_url="https://example.com",
            source="browser",
        )
        self.static_error = static_error

    def _static_live(self, url, data=None, headers=None):
        if self.static_error:
            raise self.static_error
        return self.static_page

    def _render_live(self, url, reason="manual"):
        self.render_attempts += 1
        return self.rendered_page


class SmartRenderedFetcherTests(unittest.TestCase):
    def test_static_html_is_used_when_page_has_content(self):
        static_page = Page(
            url="https://example.com",
            html="<html><body><p>Our product helps teams collaborate across engineering and design.</p></body></html>",
            final_url="https://example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com")

        self.assertEqual(page.source, "live")
        self.assertEqual(fetcher.render_attempts, 0)

    def test_nonempty_jobs_shell_without_usable_links_uses_browser(self):
        static_page = Page(
            url="https://example.com/careers",
            html=(
                '<html><body><div id="root">'
                "Explore our culture, benefits, values, offices, and open opportunities. "
                "We are hiring people who want to build thoughtful products with our team."
                "</div><script src=\"/assets/app.js\"></script></body></html>"
            ),
            final_url="https://example.com/careers",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            render_budget=1,
            min_visible_text_chars=20,
        )

        page = fetcher._fetch_live("https://example.com/careers")

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertEqual(fetcher.render_attempts, 1)
        self.assertEqual(fetcher.render_events[0]["reason"], "static_no_usable_job_links")

    def test_usable_career_link_keeps_static_page(self):
        static_page = Page(
            url="https://example.com",
            html=(
                '<html><body><div id="root">We are hiring across our global teams.</div>'
                '<a href="/careers/open-roles">View open roles</a>'
                '<script src="/app.js"></script></body></html>'
            ),
            final_url="https://example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com")

        self.assertEqual(page.source, "live")
        self.assertEqual(fetcher.render_attempts, 0)

    def test_structured_jobs_payload_does_not_trigger_browser(self):
        static_page = Page(
            url="https://example.com/api/jobs",
            html='{"jobs":[{"title":"Software Engineer","id":"123"}]}',
            final_url="https://example.com/api/jobs",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com/api/jobs")

        self.assertEqual(page.source, "live")
        self.assertEqual(fetcher.render_attempts, 0)

    def test_js_shell_uses_browser_budget(self):
        static_page = Page(
            url="https://example.com",
            html='<html><body><div id="root"></div><script src="/app.js"></script></body></html>',
            final_url="https://example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com")

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertEqual(fetcher.render_attempts, 1)
        self.assertEqual(fetcher.render_events[0]["reason"], "static_shell")
        self.assertEqual(fetcher.render_events[0]["outcome"], "success")

    def test_static_error_can_fall_back_to_browser(self):
        fetcher = FakeSmartRenderedFetcher(
            static_error=FetchError("timeout"),
            render_budget=1,
        )

        page = fetcher._fetch_live("https://example.com")

        self.assertTrue(page.source.startswith("browser_after_static_error"))
        self.assertEqual(fetcher.render_attempts, 1)
        self.assertEqual(fetcher.render_events[0]["reason"], "static_error")

    def test_rendered_source_keeps_artifact_marker(self):
        rendered_page = Page(
            url="https://example.com",
            html="<html><body>Rendered jobs</body></html>",
            final_url="https://example.com",
            source="browser|artifact:screenshot_png",
            artifacts={"screenshot_png": b"fake-png"},
        )
        static_page = Page(
            url="https://example.com",
            html='<html><body><div id="root"></div><script src="/app.js"></script></body></html>',
            final_url="https://example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, rendered_page=rendered_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com")

        self.assertEqual(page.source, "browser_after_static_shell|artifact:screenshot_png")
        self.assertEqual(fetcher.render_events[0]["source"], page.source)

    def test_render_budget_is_respected(self):
        fetcher = FakeSmartRenderedFetcher(
            static_error=FetchError("timeout"),
            render_budget=0,
        )

        with self.assertRaises(FetchError):
            fetcher._fetch_live("https://example.com")

    def test_exhausted_budget_returns_static_page_and_records_skip(self):
        static_page = Page(
            url="https://example.com/jobs",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://example.com/jobs",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=0)

        page = fetcher._fetch_live("https://example.com/jobs")

        self.assertIs(page, static_page)
        self.assertEqual(fetcher.render_attempts, 0)
        self.assertEqual(fetcher.render_events[0]["reason"], "static_no_usable_job_links")
        self.assertEqual(fetcher.render_events[0]["outcome"], "skipped_budget")

    def test_render_budget_is_shared_across_requests(self):
        static_page = Page(
            url="https://example.com/jobs",
            html=(
                '<html><body><div id="root">Open jobs load here.</div>'
                '<a href="#">Jobs</a></body></html>'
            ),
            final_url="https://example.com/jobs",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        first_page = fetcher._fetch_live("https://example.com/jobs")
        second_page = fetcher._fetch_live("https://example.com/jobs")

        self.assertEqual(first_page.source, "browser_after_static_shell")
        self.assertIs(second_page, static_page)
        self.assertEqual(fetcher.render_attempts, 1)
        self.assertEqual(
            [event["outcome"] for event in fetcher.render_events],
            ["success", "skipped_budget"],
        )

    def test_launch_browser_falls_back_to_local_chrome(self):
        class FakePlaywrightError(Exception):
            pass

        class FakeChromium:
            def __init__(self):
                self.calls = []

            def launch(self, **kwargs):
                self.calls.append(kwargs)
                if "channel" not in kwargs:
                    raise FakePlaywrightError("managed browser missing")
                return "local chrome"

        chromium = FakeChromium()
        playwright = type("FakePlaywright", (), {"chromium": chromium})()
        original_local_chrome_available = rendered_fetcher._local_chrome_available
        try:
            rendered_fetcher._local_chrome_available = lambda: True
            browser = rendered_fetcher._launch_browser(playwright, FakePlaywrightError)
        finally:
            rendered_fetcher._local_chrome_available = original_local_chrome_available

        self.assertEqual(browser, "local chrome")
        self.assertEqual(chromium.calls[1]["channel"], "chrome")

    def test_launch_browser_reraises_when_no_local_chrome_exists(self):
        class FakePlaywrightError(Exception):
            pass

        class FakeChromium:
            def launch(self, **kwargs):
                raise FakePlaywrightError("managed browser missing")

        playwright = type("FakePlaywright", (), {"chromium": FakeChromium()})()
        original_local_chrome_available = rendered_fetcher._local_chrome_available
        try:
            rendered_fetcher._local_chrome_available = lambda: False
            with self.assertRaises(FakePlaywrightError):
                rendered_fetcher._launch_browser(playwright, FakePlaywrightError)
        finally:
            rendered_fetcher._local_chrome_available = original_local_chrome_available


if __name__ == "__main__":
    unittest.main()
