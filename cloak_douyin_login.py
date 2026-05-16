"""Acquire a logged-in Douyin cookie via CloakBrowser QR login.

Why this exists: the bundled cookie_extractor.py writes a flat
``cookies:`` dict (corrupts the v4.0 ``cookies.douyin`` schema) and
uses plain Playwright. Cookie acquisition is the trust-sensitive step
where CloakBrowser's source-level stealth matters most, so we do it
here and persist via the project's own ConfigLoader.save_cookie.

Run it, scan the QR in the window that opens. It auto-detects login
by polling for the HttpOnly ``sessionid`` cookie, then writes back to
config.yml in the correct nested form.
"""

import asyncio
import time

import cloakbrowser

from core.config import ConfigLoader

CONFIG = "config.yml"
LOGIN_TIMEOUT = 300  # seconds to wait for the QR scan
KEY_FIELDS = ("sessionid", "sessionid_ss", "msToken", "passport_csrf_token",
              "sid_guard", "uid_tt", "ttwid")


async def main() -> None:
    # No user_agent override: CloakBrowser ships a self-consistent
    # UA / navigator.userAgentData / JA3. Forcing a UA string risks a
    # version mismatch against its native Chromium and is itself a tell
    # (over-evasion backfires).
    ctx = await cloakbrowser.launch_context_async(
        headless=False,
        stealth_args=True,
        viewport={"width": 1280, "height": 900},
    )
    page = await ctx.new_page()
    await page.goto(
        "https://www.douyin.com", wait_until="domcontentloaded", timeout=60000,
    )
    print("浏览器已打开抖音。请用手机 APP 扫码登录，脚本会自动检测...")

    deadline = time.time() + LOGIN_TIMEOUT
    cookie_str = ""
    while time.time() < deadline:
        cookies = await ctx.cookies()
        dy = [c for c in cookies if "douyin.com" in c.get("domain", "")]
        names = {c["name"] for c in dy}
        if "sessionid" in names or "sessionid_ss" in names:
            # Logged in. Let msToken / sid_guard settle, then re-read.
            await page.wait_for_timeout(3000)
            cookies = await ctx.cookies()
            dy = [c for c in cookies if "douyin.com" in c.get("domain", "")]
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in dy)
            break
        remaining = int(deadline - time.time())
        print(f"\r等待扫码登录... 还剩 {remaining}s ", end="", flush=True)
        await asyncio.sleep(3)

    await ctx.close()
    print()

    if not cookie_str:
        print("❌ 超时：未检测到 sessionid，登录未完成。未写入 config。")
        return

    have = [k for k in KEY_FIELDS if f"{k}=" in cookie_str]
    miss = [k for k in KEY_FIELDS if f"{k}=" not in cookie_str]
    print(f"✅ 登录成功，提取到 {len(cookie_str)} 字符")
    print(f"   关键字段有: {have}")
    if miss:
        print(f"   缺失(可能正常): {miss}")

    ConfigLoader(CONFIG).save_cookie(cookie_str, "douyin")
    print(f"✅ 已写回 {CONFIG} 的 cookies.douyin")
    print("   注意：save_cookie 用 yaml.dump 会重写文件、丢失注释（项目既有行为）")


if __name__ == "__main__":
    asyncio.run(main())
