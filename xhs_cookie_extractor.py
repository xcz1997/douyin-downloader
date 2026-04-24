# xhs_cookie_extractor.py
"""Interactive XHS (小红书) cookie extractor.

Launches a Playwright-controlled Chromium window, navigates to
https://www.xiaohongshu.com, waits for the user to log in via QR-code,
harvests the authenticated cookies, and writes the result back into
``config.yml`` under ``cookies.xhs``.

Usage:
    python xhs_cookie_extractor.py              # uses ./config.yml
    python xhs_cookie_extractor.py other.yml    # specify a config file
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


# Cookie fields the XHS signer / API need.
REQUIRED_FIELDS = {"a1", "web_session", "webId"}

# Nice-to-have fields (logged but not required).
OPTIONAL_FIELDS = {"gid", "xsecappid", "websectiga", "customer-sso-sid"}


def _cookies_to_header_string(cookies: list[dict]) -> str:
    """Turn a Playwright cookie list into a ``Cookie:`` header value."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _has_required(cookies: list[dict]) -> bool:
    """Return True iff the harvest contains the fields the signer needs."""
    names = {c["name"] for c in cookies}
    return REQUIRED_FIELDS.issubset(names)


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
        # Hide webdriver flag so XHS doesn't block automated Chromium.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{ get: () => undefined });"
        )
        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com/explore")

        print("=" * 60)
        print("请在浏览器里扫码登录 XHS。")
        print("登录完成后脚本会自动提取 cookie 并写入 config.yml。")
        print("（最长等待 5 分钟；登录后可保持浏览器打开不动）")
        print("=" * 60)

        deadline = asyncio.get_event_loop().time() + 300
        harvested: list[dict] | None = None
        while asyncio.get_event_loop().time() < deadline:
            cookies = await context.cookies("https://www.xiaohongshu.com")
            if _has_required(cookies):
                harvested = cookies
                break
            await asyncio.sleep(3)

        await context.close()
        await browser.close()

    if harvested is None:
        print(
            "ERROR: 未在 5 分钟内检测到有效登录"
            "（缺少 a1 / web_session / webId）",
            file=sys.stderr,
        )
        sys.exit(2)

    cookie_str = _cookies_to_header_string(harvested)
    _write_cookie_to_config(config_path, cookie_str)
    names = sorted({c["name"] for c in harvested})
    print(f"\n成功：已抓取 {len(harvested)} 个 cookie，写入 {config_path}")
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
        # Migrate legacy `cookie:` → `cookies.douyin` so the new dict
        # form is self-consistent after we add the xhs key.
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
