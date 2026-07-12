import unittest
from pathlib import Path

import job_source_agent.rendered_fetcher as rendered_fetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.rendered_fetcher import _navigate_with_settle
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
    fixtures = Path(__file__).parent / "fixtures" / "rendered_fetcher"

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

    def test_workable_shell_assets_and_locale_variants_do_not_suppress_render(self):
        url = "https://apply.workable.com/example/"
        static_page = Page(
            url=url,
            html=(self.fixtures / "workable_static_shell.html").read_text(),
            final_url=url,
            source="live",
        )
        rendered_page = Page(
            url=url,
            html=(self.fixtures / "rendered_job_page.html").read_text(),
            final_url=url,
            source="browser",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            rendered_page=rendered_page,
            render_budget=1,
        )

        page = fetcher._fetch_live(url)

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertIn("Software Engineer", page.html)
        self.assertEqual(fetcher.render_events[0]["reason"], "static_no_usable_job_links")

    def test_same_path_job_identifier_query_remains_a_usable_link(self):
        url = "https://example.com/careers"
        static_page = Page(
            url=url,
            html='<a href="?jobId=123">Software Engineer job</a><div id="app"></div>',
            final_url=url,
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            rendered_page=static_page,
            render_budget=1,
        )

        page = fetcher._fetch_live(url)

        self.assertEqual(page.source, "live")
        self.assertEqual(fetcher.render_attempts, 0)

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

    def test_navigation_accepts_useful_dom_when_network_never_becomes_idle(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))
                raise NavigationTimeout("analytics connection remains open")

        times = iter((10.0, 10.4))
        page = FakePage()

        _navigate_with_settle(
            page,
            "https://example.com/jobs",
            timeout_seconds=2,
            timeout_error_type=NavigationTimeout,
            clock=lambda: next(times),
        )

        self.assertEqual(page.calls[0][2], {"wait_until": "domcontentloaded", "timeout": 2000})
        self.assertEqual(page.calls[1][0:2], ("wait", "networkidle"))
        self.assertLessEqual(page.calls[1][2]["timeout"], 1600)

    def test_navigation_does_not_exceed_budget_after_dom_ready(self):
        class FakePage:
            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))

        times = iter((20.0, 22.1))
        page = FakePage()

        _navigate_with_settle(
            page,
            "https://example.com/jobs",
            timeout_seconds=2,
            timeout_error_type=TimeoutError,
            clock=lambda: next(times),
        )

        self.assertEqual([call[0] for call in page.calls], ["goto"])


if __name__ == "__main__":
    unittest.main()
