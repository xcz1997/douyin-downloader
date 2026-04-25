"""Long-lived Playwright session for XHS data capture.

Owns one chromium browser + one context seeded with the user's XHS
cookie. Shared by XHSPlatformClient across all XHS calls in a pipeline
run; torn down in downloader.py's cleanup phase.
"""

from __future__ import annotations

from contextlib import asynccontextmanager


def _cookie_header_to_playwright(raw: str) -> list[dict]:
    """Parse a raw Cookie header into Playwright's add_cookies shape."""
    out: list[dict] = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        out.append({
            "name": name.strip(),
            "value": value.strip().strip('"'),
            "domain": ".xiaohongshu.com",
            "path": "/",
        })
    return out


class XHSBrowserSession:
    """Shared Playwright browser + context for all XHS calls in one run.

    Usage:
        session = XHSBrowserSession(cookie_header)
        await session.start()
        try:
            async with session.page() as page:
                await page.goto(...)
        finally:
            await session.close()
    """

    def __init__(self, cookie_header: str, *, headless: bool = True) -> None:
        self._cookie_header = cookie_header
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self) -> None:
        """Launch chromium and create a context with XHS cookies."""
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        await self._context.add_cookies(
            _cookie_header_to_playwright(self._cookie_header),
        )

    async def close(self) -> None:
        """Release all Playwright resources. Idempotent."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    @asynccontextmanager
    async def page(self):
        """Yield a fresh page; auto-close on exit regardless of exception."""
        if self._context is None:
            raise RuntimeError(
                "XHSBrowserSession not started — call start() first",
            )
        pg = await self._context.new_page()
        try:
            yield pg
        finally:
            try:
                await pg.close()
            except Exception:
                pass
