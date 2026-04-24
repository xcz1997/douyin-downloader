# xhs_cookie_extractor.py
"""Interactive XHS (小红书) cookie extractor with in-terminal QR code.

Flow:
    1. Launch headless Chromium, open https://www.xiaohongshu.com, click
       the login button so the QR-code dialog is rendered.
    2. Screenshot the page, feed bytes to OpenCV's QRCodeDetector to
       decode the QR payload (a one-time login URL).
    3. Re-emit the decoded payload as a Unicode half-block QR in the
       terminal using the `qrcode` library.
    4. Poll for QR changes every 2 s — XHS rotates the QR roughly once
       a minute, so a scannable code is always on screen.
    5. User scans with the Xiaohongshu mobile app, waits for in-app
       confirmation, then presses Enter here to harvest the cookies
       and write them into ``config.yml::cookies.xhs``.

Requires:
    pip install playwright opencv-python qrcode pyyaml
    playwright install chromium

Env overrides:
    XHS_HEADLESS=0   launch a visible browser window (debug)
"""

from __future__ import annotations

import asyncio
import os
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

try:
    import cv2
    import numpy as np
except ImportError:
    print(
        "ERROR: opencv-python not installed. Install with:\n"
        "    pip install opencv-python",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import qrcode
except ImportError:
    print(
        "ERROR: qrcode not installed. Install with:\n"
        "    pip install qrcode",
        file=sys.stderr,
    )
    sys.exit(1)


REQUIRED_FIELDS = {"a1", "web_session", "webId"}
OPTIONAL_FIELDS = {"gid", "xsecappid", "websectiga", "customer-sso-sid"}
POLL_INTERVAL_SEC = 2.0
INITIAL_QR_TIMEOUT_SEC = 45.0


def _cookies_to_header_string(cookies: list[dict]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _has_required(cookies: list[dict]) -> bool:
    return REQUIRED_FIELDS.issubset({c["name"] for c in cookies})


async def _screenshot_decode_qr(page) -> str | None:
    """Full-page screenshot, decode largest visible QR code."""
    try:
        png = await page.screenshot(full_page=False)
    except Exception:
        return None
    arr = np.frombuffer(png, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()
    try:
        data, _bbox, _ = detector.detectAndDecode(img)
    except Exception:
        return None
    return data or None


def _render_qr(data: str) -> None:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    # Default invert=True for dark terminals (filled Unicode blocks
    # represent light QR modules, empty bg represents dark modules —
    # scanner reads dark-on-light correctly against dark terminal bg).
    # Set XHS_QR_INVERT=0 on a light terminal.
    invert = os.environ.get("XHS_QR_INVERT", "1") != "0"
    qr.print_ascii(tty=False, invert=invert)


async def _wait_for_enter() -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input)


async def _try_click_login(page) -> None:
    """Best-effort click on whatever login affordance XHS is showing."""
    for selector in [
        'text=登录',
        'button:has-text("登录")',
        'a:has-text("登录")',
        '.login-btn',
    ]:
        try:
            await page.locator(selector).first.click(timeout=3000)
            return
        except Exception:
            continue


async def _harvest(config_path: Path) -> None:
    headless = os.environ.get("XHS_HEADLESS", "1") != "0"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
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
        print("打开 XHS 登录页面中...", flush=True)
        await page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await _try_click_login(page)

        stop_event = asyncio.Event()
        last_qr_data: str | None = None
        header_shown = False
        first_qr_deadline = (
            asyncio.get_event_loop().time() + INITIAL_QR_TIMEOUT_SEC
        )

        async def qr_loop() -> None:
            nonlocal last_qr_data, header_shown
            while not stop_event.is_set():
                qr_data = await _screenshot_decode_qr(page)
                if qr_data and qr_data != last_qr_data:
                    if not header_shown:
                        print()
                        print("=" * 64)
                        print("请用小红书 App → 右上角 ➕ → 扫一扫 扫描下方二维码")
                        print("扫码并在 App 中确认登录后，回到这里按【回车】抓取 cookie")
                        print("（二维码过期会自动刷新，按 Ctrl+C 可退出）")
                        print("=" * 64)
                        header_shown = True
                    else:
                        print("\n二维码已刷新：")
                    _render_qr(qr_data)
                    last_qr_data = qr_data
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=POLL_INTERVAL_SEC,
                    )
                except asyncio.TimeoutError:
                    pass

        async def enter_wait() -> None:
            await _wait_for_enter()
            stop_event.set()

        async def initial_guard() -> None:
            """Abort if the first QR never appears within the timeout."""
            while not stop_event.is_set():
                if last_qr_data is not None:
                    return
                if asyncio.get_event_loop().time() >= first_qr_deadline:
                    print(
                        "\nERROR: 未能在页面检测到二维码。"
                        "可能 XHS 阻止了 headless 浏览器；"
                        "可尝试 XHS_HEADLESS=0 python xhs_cookie_extractor.py",
                        file=sys.stderr,
                    )
                    stop_event.set()
                    return
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

        await asyncio.gather(qr_loop(), enter_wait(), initial_guard())

        cookies = await context.cookies("https://www.xiaohongshu.com")
        await context.close()
        await browser.close()

    if last_qr_data is None:
        # Guard fired; error already printed.
        sys.exit(3)

    if not _has_required(cookies):
        missing = REQUIRED_FIELDS - {c["name"] for c in cookies}
        print(
            f"ERROR: cookie 缺少关键字段 {sorted(missing)}，"
            f"请确认已在 App 中完成扫码 + 确认登录后再按回车。",
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
    try:
        asyncio.run(_harvest(config_path))
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
