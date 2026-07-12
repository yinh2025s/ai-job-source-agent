from __future__ import annotations

from pathlib import Path
import re

from .web import FetchError, Fetcher, Page, extract_links, normalize_url


class RenderedFetcher(Fetcher):
    """Fetch pages through a real browser when static HTML is insufficient.

    This is intentionally optional so the default demo stays dependency-free.
    Install with `pip install -e ".[browser]"` and run `playwright install
    chromium` before using `--render-js`.
    """

    def __init__(self, *args, capture_screenshot: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.capture_screenshot = capture_screenshot

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
                browser = _launch_browser(playwright, PlaywrightError)
                try:
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
                    artifacts = {}
                    if self.capture_screenshot:
                        artifacts["screenshot_png"] = page.screenshot(full_page=True)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise FetchError(str(exc)) from exc

        page = Page(url=normalized, html=html, final_url=final_url, source="browser", artifacts=artifacts)
        page.source = _source_with_artifacts(page.source, page)
        return page


class SmartRenderedFetcher(Fetcher):
    """Use static HTML first, then render only when it is likely useful."""

    def __init__(
        self,
        *args,
        render_budget: int = 3,
        min_visible_text_chars: int = 120,
        capture_screenshot: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.render_budget = render_budget
        self.min_visible_text_chars = min_visible_text_chars
        self.capture_screenshot = capture_screenshot
        self.render_attempts = 0
        self.render_events: list[dict[str, str | int]] = []

    def _fetch_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        if data is not None:
            return self._static_live(url, data=data, headers=headers)

        try:
            static_page = self._static_live(url, data=data, headers=headers)
        except FetchError as exc:
            if not self._can_render():
                raise
            try:
                rendered = self._render_live(url, reason="static_error")
            except FetchError as render_exc:
                self._record_render_event(url, "static_error", "failed", source="browser", error=str(render_exc))
                raise
            rendered.source = _source_with_artifacts("browser_after_static_error", rendered)
            self._record_render_event(url, "static_error", "success", source=rendered.source, error=str(exc))
            return rendered

        if not self._should_render(static_page) or not self._can_render():
            return static_page

        try:
            rendered = self._render_live(url, reason="static_shell")
            rendered.source = _source_with_artifacts("browser_after_static_shell", rendered)
            self._record_render_event(url, "static_shell", "success", source=rendered.source)
            return rendered
        except FetchError as exc:
            self._record_render_event(url, "static_shell", "failed", source=static_page.source, error=str(exc))
            return static_page

    def _static_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        return super()._fetch_live(url, data=data, headers=headers)

    def _render_live(self, url: str, reason: str = "manual") -> Page:
        self.render_attempts += 1
        return RenderedFetcher(timeout=self.timeout, capture_screenshot=self.capture_screenshot)._fetch_live(url)

    def _record_render_event(
        self,
        url: str,
        reason: str,
        outcome: str,
        source: str,
        error: str | None = None,
    ) -> None:
        event: dict[str, str | int] = {
            "url": url,
            "reason": reason,
            "outcome": outcome,
            "source": source,
            "attempt": self.render_attempts,
        }
        if error:
            event["error"] = error
        self.render_events.append(event)

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


def _launch_browser(playwright, playwright_error_type):
    try:
        return playwright.chromium.launch(headless=True)
    except playwright_error_type as exc:
        if not _local_chrome_available():
            raise
        try:
            return playwright.chromium.launch(channel="chrome", headless=True)
        except playwright_error_type as chrome_exc:
            raise playwright_error_type(f"{exc}; local Chrome fallback failed: {chrome_exc}") from chrome_exc


def _local_chrome_available() -> bool:
    return any(
        Path(path).exists()
        for path in (
            "/Applications/Google Chrome.app",
            "/Applications/Chromium.app",
            "/Applications/Microsoft Edge.app",
        )
    )


def _source_with_artifacts(source: str, page: Page) -> str:
    source_parts = source.split("|")
    existing = set(source_parts)
    for artifact_name in sorted(page.artifacts or {}):
        marker = f"artifact:{artifact_name}"
        if marker not in existing:
            source_parts.append(marker)
    return "|".join(source_parts)
