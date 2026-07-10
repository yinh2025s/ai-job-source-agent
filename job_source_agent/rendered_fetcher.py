from __future__ import annotations

import re

from .web import FetchError, Fetcher, Page, extract_links, normalize_url


class RenderedFetcher(Fetcher):
    """Fetch pages through a real browser when static HTML is insufficient.

    This is intentionally optional so the default demo stays dependency-free.
    Install with `pip install -e ".[browser]"` and run `playwright install
    chromium` before using `--render-js`.
    """

    def _fetch_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        if data is not None:
            return super()._fetch_live(url, data=data, headers=headers)

        normalized = normalize_url(url)
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                "Playwright is not installed. Install with: "
                'pip install -e ".[browser]" && playwright install chromium'
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0 Safari/537.36"
                    )
                )
                page.goto(normalized, wait_until="networkidle", timeout=int(self.timeout * 1000))
                html = page.content()
                final_url = page.url
                browser.close()
        except PlaywrightError as exc:
            raise FetchError(str(exc)) from exc

        return Page(url=normalized, html=html, final_url=final_url, source="browser")


class SmartRenderedFetcher(Fetcher):
    """Use static HTML first, then render only when it is likely useful."""

    def __init__(
        self,
        *args,
        render_budget: int = 3,
        min_visible_text_chars: int = 120,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.render_budget = render_budget
        self.min_visible_text_chars = min_visible_text_chars
        self.render_attempts = 0

    def _fetch_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        if data is not None:
            return self._static_live(url, data=data, headers=headers)

        try:
            static_page = self._static_live(url, data=data, headers=headers)
        except FetchError:
            if not self._can_render():
                raise
            rendered = self._render_live(url)
            rendered.source = "browser_after_static_error"
            return rendered

        if not self._should_render(static_page) or not self._can_render():
            return static_page

        try:
            rendered = self._render_live(url)
            rendered.source = "browser_after_static_shell"
            return rendered
        except FetchError:
            return static_page

    def _static_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        return super()._fetch_live(url, data=data, headers=headers)

    def _render_live(self, url: str) -> Page:
        self.render_attempts += 1
        return RenderedFetcher(timeout=self.timeout)._fetch_live(url)

    def _can_render(self) -> bool:
        return self.render_attempts < self.render_budget

    def _should_render(self, page: Page) -> bool:
        html = page.html[:250000]
        lower_html = html.lower()
        visible_text = _visible_text(html)
        if any(marker in visible_text.lower() for marker in ("enable javascript", "please enable js")):
            return True
        if len(visible_text) >= self.min_visible_text_chars:
            return False
        if len(extract_links(page)) >= 3:
            return False
        return any(marker in lower_html for marker in JS_SHELL_MARKERS)


JS_SHELL_MARKERS = (
    'id="root"',
    "id='root'",
    'id="__next"',
    "id='__next'",
    "<app-root",
    "data-reactroot",
    "window.__initial_state__",
    "window.__apollo_state__",
    "webpack",
    "vite",
)


def _visible_text(html: str) -> str:
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())
