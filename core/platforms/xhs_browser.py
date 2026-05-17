"""Long-lived CloakBrowser session for XHS data capture.

Two modes:
- persistent (xhs.profile_dir set): launch_persistent_context_async on a
  real profile pre-populated by xhs_login.py. Trusted, no cookie inject,
  no interactive block (headless-capable).
- ephemeral (no profile_dir): launch_context_async + add_cookies from the
  config Cookie header; keeps the operator login-confirm prompt when headed.

CloakBrowser ships C++-level stealth (navigator.webdriver, canvas/WebGL,
TLS/JA3, CDP) and a self-consistent native UA — so we never inject a JS
webdriver patch, never override user_agent, never pass
--disable-blink-features. Missing cloakbrowser hard-fails (no silent
downgrade to a detectable Playwright stack).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager


def _cookie_header_to_playwright(raw: str) -> list[dict]:
    """Parse a raw Cookie header into add_cookies shape."""
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
    """Shared CloakBrowser context for all XHS calls in one run.

    Usage:
        session = XHSBrowserSession(cookie_header, profile_dir=...)
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
        profile_dir: str | None = None,
    ) -> None:
        # Headed by default locally (a display exists); CI/server sets
        # XHS_HEADLESS=1. CloakBrowser is headless-safe, but headed keeps
        # parity with the operator login-confirm flow in ephemeral mode.
        if headless is None:
            headless = os.environ.get("XHS_HEADLESS", "0") == "1"
        # Interactive confirm only matters in ephemeral mode (injected
        # cookie may be stale). Persistent mode trusts the profile.
        if interactive is None:
            interactive = not headless
        self._cookie_header = cookie_header
        self._headless = headless
        self._interactive = interactive
        self._profile_dir = (profile_dir or "").strip() or None
        self._context = None

    async def start(self) -> None:
        """Launch a CloakBrowser context (persistent or ephemeral)."""
        try:
            import cloakbrowser
        except ImportError as exc:
            raise RuntimeError(
                "XHS 需要 CloakBrowser：pip install cloakbrowser。"
                "（不回退 Playwright——避免静默使用可被检测的弱栈）"
            ) from exc

        # No user_agent override (CloakBrowser native UA is self-consistent
        # with its navigator.userAgentData / JA3). humanize=True adds
        # human-like input curves/timing.
        launch_kwargs = dict(headless=self._headless, humanize=True)

        if self._profile_dir:
            self._context = await cloakbrowser.launch_persistent_context_async(
                user_data_dir=self._profile_dir, **launch_kwargs
            )
            # persistent: trust the profile — no cookie inject, no block
        else:
            self._context = await cloakbrowser.launch_context_async(
                **launch_kwargs
            )
            await self._context.add_cookies(
                _cookie_header_to_playwright(self._cookie_header)
            )
            if self._interactive:
                await self._await_login_confirmation()

    async def _await_login_confirmation(self) -> None:
        """Open XHS and block until the operator confirms login.

        The injected cookie may be valid, expired, or risk-controlled —
        we don't detect; we ask. Operator can re-scan the QR in this same
        context, then press Enter.
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
        """Release the CloakBrowser context. Idempotent."""
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

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
