# xhs_cookie_extractor.py
"""Interactive XHS (小红书) cookie extractor.

Launches a Playwright-controlled Chromium window, navigates to
https://www.xiaohongshu.com, waits for the user to scan the QR code and
log in, then harvests the authenticated cookies and writes the result
back into ``config.yml`` under ``cookies.xhs``.

Usage:
    python xhs_cookie_extractor.py              # uses ./config.yml
    python xhs_cookie_extractor.py other.yml    # specify a config file

Why we require manual confirmation instead of auto-detecting login:
    Fields like ``a1`` / ``web_session`` / ``webId`` are issued by XHS
    to guest visitors too — ``web_session`` in particular is *not* a
    login-session marker despite the name. Auto-detecting login by
    waiting for these fields to appear would false-positive on guests.
    Rather than guess which combination of cookies proves login, we ask
    the user to press Enter after they've scanned the QR code. Simple
    and robust.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

try:
    from playwright.async_api import async_playwright
except ImportError:
    print(
        "ERROR: playwright not installed. Install with:\n"
        "    pip install playwright\n"
        "    playwright install chromium",
        file=sys.stderr,
    )
    sys.exit(1)


REQUIRED_FIELDS = {"a1", "web_session", "webId"}
OPTIONAL_FIELDS = {"gid", "xsecappid", "websectiga", "customer-sso-sid"}


def _cookies_to_header_string(cookies: list[dict]) -> str:
    """Turn a Playwright cookie list into a ``Cookie:`` header value."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _has_required(cookies: list[dict]) -> bool:
    names = {c["name"] for c in cookies}
    return REQUIRED_FIELDS.issubset(names)


async def _wait_for_enter() -> None:
    """Block until the user presses Enter, without blocking the loop."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input)


async def _harvest(config_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{ get: () => undefined });"
        )
        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com/explore")

        print("=" * 60)
        print("请在浏览器里扫码登录 XHS（手机 App → 右上角 → 扫一扫）")
        print()
        print("⚠️  重要：务必完成扫码并在浏览器里看到你的头像/主页后，")
        print("   再回到这里按【回车】，否则抓到的是游客 cookie，不能用于下载。")
        print("=" * 60)

        await _wait_for_enter()

        cookies = await context.cookies("https://www.xiaohongshu.com")
        await context.close()
        await browser.close()

    if not _has_required(cookies):
        missing = REQUIRED_FIELDS - {c["name"] for c in cookies}
        print(
            f"ERROR: cookie 缺少关键字段 {sorted(missing)}，"
            f"请确认已完成扫码登录后重试。",
            file=sys.stderr,
        )
        sys.exit(2)

    cookie_str = _cookies_to_header_string(cookies)
    _write_cookie_to_config(config_path, cookie_str)
    names = sorted({c["name"] for c in cookies})
    print(f"\n成功：已抓取 {len(cookies)} 个 cookie，写入 {config_path}")
    print(f"  关键字段: {', '.join(sorted(REQUIRED_FIELDS))}")
    extra = [n for n in names if n in OPTIONAL_FIELDS]
    if extra:
        print(f"  额外字段: {', '.join(extra)}")


def _write_cookie_to_config(config_path: Path, cookie_str: str) -> None:
    """Write *cookie_str* into ``cookies.xhs`` of *config_path*.

    Preserves existing ``cookies.douyin`` and migrates a legacy singular
    ``cookie:`` field into ``cookies.douyin`` as a side effect.
    """
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {}

    cookies_block = data.get("cookies")
    if not isinstance(cookies_block, dict):
        cookies_block = {}
        legacy = data.pop("cookie", None)
        if isinstance(legacy, str) and legacy.strip():
            cookies_block["douyin"] = legacy
        data["cookies"] = cookies_block

    cookies_block["xhs"] = cookie_str

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            data, fh, allow_unicode=True,
            default_flow_style=False, sort_keys=False,
        )


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yml")
    asyncio.run(_harvest(config_path))


if __name__ == "__main__":
    main()
