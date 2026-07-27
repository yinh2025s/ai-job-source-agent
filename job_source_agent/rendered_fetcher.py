from __future__ import annotations

from math import ceil
from pathlib import Path
import re
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit

from .browser_interaction import BrowserInteraction, JobSearchInteraction
from .web import FetchError, Fetcher, Page, extract_links, normalize_url


_STATIC_ONLY_SEARCH_HOSTS = frozenset(
    {
        "bing.com",
        "www.bing.com",
        "duckduckgo.com",
        "html.duckduckgo.com",
    }
)
FORCE_RENDER_HEADER = "X-Job-Source-Agent-Render"


class RenderCapabilityUnavailable(FetchError):
    """The configured renderer cannot run in the current environment."""


class RenderedFetcher(Fetcher):
    """Fetch pages through a real browser when static HTML is insufficient.

    This is intentionally optional so the default demo stays dependency-free.
    Install with `pip install -e ".[browser]"` and run `playwright install
    chromium` before using `--render-js`.
    """

    def __init__(self, *args, capture_screenshot: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.capture_screenshot = capture_screenshot

    def _fetch_live(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        if data is not None:
            if interaction is not None:
                raise FetchError(
                    "browser interaction cannot be combined with request data",
                    reason_code="OPENING_DISCOVERY_INCOMPLETE",
                    retryable=False,
                )
            return super()._fetch_live(url, data=data, headers=headers)

        normalized = normalize_url(url)
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderCapabilityUnavailable(
                "Playwright is not installed. Install with: "
                'pip install -e ".[browser]" && playwright install chromium'
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = _launch_browser(playwright, PlaywrightError)
                context = None
                try:
                    if interaction is not None:
                        context, page = _new_interaction_page(browser, headers)
                        html, final_url = _execute_job_search_interaction(
                            page,
                            normalized,
                            interaction,
                            timeout_seconds=self.timeout,
                            timeout_error_type=PlaywrightTimeoutError,
                        )
                    else:
                        page = _new_browser_page(browser, headers)
                    if interaction is None and _wants_raw_browser_response(headers):
                        html, final_url = _fetch_raw_browser_response(
                            page,
                            normalized,
                            timeout_seconds=self.timeout,
                        )
                    elif interaction is None:
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
                    if context is not None:
                        context.close()
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
        explicit_action_render_budget: int = 3,
        min_visible_text_chars: int = 120,
        capture_screenshot: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.render_budget = render_budget
        self.explicit_action_render_budget = explicit_action_render_budget
        self.min_visible_text_chars = min_visible_text_chars
        self.capture_screenshot = capture_screenshot
        self.render_attempts = 0
        self.explicit_action_render_attempts = 0
        self.render_events: list[dict[str, str | int]] = []
        self._render_capability_error: str | None = None

    @property
    def supports_forced_render(self) -> bool:
        return True

    def _fetch_live(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        request_headers = dict(headers or {})
        force_render = (
            request_headers.pop(FORCE_RENDER_HEADER, "").casefold() == "force"
        )
        explicit_action = force_render or interaction is not None
        headers = request_headers or None
        if data is not None:
            if interaction is not None:
                raise FetchError(
                    "browser interaction cannot be combined with request data",
                    reason_code="OPENING_DISCOVERY_INCOMPLETE",
                    retryable=False,
                )
            return self._static_live(url, data=data, headers=headers)

        try:
            static_page = self._static_live(url, data=data, headers=headers)
        except FetchError as static_exc:
            if _is_static_only_search_url(url):
                self._record_render_event(
                    url,
                    "static_error",
                    "skipped_static_only_source",
                    source="static_error",
                    error=str(static_exc),
                )
                raise
            if self._render_capability_error is not None:
                self._record_render_event(
                    url,
                    "static_error",
                    "skipped_unavailable",
                    source="static_error",
                )
                raise
            render_reason = (
                "job_search_interaction"
                if interaction is not None
                else "explicit_career_action"
                if force_render
                else "static_error"
            )
            use_explicit_reserve = self._uses_explicit_action_reserve(explicit_action)
            if not self._can_render(explicit_action=explicit_action):
                raise
            if use_explicit_reserve:
                self.explicit_action_render_attempts += 1
            try:
                rendered = self._render_with_optional_interaction(
                    url,
                    reason=render_reason,
                    headers=headers,
                    interaction=interaction,
                )
            except RenderCapabilityUnavailable as render_exc:
                if use_explicit_reserve:
                    self.explicit_action_render_attempts -= 1
                self._record_render_event(
                    url,
                    render_reason,
                    "capability_unavailable",
                    source="static_error",
                    error=str(render_exc),
                )
                raise static_exc
            except FetchError as render_exc:
                self._record_render_event(url, render_reason, "failed", source="browser", error=str(render_exc))
                raise
            rendered.source = _source_with_artifacts("browser_after_static_error", rendered)
            self._record_render_event(url, render_reason, "success", source=rendered.source, error=str(static_exc))
            return rendered

        render_reason = (
            "job_search_interaction"
            if interaction is not None
            else "explicit_career_action"
            if force_render
            else self._render_reason(static_page)
        )
        if not render_reason:
            return static_page

        if self._render_capability_error is not None:
            self._record_render_event(
                url,
                render_reason,
                "skipped_unavailable",
                source=static_page.source,
            )
            return static_page

        use_explicit_reserve = self._uses_explicit_action_reserve(explicit_action)
        if not self._can_render(explicit_action=explicit_action):
            self._record_render_event(
                url,
                render_reason,
                "skipped_budget",
                source=static_page.source,
            )
            return static_page

        if use_explicit_reserve:
            self.explicit_action_render_attempts += 1
        try:
            rendered = self._render_with_optional_interaction(
                url,
                reason=render_reason,
                headers=headers,
                interaction=interaction,
            )
            rendered.source = _source_with_artifacts("browser_after_static_shell", rendered)
            self._record_render_event(url, render_reason, "success", source=rendered.source)
            return rendered
        except RenderCapabilityUnavailable as exc:
            if use_explicit_reserve:
                self.explicit_action_render_attempts -= 1
            self._record_render_event(
                url,
                render_reason,
                "capability_unavailable",
                source=static_page.source,
                error=str(exc),
            )
            return static_page
        except FetchError as exc:
            self._record_render_event(url, render_reason, "failed", source=static_page.source, error=str(exc))
            return static_page

    def _static_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        return super()._fetch_live(url, data=data, headers=headers)

    def _render_live(
        self,
        url: str,
        reason: str = "manual",
        headers: dict[str, str] | None = None,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        if self._render_capability_error is not None:
            raise RenderCapabilityUnavailable(self._render_capability_error)
        self.render_attempts += 1
        try:
            return RenderedFetcher(timeout=self.timeout, capture_screenshot=self.capture_screenshot)._fetch_live(
                url,
                headers=headers,
                interaction=interaction,
            )
        except RenderCapabilityUnavailable as exc:
            self.render_attempts -= 1
            self._render_capability_error = str(exc)
            raise

    def _render_with_optional_interaction(
        self,
        url: str,
        *,
        reason: str,
        headers: dict[str, str] | None,
        interaction: BrowserInteraction | None,
    ) -> Page:
        if interaction is None:
            return self._render_live(url, reason=reason, headers=headers)
        return self._render_live(
            url,
            reason=reason,
            headers=headers,
            interaction=interaction,
        )

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

    def _can_render(self, *, explicit_action: bool = False) -> bool:
        return bool(
            self.render_attempts < self.render_budget
            or (
                explicit_action
                and self.explicit_action_render_attempts
                < self.explicit_action_render_budget
            )
        )

    def _uses_explicit_action_reserve(self, explicit_action: bool) -> bool:
        return bool(
            explicit_action
            and self.render_attempts >= self.render_budget
            and self.explicit_action_render_attempts
            < self.explicit_action_render_budget
        )

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
        if any(
            _is_usable_job_link(link.url, link.text, source_url, origin=link.origin)
            for link in links
        ):
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
    'id="rootdiv"',
    "id='rootdiv'",
    'id="__next"',
    "id='__next'",
    'id="__nuxt"',
    "id='__nuxt'",
    "<app-root",
    "data-reactroot",
    "window.__initial_state__",
    "window.__apollo_state__",
    "webpack",
    "vite",
)


SAFE_BROWSER_HEADER_NAMES = {
    "accept": "Accept",
    "accept-language": "Accept-Language",
}
SAFE_BROWSER_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAFE_INTERACTION_SUBMIT_TAGS = frozenset({"a", "button", "input", "span"})
_ACTION_CLASS_MARKERS = ("btn", "button", "search", "action")
_INTERACTION_POLL_SECONDS = 0.05
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)


def _safe_browser_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Keep only non-credential content negotiation headers for browser use.

    Playwright applies ``extra_http_headers`` to redirects and subresources, so
    forwarding caller credentials here could disclose them to another origin.
    """

    safe_headers: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        canonical_name = SAFE_BROWSER_HEADER_NAMES.get(name.casefold())
        if canonical_name is None or "\r" in value or "\n" in value:
            continue
        safe_headers[canonical_name] = value
    return safe_headers


def _new_browser_page(browser, headers: dict[str, str] | None):
    return browser.new_page(
        user_agent=_BROWSER_USER_AGENT,
        extra_http_headers=_safe_browser_headers(headers),
    )


def _new_interaction_page(browser, headers: dict[str, str] | None):
    """Create an isolated browser context with no persisted login state."""

    new_context = getattr(browser, "new_context", None)
    if not callable(new_context):
        raise RenderCapabilityUnavailable(
            "Playwright browser contexts are unavailable"
        )
    context = new_context(
        user_agent=_BROWSER_USER_AGENT,
        extra_http_headers=_safe_browser_headers(headers),
    )
    route = getattr(context, "route", None)
    new_page = getattr(context, "new_page", None)
    if not callable(route) or not callable(new_page):
        close = getattr(context, "close", None)
        if callable(close):
            close()
        raise RenderCapabilityUnavailable(
            "Playwright request routing is unavailable"
        )
    route("**/*", _route_safe_browser_method)
    return context, new_page()


def _route_safe_browser_method(route) -> None:
    method = str(getattr(route.request, "method", "")).upper()
    if method not in SAFE_BROWSER_METHODS:
        route.abort()
        return
    route.continue_()


def _execute_job_search_interaction(
    page,
    url: str,
    interaction: BrowserInteraction,
    *,
    timeout_seconds: float,
    timeout_error_type,
    clock=monotonic,
    sleeper=sleep,
) -> tuple[str, str]:
    """Execute one frozen job-search action without script or selector input."""

    if not isinstance(interaction, JobSearchInteraction):
        raise FetchError(
            "unsupported browser interaction",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    deadline = clock() + timeout_seconds
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=_remaining_timeout_ms(deadline, clock),
    )
    initial_url = getattr(page, "url", None) or url
    initial_origin = _https_origin(initial_url)
    if initial_origin is None:
        raise FetchError(
            "browser interaction requires an HTTPS initial document",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )

    form = _interaction_form(page, interaction.form_ordinal)
    field = _interaction_query_field(form, interaction)
    submit_control = _interaction_submit_control(form, interaction)
    field.fill(
        interaction.target_title,
        timeout=_remaining_timeout_ms(deadline, clock),
    )
    before_click_dom = page.content()
    click_timeout = _remaining_timeout_ms(deadline, clock)
    try:
        submit_control.click(timeout=click_timeout)
    except timeout_error_type:
        # Playwright may time out while waiting for a navigation it already
        # initiated. The state check below decides whether the single click
        # completed; the action is never retried.
        pass

    final_url, final_dom = _wait_for_interaction_change(
        page,
        initial_url=initial_url,
        initial_dom=before_click_dom,
        deadline=deadline,
        clock=clock,
        sleeper=sleeper,
    )
    final_origin = _https_origin(final_url)
    if final_origin != initial_origin:
        raise FetchError(
            "browser interaction changed to an unsafe document origin",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    return final_dom, final_url


def _interaction_form(page, form_ordinal: int):
    locator = getattr(page, "locator", None)
    if not callable(locator):
        raise RenderCapabilityUnavailable("Playwright locators are unavailable")
    forms = locator("form")
    if forms.count() <= form_ordinal:
        raise FetchError(
            "job-search form is unavailable",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    return forms.nth(form_ordinal)


def _interaction_query_field(form, interaction: JobSearchInteraction):
    semantic_attributes = tuple(
        (name, value)
        for name, value in (
            ("name", interaction.query_name),
            ("id", interaction.query_id),
            ("placeholder", interaction.query_placeholder),
        )
        if value is not None
    )
    if not semantic_attributes:
        raise FetchError(
            "job-search query field has no semantic locator",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    matches = []
    inputs = form.locator("input")
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        input_type = (candidate.get_attribute("type") or "text").casefold()
        if input_type not in {"search", "text"}:
            continue
        if any(
            candidate.get_attribute(name) != value
            for name, value in semantic_attributes
        ):
            continue
        matches.append(candidate)
    if len(matches) != 1:
        raise FetchError(
            "job-search query field match is ambiguous",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    return matches[0]


def _interaction_submit_control(form, interaction: JobSearchInteraction):
    submit_tag = interaction.submit_tag
    if submit_tag not in SAFE_INTERACTION_SUBMIT_TAGS:
        raise FetchError(
            "job-search submit tag is unsupported",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    expected = _normalized_control_text(interaction.submit_text)
    matches = []
    controls = form.locator(submit_tag)
    for index in range(controls.count()):
        candidate = controls.nth(index)
        if not candidate.is_visible() or not candidate.is_enabled():
            continue
        if submit_tag in {"a", "span"} and not _has_action_semantics(candidate):
            continue
        control_text = (
            candidate.get_attribute("value")
            if submit_tag == "input"
            else candidate.inner_text()
        )
        if _normalized_control_text(control_text or "") != expected:
            continue
        matches.append(candidate)
    if not matches:
        raise FetchError(
            "job-search submit control match is ambiguous",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    if len(matches) > 1 and not _equivalent_submit_controls(matches):
        raise FetchError(
            "job-search submit control match is ambiguous",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    return matches[0]


def _equivalent_submit_controls(controls) -> bool:
    fingerprints = {
        tuple((control.get_attribute(name) or "").strip().casefold() for name in (
            "type",
            "name",
            "value",
            "formaction",
        ))
        for control in controls
    }
    if len(fingerprints) != 1:
        return False
    fingerprint = next(iter(fingerprints))
    return not fingerprint[-1]


def _has_action_semantics(control) -> bool:
    if (control.get_attribute("role") or "").casefold() == "button":
        return True
    class_tokens = (control.get_attribute("class") or "").casefold().split()
    return any(
        marker in token
        for token in class_tokens
        for marker in _ACTION_CLASS_MARKERS
    )


def _wait_for_interaction_change(
    page,
    *,
    initial_url: str,
    initial_dom: str,
    deadline: float,
    clock,
    sleeper,
) -> tuple[str, str]:
    remaining_seconds = max(0.0, deadline - clock())
    attempts = max(1, ceil(remaining_seconds / _INTERACTION_POLL_SECONDS))
    final_url = getattr(page, "url", None) or initial_url
    final_dom = page.content()
    for attempt in range(attempts):
        final_url = getattr(page, "url", None) or initial_url
        final_dom = page.content()
        if final_url != initial_url or final_dom != initial_dom:
            return final_url, final_dom
        remaining_seconds = deadline - clock()
        if remaining_seconds <= 0 or attempt + 1 == attempts:
            break
        sleeper(min(_INTERACTION_POLL_SECONDS, remaining_seconds))
    raise FetchError(
        "browser interaction produced no URL or DOM state change",
        reason_code="OPENING_DISCOVERY_INCOMPLETE",
        retryable=False,
    )


def _remaining_timeout_ms(deadline: float, clock) -> int:
    remaining_ms = int((deadline - clock()) * 1000)
    if remaining_ms <= 0:
        raise FetchError(
            "browser interaction exceeded its timeout",
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            retryable=False,
        )
    return remaining_ms


def _https_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            return None
        return "https", parsed.hostname.casefold().rstrip("."), 443
    except (TypeError, ValueError):
        return None


def _normalized_control_text(value: str) -> str:
    return " ".join(str(value).split()).casefold()

MAX_RAW_BROWSER_RESPONSE_BYTES = 2_000_000
RAW_BROWSER_ACCEPT_TYPES = (
    "application/json",
    "application/javascript",
    "text/javascript",
)
RAW_BROWSER_CONTENT_TYPES = RAW_BROWSER_ACCEPT_TYPES + ("text/plain",)

JOB_URL_MARKERS = (
    "/annonces",
    "/career",
    "/job",
    "/offres",
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


def _is_usable_job_link(
    url: str,
    text: str,
    source_url: str,
    *,
    origin: str | None = None,
) -> bool:
    if origin in {"embedded_url", "script_src"}:
        return False
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
    if (
        parsed.netloc.casefold() == source.netloc.casefold()
        and path != source_path
        and _path_without_locale(path) == _path_without_locale(source_path)
    ):
        return False
    if re.search(r"\.(?:avif|css|gif|ico|jpe?g|js|json|map|png|svg|txt|webp|xml)$", path, re.I):
        return False
    lower_path = parsed.path.casefold()
    hostname = (parsed.hostname or "").casefold()
    if any(
        marker in lower_path
        for marker in JOB_URL_MARKERS
        if marker.startswith("/")
    ):
        return True
    if any(
        hostname == marker or hostname.endswith("." + marker)
        for marker in JOB_URL_MARKERS
        if not marker.startswith("/")
    ):
        return True
    if not any(marker in text.lower() for marker in JOB_TEXT_MARKERS):
        return False
    return True


def _path_without_locale(path: str) -> str:
    return re.sub(r"^/[a-z]{2}(?:_[A-Z]{2})?(?=/|$)", "", path) or "/"


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


def _wants_raw_browser_response(headers: dict[str, str] | None) -> bool:
    if not headers:
        return False
    accept = next(
        (
            value
            for key, value in headers.items()
            if isinstance(key, str)
            and key.casefold() == "accept"
            and isinstance(value, str)
        ),
        "",
    ).casefold()
    return any(content_type in accept for content_type in RAW_BROWSER_ACCEPT_TYPES)


def _fetch_raw_browser_response(page, url: str, *, timeout_seconds: float) -> tuple[str, str]:
    """Preserve a bounded JSON/JSONP navigation response instead of its executed DOM."""

    response = page.goto(
        url,
        wait_until="commit",
        timeout=max(1, int(timeout_seconds * 1000)),
    )
    if response is None:
        raise FetchError("browser navigation returned no response")

    status = getattr(response, "status", None)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise FetchError(
            f"browser response returned HTTP {status}",
            status=status if isinstance(status, int) else None,
        )

    response_headers = getattr(response, "headers", {}) or {}
    content_type = next(
        (
            str(value).split(";", 1)[0].strip().casefold()
            for key, value in response_headers.items()
            if str(key).casefold() == "content-type"
        ),
        "",
    )
    if content_type and not any(
        allowed in content_type for allowed in RAW_BROWSER_CONTENT_TYPES
    ):
        raise FetchError(f"browser response has unsupported content type {content_type}")

    body = response.body()
    if len(body) > MAX_RAW_BROWSER_RESPONSE_BYTES:
        raise FetchError(
            f"browser response exceeds {MAX_RAW_BROWSER_RESPONSE_BYTES} byte limit"
        )
    text = body.decode("utf-8", errors="replace")
    if not text.strip() or text.lstrip().startswith("<"):
        raise FetchError("browser response did not contain a structured payload")

    final_url = getattr(response, "url", None) or getattr(page, "url", None) or url
    return text, final_url


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
    network_idle_ms = (
        min(2000, max(1, remaining_ms // 4))
        if _has_job_context(url, "")
        else remaining_ms
    )
    try:
        page.wait_for_load_state("networkidle", timeout=network_idle_ms)
    except timeout_error_type:
        # Analytics, long polling, and streaming requests can keep a useful job
        # page permanently non-idle. Keep the reserved tail for real job DOM.
        pass
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
      return Array.from(document.querySelectorAll('a[href]')).some((link) => {
        let target;
        try {
          target = new URL(link.href, window.location.href);
        } catch (_error) {
          return false;
        }
        const host = target.hostname.toLowerCase();
        const path = target.pathname;
        if (/\\/profile\\/job_details\\/\\d+\\/?$/i.test(path)) return true;
        if (host === 'jobs.lever.co' && /^\\/[^/]+\\/[0-9a-z-]+\\/?$/i.test(path)) return true;
        if (host === 'jobs.ashbyhq.com' && /^\\/[^/]+\\/[^/]+\\/?$/i.test(path)) return true;
        if (host === 'apply.workable.com' && /^\\/[^/]+\\/j\\/[^/]+\\/?$/i.test(path)) return true;
        if (host === 'jobs.smartrecruiters.com' && /^\\/[^/]+\\/[^/]+\\/?$/i.test(path)) return true;
        if (target.href === window.location.href) return false;
        return /\\/(?:annonces|jobs?|offres|openings?|positions?|vacanc(?:y|ies))\\/(?!search(?:\\/|$)|results?(?:\\/|$))[^/]+/i
          .test(target.pathname);
      });
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
    if any(
        _is_usable_job_link(
            link.url,
            link.text,
            current_url,
            origin=link.origin,
        )
        for link in links
    ):
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


def _is_static_only_search_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except (TypeError, ValueError):
        return False
    return hostname in _STATIC_ONLY_SEARCH_HOSTS


def _source_with_artifacts(source: str, page: Page) -> str:
    source_parts = source.split("|")
    existing = set(source_parts)
    for artifact_name in sorted(page.artifacts or {}):
        marker = f"artifact:{artifact_name}"
        if marker not in existing:
            source_parts.append(marker)
    return "|".join(source_parts)
