import unittest
from pathlib import Path
from unittest.mock import patch

import job_source_agent.rendered_fetcher as rendered_fetcher
from job_source_agent.browser_interaction import JobSearchInteraction
from job_source_agent.rendered_fetcher import RenderCapabilityUnavailable, RenderedFetcher
from job_source_agent.rendered_fetcher import FORCE_RENDER_HEADER, SmartRenderedFetcher
from job_source_agent.rendered_fetcher import _execute_job_search_interaction
from job_source_agent.rendered_fetcher import _navigate_with_settle
from job_source_agent.rendered_fetcher import _fetch_raw_browser_response
from job_source_agent.rendered_fetcher import _new_browser_page
from job_source_agent.rendered_fetcher import _new_interaction_page
from job_source_agent.rendered_fetcher import _route_safe_browser_method
from job_source_agent.rendered_fetcher import _safe_browser_headers
from job_source_agent.rendered_fetcher import _wants_raw_browser_response
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

    def _render_live(self, url, reason="manual", headers=None, interaction=None):
        self.render_attempts += 1
        self.render_headers = headers
        self.render_interaction = interaction
        return self.rendered_page


class FakeLocatorCollection:
    def __init__(self, items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class FakeInteractionControl:
    def __init__(
        self,
        *,
        attrs=None,
        text="",
        visible=True,
        enabled=True,
        on_click=None,
    ):
        self.attrs = dict(attrs or {})
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.on_click = on_click
        self.fill_calls = []
        self.click_calls = []

    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def inner_text(self):
        return self.text

    def fill(self, value, **kwargs):
        self.fill_calls.append((value, kwargs))

    def click(self, **kwargs):
        self.click_calls.append(kwargs)
        if self.on_click:
            self.on_click()


class FakeInteractionForm:
    def __init__(self, inputs, buttons, *, controls_by_tag=None):
        self.inputs = inputs
        self.buttons = buttons
        self.controls_by_tag = {
            "input": inputs,
            "button": buttons,
            **(controls_by_tag or {}),
        }
        self.locator_calls = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        if selector in self.controls_by_tag:
            return FakeLocatorCollection(self.controls_by_tag[selector])
        raise AssertionError(f"unexpected fixed selector: {selector}")


class FakeInteractionPage:
    def __init__(self, *, url="https://jobs.example.com/search", dom="<form></form>"):
        self.url = url
        self.dom = dom
        self.forms = []
        self.goto_calls = []
        self.locator_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def locator(self, selector):
        self.locator_calls.append(selector)
        if selector != "form":
            raise AssertionError(f"unexpected fixed selector: {selector}")
        return FakeLocatorCollection(self.forms)

    def content(self):
        return self.dom


def _job_search_interaction():
    return JobSearchInteraction(
        form_ordinal=0,
        query_name="keywords",
        query_id="job-query",
        target_title="Platform Engineer",
        submit_text="Search jobs",
    )


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

    def test_explicit_action_can_force_render_despite_rich_static_navigation(self):
        static_page = Page(
            url="https://jobs.example.com",
            html=(
                "<html><body><nav>About Services Patients Contact Careers</nav>"
                '<a href="/jobs?internal=true">Internal applicants</a>'
                "</body></html>"
            ),
            final_url="https://jobs.example.com",
            source="live",
        )
        rendered_page = Page(
            url="https://jobs.example.com",
            html='<a href="/jobs/123/nurse">Registered Nurse</a>',
            final_url="https://jobs.example.com",
            source="browser",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            rendered_page=rendered_page,
            render_budget=1,
        )

        page = fetcher._fetch_live(
            "https://jobs.example.com",
            headers={FORCE_RENDER_HEADER: "force"},
        )

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertIn("Registered Nurse", page.html)
        self.assertEqual(fetcher.render_events[0]["reason"], "explicit_career_action")
        self.assertIsNone(fetcher.render_headers)

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

    def test_rootdiv_job_shell_uses_browser(self):
        static_page = Page(
            url="https://jobs.example.com/jobs",
            html=(
                '<html><body><header>Career Center</header><div id="rootDiv"></div>'
                '<script src="/assets/applicant-client.js"></script></body></html>'
            ),
            final_url="https://jobs.example.com/jobs",
            source="live",
        )
        rendered_page = Page(
            url="https://jobs.example.com/jobs",
            html='<form><input placeholder="Job Title"><span class="btn">Find Jobs</span></form>',
            final_url="https://jobs.example.com/jobs",
            source="browser",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            rendered_page=rendered_page,
            render_budget=1,
        )

        page = fetcher._fetch_live("https://jobs.example.com/jobs")

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertIn("Find Jobs", page.html)
        self.assertEqual(
            fetcher.render_events[0]["reason"],
            "static_no_usable_job_links",
        )

    def test_nuxt_jobs_shell_without_inventory_uses_browser(self):
        url = "https://careers.example.com/en/openings"
        static_page = Page(
            url=url,
            html=(
                '<main class="job-ads-listing-page"><div class="jobs-container"></div></main>'
                '<div id="__nuxt"></div><script type="module" src="/_nuxt/client.js"></script>'
            ),
            final_url=url,
            source="live",
        )
        rendered_page = Page(
            url=url,
            html='<a href="/en/openings/platform-engineer">Platform Engineer</a>',
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
        self.assertIn("Platform Engineer", page.html)
        self.assertEqual(
            fetcher.render_events[0]["reason"],
            "static_no_usable_job_links",
        )

    def test_embedded_career_api_url_does_not_masquerade_as_navigation(self):
        url = "https://careers.example.com/en/annonces"
        static_page = Page(
            url=url,
            html=(
                '<html><head><link rel="stylesheet" '
                'href="https://api.recruiter.test/careers/v1/sites/example/css"></head>'
                '<body><div id="__nuxt"><main class="job-ads-listing-page">'
                '<h1>Our job offers</h1></main></div>'
                '<a href="https://careers.example.com/fr/annonces">Français</a>'
                '<a href="https://careers.example.com/de/annonces">Deutsch</a>'
                '<script>window.__STATE__={jobAds:[]}</script>'
                '<script src="/_nuxt/JobAdPage-client.js"></script></body></html>'
            ),
            final_url=url,
            source="live",
        )
        rendered_page = Page(
            url=url,
            html='<a href="/en/annonces/account-executive">Account Executive</a>',
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
        self.assertIn("account-executive", page.html)
        self.assertEqual(
            fetcher.render_events[0]["reason"],
            "static_no_usable_job_links",
        )

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

    def test_search_backend_static_error_never_uses_browser_budget(self):
        fetcher = FakeSmartRenderedFetcher(
            static_error=FetchError("timeout"),
            render_budget=1,
        )

        with self.assertRaises(FetchError):
            fetcher._fetch_live("https://html.duckduckgo.com/html/?q=acme+jobs")

        self.assertEqual(fetcher.render_attempts, 0)
        self.assertEqual(
            fetcher.render_events[0]["outcome"],
            "skipped_static_only_source",
        )

    def test_company_page_static_error_still_uses_browser_fallback(self):
        fetcher = FakeSmartRenderedFetcher(
            static_error=FetchError("timeout"),
            render_budget=1,
        )

        page = fetcher._fetch_live("https://careers.example.com/jobs")

        self.assertTrue(page.source.startswith("browser_after_static_error"))
        self.assertEqual(fetcher.render_attempts, 1)

    def test_static_api_error_passes_accept_header_to_browser(self):
        fetcher = FakeSmartRenderedFetcher(
            static_error=FetchError("HTTP 404"),
            render_budget=1,
        )
        headers = {"Accept": "application/javascript, application/json"}

        fetcher._fetch_live("https://api.example.com/job?callback=jobs", headers=headers)

        self.assertEqual(fetcher.render_headers, headers)

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

    def test_explicit_action_uses_one_reserved_render_after_general_budget(self):
        static_page = Page(
            url="https://jobs.example.com",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://jobs.example.com",
            source="live",
        )
        rendered_page = Page(
            url="https://jobs.example.com",
            html='<a href="/jobs/123">Registered Nurse</a>',
            final_url="https://jobs.example.com",
            source="browser",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            rendered_page=rendered_page,
            render_budget=1,
            explicit_action_render_budget=1,
        )

        fetcher._fetch_live("https://speculative.example.com")
        page = fetcher._fetch_live(
            "https://jobs.example.com",
            headers={FORCE_RENDER_HEADER: "force"},
        )
        exhausted = fetcher._fetch_live(
            "https://jobs.example.com",
            headers={FORCE_RENDER_HEADER: "force"},
        )

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertIs(exhausted, static_page)
        self.assertEqual(fetcher.render_attempts, 2)
        self.assertEqual(fetcher.explicit_action_render_attempts, 1)
        self.assertEqual(
            [event["outcome"] for event in fetcher.render_events],
            ["success", "success", "skipped_budget"],
        )

    def test_job_search_interaction_uses_explicit_reserved_render(self):
        static_page = Page(
            url="https://jobs.example.com/search",
            html="<form>Search jobs</form>",
            final_url="https://jobs.example.com/search",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            render_budget=0,
            explicit_action_render_budget=1,
        )
        interaction = _job_search_interaction()

        page = fetcher._fetch_live(
            static_page.url,
            interaction=interaction,
        )

        self.assertEqual(page.source, "browser_after_static_shell")
        self.assertIs(fetcher.render_interaction, interaction)
        self.assertEqual(fetcher.explicit_action_render_attempts, 1)
        self.assertEqual(fetcher.render_events[0]["reason"], "job_search_interaction")

    def test_reserved_render_can_be_disabled(self):
        static_page = Page(
            url="https://jobs.example.com",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://jobs.example.com",
            source="live",
        )
        fetcher = FakeSmartRenderedFetcher(
            static_page=static_page,
            render_budget=0,
            explicit_action_render_budget=0,
        )

        page = fetcher._fetch_live(
            "https://jobs.example.com",
            headers={FORCE_RENDER_HEADER: "force"},
        )

        self.assertIs(page, static_page)
        self.assertEqual(fetcher.render_attempts, 0)
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

    def test_unavailable_renderer_is_cached_without_consuming_attempts(self):
        static_page = Page(
            url="https://example.com/jobs",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://example.com/jobs",
            source="live",
        )
        fetcher = SmartRenderedFetcher(render_budget=1)

        with (
            patch.object(fetcher, "_static_live", return_value=static_page),
            patch.object(
                RenderedFetcher,
                "_fetch_live",
                side_effect=RenderCapabilityUnavailable("Playwright is not installed"),
            ) as render_live,
        ):
            first_page = fetcher._fetch_live("https://example.com/jobs/first")
            second_page = fetcher._fetch_live("https://example.com/jobs/second")

        self.assertIs(first_page, static_page)
        self.assertIs(second_page, static_page)
        self.assertEqual(render_live.call_count, 1)
        self.assertEqual(fetcher.render_attempts, 0)
        self.assertEqual(
            [event["outcome"] for event in fetcher.render_events],
            ["capability_unavailable", "skipped_unavailable"],
        )
        self.assertEqual(fetcher.render_events[0]["error"], "Playwright is not installed")
        self.assertNotIn("error", fetcher.render_events[1])
        self.assertEqual(first_page.source, "live")

    def test_static_fetch_continues_after_renderer_becomes_unavailable(self):
        shell_page = Page(
            url="https://example.com/jobs",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://example.com/jobs",
            source="live",
        )
        content_page = Page(
            url="https://example.com/about",
            html="<html><body>Substantial static company information remains available.</body></html>",
            final_url="https://example.com/about",
            source="live",
        )
        fetcher = SmartRenderedFetcher(render_budget=1)

        with (
            patch.object(fetcher, "_static_live", side_effect=[shell_page, content_page]),
            patch.object(
                RenderedFetcher,
                "_fetch_live",
                side_effect=RenderCapabilityUnavailable("Playwright is not installed"),
            ) as render_live,
        ):
            fetcher._fetch_live(shell_page.url)
            page = fetcher._fetch_live(content_page.url)

        self.assertIs(page, content_page)
        self.assertEqual(page.source, "live")
        self.assertEqual(render_live.call_count, 1)
        self.assertEqual(fetcher.render_attempts, 0)

    def test_cached_renderer_unavailability_preserves_later_static_error(self):
        shell_page = Page(
            url="https://example.com/jobs",
            html='<html><body><div id="root">Open jobs load here.</div></body></html>',
            final_url="https://example.com/jobs",
            source="live",
        )
        fetcher = SmartRenderedFetcher(render_budget=1)

        with (
            patch.object(
                fetcher,
                "_static_live",
                side_effect=[shell_page, FetchError("later static timeout")],
            ),
            patch.object(
                RenderedFetcher,
                "_fetch_live",
                side_effect=RenderCapabilityUnavailable("Playwright is not installed"),
            ) as render_live,
        ):
            fetcher._fetch_live(shell_page.url)
            with self.assertRaisesRegex(FetchError, "later static timeout"):
                fetcher._fetch_live("https://example.com/other")

        self.assertEqual(render_live.call_count, 1)
        self.assertEqual(fetcher.render_attempts, 0)
        self.assertEqual(fetcher.render_events[-1]["outcome"], "skipped_unavailable")

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

    def test_raw_browser_mode_requires_explicit_structured_accept_header(self):
        self.assertTrue(_wants_raw_browser_response({"Accept": "application/json"}))
        self.assertTrue(
            _wants_raw_browser_response({"accept": "application/javascript, application/json"})
        )
        self.assertFalse(_wants_raw_browser_response({"Accept": "text/html"}))
        self.assertFalse(_wants_raw_browser_response(None))

    def test_browser_headers_keep_only_safe_content_negotiation(self):
        headers = {
            "accept": "application/javascript, application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "Proxy-Authorization": "Basic secret",
            "X-Api-Key": "secret",
            "Referer": "https://private.example/account",
            "X-Test": "contract",
        }

        self.assertEqual(
            _safe_browser_headers(headers),
            {
                "Accept": "application/javascript, application/json",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def test_browser_headers_reject_malformed_safe_values(self):
        self.assertEqual(
            _safe_browser_headers(
                {
                    "Accept": "application/json\r\nAuthorization: Bearer secret",
                    "Accept-Language": None,
                }
            ),
            {},
        )

    def test_browser_page_never_receives_sensitive_caller_headers(self):
        class Browser:
            def new_page(self, **kwargs):
                self.kwargs = kwargs
                return object()

        browser = Browser()
        page = _new_browser_page(
            browser,
            {
                "Accept": "application/json",
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "Proxy-Authorization": "Basic secret",
            },
        )

        self.assertIsNotNone(page)
        self.assertEqual(
            browser.kwargs["extra_http_headers"],
            {"Accept": "application/json"},
        )
        self.assertNotIn("secret", repr(browser.kwargs))

    def test_job_search_interaction_succeeds_with_one_bounded_click(self):
        page = FakeInteractionPage()
        field = FakeInteractionControl(
            attrs={"name": "keywords", "id": "job-query", "type": "search"}
        )

        def show_results():
            page.url = "https://jobs.example.com/search?keywords=Platform+Engineer"
            page.dom = "<main>Platform Engineer results</main>"

        button = FakeInteractionControl(
            text="  Search\n jobs  ",
            on_click=show_results,
        )
        form = FakeInteractionForm([field], [button])
        page.forms = [form]

        html, final_url = _execute_job_search_interaction(
            page,
            page.url,
            _job_search_interaction(),
            timeout_seconds=2,
            timeout_error_type=TimeoutError,
            clock=lambda: 10.0,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(final_url, page.url)
        self.assertIn("Platform Engineer results", html)
        self.assertEqual(field.fill_calls[0][0], "Platform Engineer")
        self.assertEqual(len(button.click_calls), 1)
        self.assertEqual(page.locator_calls, ["form"])
        self.assertEqual(form.locator_calls, ["input", "button"])
        self.assertEqual(
            page.goto_calls[0][1]["wait_until"],
            "domcontentloaded",
        )

    def test_hcs_placeholder_field_and_span_action_succeed(self):
        page = FakeInteractionPage(url="https://careers.hcs.example/jobs")
        field = FakeInteractionControl(
            attrs={"placeholder": "Job Title", "type": "text"}
        )

        def show_results():
            page.dom = "<main>Platform Engineer</main>"

        span = FakeInteractionControl(
            attrs={"class": "btn btn-primary"},
            text=" Find   Jobs ",
            on_click=show_results,
        )
        form = FakeInteractionForm(
            [field],
            [],
            controls_by_tag={"span": [span]},
        )
        page.forms = [form]
        interaction = JobSearchInteraction(
            form_ordinal=0,
            query_name=None,
            query_placeholder="Job Title",
            target_title="Platform Engineer",
            submit_text="Find Jobs",
            submit_tag="span",
        )

        html, final_url = _execute_job_search_interaction(
            page,
            page.url,
            interaction,
            timeout_seconds=2,
            timeout_error_type=TimeoutError,
            clock=lambda: 10.0,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(final_url, page.url)
        self.assertIn("Platform Engineer", html)
        self.assertEqual(field.fill_calls[0][0], "Platform Engineer")
        self.assertEqual(len(span.click_calls), 1)
        self.assertEqual(form.locator_calls, ["input", "span"])

    def test_span_submit_requires_declared_action_semantics(self):
        page = FakeInteractionPage()
        field = FakeInteractionControl(
            attrs={"name": "keywords", "id": "job-query", "type": "text"}
        )
        inert_span = FakeInteractionControl(
            attrs={"class": "label primary"},
            text="Search jobs",
        )
        page.forms = [
            FakeInteractionForm(
                [field],
                [],
                controls_by_tag={"span": [inert_span]},
            )
        ]
        interaction = JobSearchInteraction(
            form_ordinal=0,
            query_name="keywords",
            query_id="job-query",
            target_title="Platform Engineer",
            submit_text="Search jobs",
            submit_tag="span",
        )

        with self.assertRaisesRegex(FetchError, "control match is ambiguous"):
            _execute_job_search_interaction(
                page,
                page.url,
                interaction,
                timeout_seconds=1,
                timeout_error_type=TimeoutError,
                clock=lambda: 10.0,
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(inert_span.click_calls, [])

    def test_job_search_interaction_rejects_ambiguous_controls(self):
        duplicate_fields = [
            FakeInteractionControl(
                attrs={"name": "keywords", "id": "job-query", "type": "text"}
            ),
            FakeInteractionControl(
                attrs={"name": "keywords", "id": "job-query", "type": "search"}
            ),
        ]
        page = FakeInteractionPage()
        page.forms = [
            FakeInteractionForm(
                duplicate_fields,
                [FakeInteractionControl(text="Search jobs")],
            )
        ]

        with self.assertRaisesRegex(FetchError, "field match is ambiguous"):
            _execute_job_search_interaction(
                page,
                page.url,
                _job_search_interaction(),
                timeout_seconds=1,
                timeout_error_type=TimeoutError,
                clock=lambda: 10.0,
                sleeper=lambda _seconds: None,
            )

        page.forms = [
            FakeInteractionForm(
                [duplicate_fields[0]],
                [
                    FakeInteractionControl(
                        attrs={"type": "button"},
                        text="Search jobs",
                    ),
                    FakeInteractionControl(
                        attrs={"type": "button", "formaction": "/other"},
                        text=" search   JOBS ",
                    ),
                ],
            )
        ]
        with self.assertRaisesRegex(FetchError, "control match is ambiguous"):
            _execute_job_search_interaction(
                page,
                page.url,
                _job_search_interaction(),
                timeout_seconds=1,
                timeout_error_type=TimeoutError,
                clock=lambda: 10.0,
                sleeper=lambda _seconds: None,
            )

    def test_job_search_interaction_accepts_equivalent_responsive_submit_duplicates(self):
        page = FakeInteractionPage()
        field = FakeInteractionControl(
            attrs={"name": "keywords", "id": "job-query", "type": "search"}
        )

        def show_results():
            page.dom = "<main>Platform Engineer results</main>"

        primary = FakeInteractionControl(
            attrs={"type": "button"},
            text="Search jobs",
            on_click=show_results,
        )
        responsive_copy = FakeInteractionControl(
            attrs={"type": "button"},
            text=" search   JOBS ",
        )
        page.forms = [FakeInteractionForm([field], [primary, responsive_copy])]

        html, _final_url = _execute_job_search_interaction(
            page,
            page.url,
            _job_search_interaction(),
            timeout_seconds=1,
            timeout_error_type=TimeoutError,
            clock=lambda: 10.0,
            sleeper=lambda _seconds: None,
        )

        self.assertIn("Platform Engineer results", html)
        self.assertEqual(len(primary.click_calls), 1)
        self.assertEqual(responsive_copy.click_calls, [])

    def test_job_search_interaction_rejects_unsafe_redirect(self):
        page = FakeInteractionPage()
        field = FakeInteractionControl(
            attrs={"name": "keywords", "id": "job-query", "type": "text"}
        )

        def redirect_off_origin():
            page.url = "https://attacker.example/results"
            page.dom = "<main>redirected</main>"

        page.forms = [
            FakeInteractionForm(
                [field],
                [
                    FakeInteractionControl(
                        text="Search jobs",
                        on_click=redirect_off_origin,
                    )
                ],
            )
        ]

        with self.assertRaisesRegex(FetchError, "unsafe document origin"):
            _execute_job_search_interaction(
                page,
                page.url,
                _job_search_interaction(),
                timeout_seconds=1,
                timeout_error_type=TimeoutError,
                clock=lambda: 10.0,
                sleeper=lambda _seconds: None,
            )

    def test_browser_interaction_blocks_post_requests(self):
        class Route:
            def __init__(self, method):
                self.request = type("Request", (), {"method": method})()
                self.action = None

            def abort(self):
                self.action = "abort"

            def continue_(self):
                self.action = "continue"

        post_route = Route("POST")
        get_route = Route("GET")

        _route_safe_browser_method(post_route)
        _route_safe_browser_method(get_route)

        self.assertEqual(post_route.action, "abort")
        self.assertEqual(get_route.action, "continue")

    def test_job_search_interaction_rejects_no_state_change(self):
        page = FakeInteractionPage()
        field = FakeInteractionControl(
            attrs={"name": "keywords", "id": "job-query", "type": "text"}
        )
        button = FakeInteractionControl(text="Search jobs")
        page.forms = [FakeInteractionForm([field], [button])]

        with self.assertRaisesRegex(FetchError, "no URL or DOM state change"):
            _execute_job_search_interaction(
                page,
                page.url,
                _job_search_interaction(),
                timeout_seconds=0.1,
                timeout_error_type=TimeoutError,
                clock=lambda: 10.0,
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(len(button.click_calls), 1)

    def test_browser_interaction_capability_unavailable_without_context(self):
        with self.assertRaisesRegex(
            RenderCapabilityUnavailable,
            "browser contexts are unavailable",
        ):
            _new_interaction_page(object(), {"Authorization": "Bearer secret"})

    def test_interaction_context_is_fresh_routed_and_credential_free(self):
        class Context:
            def __init__(self):
                self.route_call = None

            def route(self, pattern, handler):
                self.route_call = (pattern, handler)

            def new_page(self):
                return "fresh page"

        class Browser:
            def new_context(self, **kwargs):
                self.kwargs = kwargs
                self.context = Context()
                return self.context

        browser = Browser()

        context, page = _new_interaction_page(
            browser,
            {
                "Accept-Language": "en-US",
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
            },
        )

        self.assertEqual(page, "fresh page")
        self.assertIs(context, browser.context)
        self.assertEqual(
            browser.kwargs["extra_http_headers"],
            {"Accept-Language": "en-US"},
        )
        self.assertEqual(context.route_call[0], "**/*")
        self.assertIs(context.route_call[1], _route_safe_browser_method)
        self.assertNotIn("secret", repr(browser.kwargs))

    def test_raw_browser_response_preserves_jsonp_body(self):
        body = b'CWS.jobs.jobCallback({"jobs":[{"id":"123"}]})'

        class Response:
            status = 200
            url = "https://api.example.com/job?callback=CWS.jobs.jobCallback"
            headers = {"content-type": "application/javascript; charset=utf-8"}

            def body(self):
                return body

        class Page:
            def goto(self, url, **kwargs):
                self.call = (url, kwargs)
                return Response()

        page = Page()
        text, final_url = _fetch_raw_browser_response(
            page,
            "https://api.example.com/job",
            timeout_seconds=2,
        )

        self.assertEqual(text, body.decode())
        self.assertIn("callback=", final_url)
        self.assertEqual(page.call[1], {"wait_until": "commit", "timeout": 2000})

    def test_raw_browser_response_rejects_html_and_error_status(self):
        class Response:
            url = "https://api.example.com/job"
            headers = {"content-type": "text/html"}

            def __init__(self, status, body):
                self.status = status
                self._body = body

            def body(self):
                return self._body

        class Page:
            def __init__(self, response):
                self.response = response

            def goto(self, url, **kwargs):
                return self.response

        with self.assertRaisesRegex(FetchError, "unsupported content type"):
            _fetch_raw_browser_response(
                Page(Response(200, b"<html></html>")),
                "https://api.example.com/job",
                timeout_seconds=1,
            )
        with self.assertRaisesRegex(FetchError, "HTTP 503"):
            _fetch_raw_browser_response(
                Page(Response(503, b"unavailable")),
                "https://api.example.com/job",
                timeout_seconds=1,
            )

    def test_raw_browser_response_rejects_oversized_body(self):
        class Response:
            status = 200
            url = "https://api.example.com/job"
            headers = {"content-type": "application/json"}

            def body(self):
                return b"x" * (rendered_fetcher.MAX_RAW_BROWSER_RESPONSE_BYTES + 1)

        class Page:
            def goto(self, url, **kwargs):
                return Response()

        with self.assertRaisesRegex(FetchError, "byte limit"):
            _fetch_raw_browser_response(
                Page(),
                "https://api.example.com/job",
                timeout_seconds=1,
            )

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

        times = iter((10.0, 10.4, 11.9))
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
        self.assertLess(page.calls[1][2]["timeout"], 1600)

    def test_domcontentloaded_timeout_salvages_usable_job_dom_without_more_waiting(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            url = "https://example.com/careers"

            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))
                raise NavigationTimeout("DOMContentLoaded did not fire")

            def content(self):
                self.calls.append(("content",))
                return (
                    "<main><h1>Careers at Example</h1>"
                    "<p>Join our team and build products used by customers around the world. "
                    "We support thoughtful collaboration, learning, and meaningful career growth.</p>"
                    '<a href="/careers/jobs/123">View open job</a></main>'
                )

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))

        page = FakePage()

        _navigate_with_settle(
            page,
            page.url,
            timeout_seconds=2,
            timeout_error_type=NavigationTimeout,
            clock=lambda: 10.0,
        )

        self.assertEqual([call[0] for call in page.calls], ["goto", "content"])
        self.assertEqual(page.calls[0][2]["timeout"], 2000)

    def test_domcontentloaded_timeout_salvages_substantial_career_dom_without_job_link(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            url = "https://apply.example/careers"

            def goto(self, url, **kwargs):
                raise NavigationTimeout("DOMContentLoaded did not fire")

            def content(self):
                return (
                    "<main><h1>Careers at Example</h1>"
                    "<p>We are building the future of workplace technology. Join our team of "
                    "engineers, designers, and customer advocates working across several offices.</p>"
                    '<a href="/careers">View jobs</a></main>'
                )

            def wait_for_load_state(self, state, **kwargs):
                self.fail("networkidle must not run after navigation timeout")

        _navigate_with_settle(
            FakePage(),
            FakePage.url,
            timeout_seconds=2,
            timeout_error_type=NavigationTimeout,
            clock=lambda: 10.0,
        )

    def test_domcontentloaded_timeout_rejects_empty_javascript_shell(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            url = "https://example.com/careers"

            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))
                raise NavigationTimeout("DOMContentLoaded did not fire")

            def content(self):
                self.calls.append(("content",))
                return (
                    "<html><body><noscript>You need to enable JavaScript to run this app.</noscript>"
                    '<div id="root"></div><script src="/app.js"></script></body></html>'
                )

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))

        page = FakePage()

        with self.assertRaisesRegex(NavigationTimeout, "DOMContentLoaded did not fire"):
            _navigate_with_settle(
                page,
                page.url,
                timeout_seconds=2,
                timeout_error_type=NavigationTimeout,
                clock=lambda: 10.0,
            )

        self.assertEqual([call[0] for call in page.calls], ["goto", "content"])

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

    def test_navigation_spends_only_remaining_budget_waiting_for_client_job_dom(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))

            def wait_for_function(self, expression, **kwargs):
                self.calls.append(("function", expression, kwargs))

        times = iter((30.0, 30.4, 30.9))
        page = FakePage()

        _navigate_with_settle(
            page,
            "https://example.com/careers",
            timeout_seconds=2,
            timeout_error_type=NavigationTimeout,
            clock=lambda: next(times),
        )

        self.assertEqual([call[0] for call in page.calls], ["goto", "wait", "function"])
        self.assertLessEqual(page.calls[2][2]["timeout"], 1100)
        self.assertIn("querySelectorAll('a[href]')", page.calls[2][1])
        self.assertIn("profile\\/job_details", page.calls[2][1])
        self.assertIn("jobs.lever.co", page.calls[2][1])
        self.assertIn("jobs.ashbyhq.com", page.calls[2][1])
        self.assertIn("apply.workable.com", page.calls[2][1])
        self.assertIn("jobs.smartrecruiters.com", page.calls[2][1])
        self.assertIn("annonces", page.calls[2][1])
        self.assertNotIn("hasJobText", page.calls[2][1])

    def test_networkidle_timeout_preserves_reserved_job_dom_wait(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            def __init__(self):
                self.calls = []

            def goto(self, url, **kwargs):
                self.calls.append(("goto", url, kwargs))

            def wait_for_load_state(self, state, **kwargs):
                self.calls.append(("wait", state, kwargs))
                raise NavigationTimeout("stream remains open")

            def wait_for_function(self, expression, **kwargs):
                self.calls.append(("function", expression, kwargs))

        times = iter((50.0, 50.2, 51.4))
        page = FakePage()

        _navigate_with_settle(
            page,
            "https://example.com/jobs",
            timeout_seconds=2,
            timeout_error_type=NavigationTimeout,
            clock=lambda: next(times),
        )

        self.assertEqual([call[0] for call in page.calls], ["goto", "wait", "function"])
        self.assertLess(page.calls[1][2]["timeout"], 1800)
        self.assertLessEqual(page.calls[2][2]["timeout"], 600)

    def test_client_job_dom_wait_timeout_is_soft(self):
        class NavigationTimeout(Exception):
            pass

        class FakePage:
            def goto(self, url, **kwargs):
                return None

            def wait_for_load_state(self, state, **kwargs):
                return None

            def wait_for_function(self, expression, **kwargs):
                raise NavigationTimeout("jobs did not render")

        times = iter((40.0, 40.1, 40.2))
        _navigate_with_settle(
            FakePage(),
            "https://example.com/jobs",
            timeout_seconds=1,
            timeout_error_type=NavigationTimeout,
            clock=lambda: next(times),
        )


if __name__ == "__main__":
    unittest.main()
