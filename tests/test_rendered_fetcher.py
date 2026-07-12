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
            html="<html><body><p>We are hiring for open roles across engineering and product.</p></body></html>",
            final_url="https://example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(static_page=static_page, render_budget=1)

        page = fetcher._fetch_live("https://example.com")

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
