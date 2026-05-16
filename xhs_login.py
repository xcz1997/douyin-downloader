"""Populate a persistent XHS CloakBrowser profile via QR login.

Why this exists: persistent mode (config xhs.profile_dir) needs a real
browser profile that already holds a logged-in XHS session, acquired
with the SAME CloakBrowser fingerprint that data capture later reuses
(acquire == use fingerprint). The profile directory itself is the
persistence — nothing is written to config.yml.

Run it, scan the QR in the window. It polls for the XHS web_session
cookie, then the profile is saved automatically by CloakBrowser.

Usage:
    python xhs_login.py [profile_dir]
(profile_dir defaults to config.yml's xhs.profile_dir; must be non-empty)
"""

import argparse
import asyncio
import sys
import time

import cloakbrowser

from core.config import ConfigLoader

CONFIG = "config.yml"
LOGIN_TIMEOUT = 300  # seconds to wait for the QR scan


async def main(profile_dir: str | None = None, timeout: int = LOGIN_TIMEOUT) -> None:
    if not profile_dir:
        cfg = ConfigLoader(CONFIG).load()
        profile_dir = cfg.xhs.profile_dir
    if not profile_dir:
        print("❌ 未配置 xhs.profile_dir（config.yml），也未传参。", file=sys.stderr)
        sys.exit(1)

    # No UA override — CloakBrowser native fingerprint is self-consistent.
    # Match xhs_browser.py persistent-mode launch kwargs so the profile is
    # acquired with the same CloakBrowser behavior it's later reused with
    # (acquire == use). stealth_args defaults True in cloakbrowser; humanize
    # must be opted in, same as the runtime session.
    ctx = await cloakbrowser.launch_persistent_context_async(
        user_data_dir=profile_dir,
        headless=False,
        humanize=True,
    )
    page = await ctx.new_page()
    await page.goto(
        "https://www.xiaohongshu.com",
        wait_until="domcontentloaded", timeout=60000,
    )
    print(f"浏览器已打开小红书。请扫码登录，profile 将持久化到: {profile_dir}")

    deadline = time.time() + timeout
    logged_in = False
    while time.time() < deadline:
        cookies = await ctx.cookies()
        names = {
            c["name"] for c in cookies
            if "xiaohongshu.com" in c.get("domain", "")
        }
        if "web_session" in names:
            logged_in = True
            break
        remaining = int(deadline - time.time())
        print(f"\r等待扫码登录... 还剩 {remaining}s ", end="", flush=True)
        await asyncio.sleep(3)

    await ctx.close()
    print()
    if logged_in:
        print(f"✅ 登录成功，profile 已持久化到 {profile_dir}")
        print("   之后数据抓取（xhs.profile_dir 指向此目录）会复用它。")
    else:
        print("❌ 超时：未检测到 web_session，登录未完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XHS 持久 profile 扫码登录")
    parser.add_argument(
        "profile_dir", nargs="?", default=None,
        help="profile 目录（默认读 config.yml 的 xhs.profile_dir）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.profile_dir))
