"""Long-lived Playwright session for XHS data capture.

Owns one chromium browser + one context seeded with the user's XHS
cookie. Shared by XHSPlatformClient across all XHS calls in a pipeline
run; torn down in downloader.py's cleanup phase.
"""

from __future__ import annotations

import os
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

    def __init__(
        self, cookie_header: str, *,
        headless: bool | None = None,
        interactive: bool | None = None,
    ) -> None:
        # Default to headed: headless chromium exposes navigator.webdriver,
        # HeadlessChrome in UA, and a few other tells that XHS uses for bot
        # detection. Locally we have a display, so go headed by default.
        # CI/server (no display) sets XHS_HEADLESS=1 to opt back in.
        if headless is None:
            headless = os.environ.get("XHS_HEADLESS", "0") == "1"
        # In interactive mode (default whenever we have a display), start()
        # opens a page to XHS and blocks until the operator confirms the
        # window shows a logged-in state. This bypasses the "config cookie
        # was valid when extracted but the server now distrusts it" trap:
        # the operator can re-scan the QR in the same chromium context if
        # needed, then press Enter to proceed.
        if interactive is None:
            interactive = not headless
        self._cookie_header = cookie_header
        self._headless = headless
        self._interactive = interactive
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self) -> None:
        """Launch chromium and create a context with XHS cookies."""
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        # Anti-detection mirrors xhs_cookie_extractor.py — without these,
        # XHS sees navigator.webdriver=true and Chrome's automation
        # flag, voids the injected web_session server-side, and the
        # page renders as logged-out even with a valid cookie.
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{ get: () => undefined });"
        )
        await self._context.add_cookies(
            _cookie_header_to_playwright(self._cookie_header),
        )
        if self._interactive:
            await self._await_login_confirmation()

    async def _await_login_confirmation(self) -> None:
        """Open XHS in a page and block until the operator confirms login.

        The injected cookie may be valid, expired, or risk-controlled —
        we don't try to detect; we ask. If the page is already logged
        in, the operator presses Enter immediately. If not, they scan
        the QR in this same chromium context (so the resulting session
        is bound to the indistinguishable browser fingerprint we'll use
        for subsequent fetches) and then press Enter.
        """
        import asyncio

        page = await self._context.new_page()
        try:
            await page.goto(
                "https://www.xiaohongshu.com/explore",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            prompt = (
                "\n  ▶ 浏览器已打开 XHS。请确认窗口里是登录态"
                "（未登录就扫码登录），完成后回到这里按 Enter 继续... "
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, input, prompt)
        finally:
            await page.close()

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
