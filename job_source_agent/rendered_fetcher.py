from __future__ import annotations

from .web import FetchError, Fetcher, Page, normalize_url


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
