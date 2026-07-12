from __future__ import annotations

from pathlib import Path
import re
from time import monotonic
from urllib.parse import parse_qs, urlsplit

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
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
                    _navigate_with_settle(
                        page,
                        normalized,
                        timeout_seconds=self.timeout,
                        timeout_error_type=PlaywrightTimeoutError,
                    )
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

        render_reason = self._render_reason(static_page)
        if not render_reason:
            return static_page

        if not self._can_render():
            self._record_render_event(
                url,
                render_reason,
                "skipped_budget",
                source=static_page.source,
            )
            return static_page

        try:
            rendered = self._render_live(url, reason=render_reason)
            rendered.source = _source_with_artifacts("browser_after_static_shell", rendered)
            self._record_render_event(url, render_reason, "success", source=rendered.source)
            return rendered
        except FetchError as exc:
            self._record_render_event(url, render_reason, "failed", source=static_page.source, error=str(exc))
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
        return self._render_reason(page) is not None

    def _render_reason(self, page: Page) -> str | None:
        html = page.html[:250000]
        lower_html = html.lower()
        visible_text = _visible_text(html)
        lower_text = visible_text.lower()
        if any(marker in visible_text.lower() for marker in ("enable javascript", "please enable js")):
            return "javascript_required"
        if _looks_like_structured_payload(html):
            return None

        links = extract_links(page)
        source_url = page.final_url or page.url
        if any(_is_usable_job_link(link.url, link.text, source_url) for link in links):
            return None

        has_shell_marker = any(marker in lower_html for marker in JS_SHELL_MARKERS)
        if has_shell_marker and _has_job_context(page.final_url or page.url, lower_text):
            return "static_no_usable_job_links"
        if has_shell_marker and len(visible_text) < self.min_visible_text_chars:
            return "static_shell"
        return None


JS_SHELL_MARKERS = (
    'id="app"',
    "id='app'",
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

JOB_URL_MARKERS = (
    "/career",
    "/job",
    "/opening",
    "/position",
    "/vacanc",
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "bamboohr.com",
    "icims.com",
    "successfactors.com",
    "sapsf.com",
    "workable.com",
)

JOB_TEXT_MARKERS = (
    "career",
    "job",
    "open role",
    "open position",
    "join our team",
    "vacanc",
    "we are hiring",
    "we're hiring",
)


def _is_usable_job_link(url: str, text: str, source_url: str) -> bool:
    try:
        if normalize_url(url) == normalize_url(source_url):
            return False
    except (TypeError, ValueError):
        return False
    parsed = urlsplit(url)
    source = urlsplit(source_url)
    path = parsed.path.rstrip("/") or "/"
    source_path = source.path.rstrip("/") or "/"
    if parsed.netloc.lower() == source.netloc.lower() and path == source_path:
        query_keys = {key.casefold() for key in parse_qs(parsed.query, keep_blank_values=True)}
        if not query_keys or query_keys <= {"lang", "lng", "locale"}:
            return False
    if re.search(r"\.(?:avif|css|gif|ico|jpe?g|js|json|map|png|svg|txt|webp|xml)$", path, re.I):
        return False
    lower_url = url.lower()
    if any(marker in lower_url for marker in JOB_URL_MARKERS):
        return True
    if not any(marker in text.lower() for marker in JOB_TEXT_MARKERS):
        return False
    return True


def _has_job_context(url: str, visible_text: str) -> bool:
    candidate = f"{url} {visible_text}".lower()
    return any(marker in candidate for marker in JOB_URL_MARKERS + JOB_TEXT_MARKERS)


def _looks_like_structured_payload(html: str) -> bool:
    stripped = html.lstrip()
    if not stripped.startswith(("{", "[")):
        return False
    lower = stripped[:2000].lower()
    return any(
        marker in lower
        for marker in ('"jobs"', '"jobpostings"', '"postings"', '"results"', '"items"')
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


def _navigate_with_settle(
    page,
    url: str,
    *,
    timeout_seconds: float,
    timeout_error_type,
    clock=monotonic,
) -> None:
    """Navigate within one budget, salvaging a useful DOM after readiness timeout."""

    timeout_ms = max(1, int(timeout_seconds * 1000))
    deadline = clock() + timeout_seconds
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except timeout_error_type:
        # Chromium can have a useful application DOM even when a slow script,
        # frame, or response prevents the DOMContentLoaded lifecycle event.
        # Snapshot once without adding another browser wait; empty JS shells
        # still fail with the original navigation timeout.
        if not _has_usable_rendered_dom(page, url):
            raise
        return
    remaining_ms = int((deadline - clock()) * 1000)
    if remaining_ms <= 0:
        return
    try:
        page.wait_for_load_state("networkidle", timeout=remaining_ms)
    except timeout_error_type:
        # Analytics, long polling, and streaming requests can keep a useful job
        # page permanently non-idle. DOM readiness remains the hard boundary.
        return
    remaining_ms = int((deadline - clock()) * 1000)
    if remaining_ms > 0:
        _wait_for_job_dom(page, url, remaining_ms, timeout_error_type)


def _wait_for_job_dom(page, url: str, timeout_ms: int, timeout_error_type) -> None:
    """Spend only remaining navigation budget waiting for client-rendered job evidence."""

    wait_for_function = getattr(page, "wait_for_function", None)
    if wait_for_function is None or not _has_job_context(url, ""):
        return
    expression = """() => {
      const body = document.body;
      if (!body) return false;
      const text = (body.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
      const hasJobLink = Array.from(document.querySelectorAll('a[href]')).some((link) =>
        /(?:career|job|opening|position|vacanc)/i.test(link.href || '')
      );
      const hasJobText = text.length >= 120 &&
        /(?:career|job|open role|open position|join our team|vacanc|we are hiring|we're hiring)/i.test(text);
      return hasJobLink || hasJobText;
    }"""
    try:
        wait_for_function(expression, timeout=max(1, timeout_ms))
    except timeout_error_type:
        return


def _has_usable_rendered_dom(page, requested_url: str) -> bool:
    try:
        html = page.content()
    except Exception:
        return False
    if not isinstance(html, str) or not html.strip():
        return False

    current_url = getattr(page, "url", None) or requested_url
    rendered_page = Page(
        url=requested_url,
        final_url=current_url,
        html=html,
        source="browser_navigation_timeout",
    )
    try:
        links = extract_links(rendered_page)
    except (TypeError, ValueError):
        links = []
    if any(_is_usable_job_link(link.url, link.text, current_url) for link in links):
        return True

    visible_text = _visible_text(html)
    if len(visible_text) < 120:
        return False
    lower_text = visible_text.casefold()
    return any(marker in lower_text for marker in JOB_TEXT_MARKERS)


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
