# XHS CloakBrowser 迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 XHS 运行时数据会话 `core/platforms/xhs_browser.py` 从裸 Playwright 迁到 CloakBrowser（双模式：配置了 `xhs.profile_dir` 走持久 profile，否则临时 context + 注入 cookie），消除 JS-webdriver 补丁 / UA 覆盖 / new_context 等可被检测的弱招，对外接口不变。

**Architecture:** 原地重写 `XHSBrowserSession` 内部（`start()/page()/close()` 签名与 `_headless`/`_interactive` 解析逻辑不变，使消费方 `xhs.py`/`downloader.py` 与既有测试零改动），新增 `xhs_login.py` 持久登录脚本，配置加 `xhs.profile_dir`（镜像既有 subtitle 配置块写法）。缺 `cloakbrowser` 硬报错不回退。

**Tech Stack:** Python 3.13、pytest、cloakbrowser 0.3.28（本地已装，CHROMIUM_VERSION=146.0.7680.177.3，Playwright-BrowserContext 兼容 API：`launch_context_async` / `launch_persistent_context_async`，返回 ctx 有 `new_page/add_cookies/cookies/close`）、PyYAML。

参考 spec：`docs/superpowers/specs/2026-05-16-xhs-cloakbrowser-migration-design.md`

---

## File Structure

```
core/platforms/xhs_browser.py   # 改：内部全重写到 CloakBrowser，对外接口不变
core/models.py                  # 改：加 XHSConfig，AppConfig 加 xhs 字段
core/config.py                  # 改：_DEFAULTS + _build_config + generate_default 加 xhs 块
downloader.py                   # 改：构造 XHSBrowserSession 时传 profile_dir（单行）
xhs_login.py                    # 新：持久 profile 扫码登录脚本（仿 cloak_douyin_login.py）
tests/
  test_xhs_browser_cloak.py     # 新：mock cloakbrowser，测双模式/反检测参数/硬报错
  test_xhs_config.py            # 新：xhs.profile_dir 默认/解析/向后兼容
  test_xhs_browser_session.py   # 既有：仅测构造器 _headless/_interactive，须保持全绿
```

关键既有契约（不可破坏）：`xhs.py:494/620` 用 `async with session.page() as pg`；`downloader.py` 用 `XHSBrowserSession(...)` / `start()` / `close()`；`tests/test_xhs_browser_session.py` 断言构造器 `s._headless` / `s._interactive`（不涉旧 Playwright 内部，故构造器逻辑须原样保留）。

---

### Task 1: 重写 XHSBrowserSession 到 CloakBrowser

**Files:**
- Modify: `core/platforms/xhs_browser.py`（内部全重写，对外接口不变）
- Test: `tests/test_xhs_browser_cloak.py`（新建）

- [ ] **Step 1: Write the failing test**

Create `tests/test_xhs_browser_cloak.py`:

```python
import sys
import types

import pytest

from core.platforms.xhs_browser import (
    XHSBrowserSession,
    _cookie_header_to_playwright,
)


def _fake_cloakbrowser(record: dict):
    """A fake cloakbrowser module recording how it was called."""

    class FakeCtx:
        def __init__(self):
            self.added_cookies = None
            self.closed = False
            self.pages = []

        async def add_cookies(self, cookies):
            self.added_cookies = cookies

        async def new_page(self):
            pg = types.SimpleNamespace(closed=False)

            async def goto(*a, **k):
                return None

            async def close():
                pg.closed = True

            pg.goto = goto
            pg.close = close
            self.pages.append(pg)
            return pg

        async def close(self):
            self.closed = True

    async def launch_context_async(**kwargs):
        record["mode"] = "ephemeral"
        record["kwargs"] = kwargs
        record["ctx"] = FakeCtx()
        return record["ctx"]

    async def launch_persistent_context_async(**kwargs):
        record["mode"] = "persistent"
        record["kwargs"] = kwargs
        record["ctx"] = FakeCtx()
        return record["ctx"]

    return types.SimpleNamespace(
        launch_context_async=launch_context_async,
        launch_persistent_context_async=launch_persistent_context_async,
    )


def test_cookie_header_to_playwright_parses_and_strips_quotes():
    out = _cookie_header_to_playwright('a=b; web_session="xyz"; bad; c=d')
    names = {c["name"]: c["value"] for c in out}
    assert names == {"a": "b", "web_session": "xyz", "c": "d"}
    assert all(c["domain"] == ".xiaohongshu.com" and c["path"] == "/"
               for c in out)


@pytest.mark.asyncio
async def test_ephemeral_mode_injects_cookies_no_persistent(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    await s.start()
    assert rec["mode"] == "ephemeral"
    assert rec["ctx"].added_cookies == _cookie_header_to_playwright("a=b")
    assert "user_agent" not in rec["kwargs"]
    assert rec["kwargs"].get("humanize") is True
    await s.close()
    assert rec["ctx"].closed is True


@pytest.mark.asyncio
async def test_persistent_mode_uses_profile_no_cookie_inject(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    s = XHSBrowserSession(
        "a=b", headless=True, interactive=False, profile_dir="/tmp/xhsprof"
    )
    await s.start()
    assert rec["mode"] == "persistent"
    assert rec["kwargs"]["user_data_dir"] == "/tmp/xhsprof"
    assert rec["ctx"].added_cookies is None
    assert "user_agent" not in rec["kwargs"]
    assert rec["kwargs"].get("humanize") is True


@pytest.mark.asyncio
async def test_persistent_mode_skips_interactive(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    called = {"interactive": False}
    s = XHSBrowserSession(
        "a=b", headless=False, interactive=True, profile_dir="/tmp/xhsprof"
    )

    async def _spy():
        called["interactive"] = True

    monkeypatch.setattr(s, "_await_login_confirmation", _spy)
    await s.start()
    assert called["interactive"] is False  # persistent trusts profile


@pytest.mark.asyncio
async def test_ephemeral_headed_runs_interactive(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    called = {"interactive": False}
    s = XHSBrowserSession("a=b", headless=False, interactive=True)

    async def _spy():
        called["interactive"] = True

    monkeypatch.setattr(s, "_await_login_confirmation", _spy)
    await s.start()
    assert called["interactive"] is True


@pytest.mark.asyncio
async def test_missing_cloakbrowser_hard_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "cloakbrowser", None)  # import → ImportError
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    with pytest.raises(RuntimeError, match="cloakbrowser"):
        await s.start()


@pytest.mark.asyncio
async def test_page_yields_and_closes(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    await s.start()
    async with s.page() as pg:
        assert pg.closed is False
    assert pg.closed is True


@pytest.mark.asyncio
async def test_page_before_start_raises():
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    with pytest.raises(RuntimeError, match="not started"):
        async with s.page():
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_xhs_browser_cloak.py -v`
Expected: FAIL — `_cookie_header_to_playwright` still exists (passes) but the new mode/kwargs/hard-fail tests fail because current code uses Playwright (`async_playwright`, `chromium.launch`, `user_agent=`, `add_init_script`) and has no `profile_dir` param.

Note: if `pytest.mark.asyncio` errors as unknown marker, the project already runs async tests (see `tests/test_xhs_client_integration.py`); use the same async test mechanism that file uses (inspect it: `grep -n "asyncio\|async def test\|anyio\|@pytest" tests/test_xhs_client_integration.py`). Adapt the new test file's async decorator to match the project's existing convention before Step 3.

- [ ] **Step 3: Rewrite the implementation**

Replace the ENTIRE contents of `core/platforms/xhs_browser.py` with:

```python
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
        self._profile_dir = profile_dir or None
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_xhs_browser_cloak.py tests/test_xhs_browser_session.py -v`
Expected: ALL pass — new cloak tests green AND existing `test_xhs_browser_session.py` (constructor `_headless`/`_interactive` logic) still green (that logic was preserved verbatim).

- [ ] **Step 5: Commit**

```bash
git add core/platforms/xhs_browser.py tests/test_xhs_browser_cloak.py
git commit -m "feat(xhs): 数据会话迁到 CloakBrowser（双模式，弃 JS webdriver 补丁/UA 覆盖）"
```

---

### Task 2: 配置 —— XHSConfig + AppConfig.xhs + config.py

镜像既有 subtitle 配置块写法（参考 `core/config.py:14` 导入、`:35` `_DEFAULTS`、`:281` `generate_default`、`:402-404` `_build_config`；`core/models.py` 的 `SubtitleConfig` + `AppConfig.subtitle`）。

**Files:**
- Modify: `core/models.py`
- Modify: `core/config.py`
- Test: `tests/test_xhs_config.py`（新建）

- [ ] **Step 1: Write the failing test**

Create `tests/test_xhs_config.py`:

```python
from core.config import ConfigLoader


def _load(tmp_path, body: str):
    f = tmp_path / "c.yml"
    f.write_text("links: []\nsave_path: ./x\n" + body, encoding="utf-8")
    return ConfigLoader(str(f)).load()


def test_xhs_profile_dir_defaults_empty(tmp_path):
    cfg = _load(tmp_path, "")
    assert cfg.xhs.profile_dir == ""


def test_xhs_profile_dir_parsed(tmp_path):
    cfg = _load(tmp_path, "xhs:\n  profile_dir: /home/me/.xhsprof\n")
    assert cfg.xhs.profile_dir == "/home/me/.xhsprof"


def test_xhs_partial_block_ok(tmp_path):
    cfg = _load(tmp_path, "xhs: {}\n")
    assert cfg.xhs.profile_dir == ""
```

(If `ConfigLoader(str(path)).load()` is not the real loader API, inspect `tests/test_subtitle_config.py` and mirror exactly — that file already uses the real API.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_xhs_config.py -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'xhs'`.

- [ ] **Step 3: Implement**

In `core/models.py`, add (immediately after the `SubtitleConfig` dataclass, before `AppConfig`):

```python
@dataclass
class XHSConfig:
    profile_dir: str = ""
```

In `core/models.py` `AppConfig`, add after the `subtitle:` field:

```python
    xhs: XHSConfig = field(default_factory=XHSConfig)
```

In `core/config.py`, extend the models import (currently `from core.models import AppConfig, DownloadOptions, SubtitleConfig`) to also import `XHSConfig`:

```python
from core.models import AppConfig, DownloadOptions, SubtitleConfig, XHSConfig
```

In `core/config.py` `_DEFAULTS`, add after the `"subtitle": {...}` entry:

```python
    "xhs": {"profile_dir": ""},
```

In `core/config.py` `generate_default`'s template dict, add after the subtitle block:

```python
        "xhs": {"profile_dir": ""},
```

In `core/config.py` `_build_config`, immediately after the subtitle block construction (`subtitle = SubtitleConfig(...)`), add:

```python
        _xhs = data.get("xhs", {}) or {}
        xhs = XHSConfig(profile_dir=str(_xhs.get("profile_dir", "")))
```

Then add `xhs=xhs,` as a kwarg in the `AppConfig(...)` constructor call (same place `subtitle=subtitle,` is passed — inspect the surrounding lines and mirror exactly).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_xhs_config.py tests/test_subtitle_config.py tests/test_config.py -v`
Expected: new xhs tests pass; existing subtitle/config tests still green (no regression).

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/config.py tests/test_xhs_config.py
git commit -m "feat(xhs): config 加 xhs.profile_dir（默认空=注入模式，向后兼容）"
```

---

### Task 3: downloader.py 传 profile_dir

**Files:**
- Modify: `downloader.py`（构造 `XHSBrowserSession` 处，约 `:129`）
- Test: `tests/test_downloader_xhs_profile.py`（新建，轻量）

- [ ] **Step 1: Write the failing test**

Create `tests/test_downloader_xhs_profile.py`:

```python
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "downloader.py"


def test_xhs_session_constructed_with_profile_dir():
    """downloader.py must pass profile_dir from config into the session
    so persistent mode is reachable end-to-end."""
    src = SRC.read_text(encoding="utf-8")
    assert "XHSBrowserSession(" in src
    # the construction call forwards the configured profile dir
    assert "profile_dir=" in src
    assert "config.xhs.profile_dir" in src
    # sanity: file still parses
    ast.parse(src)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_downloader_xhs_profile.py -v`
Expected: FAIL — current `downloader.py` constructs `XHSBrowserSession(xhs_state.value)` with no `profile_dir`.

- [ ] **Step 3: Implement**

In `downloader.py`, find the line (around 129):

```python
            xhs_session = XHSBrowserSession(xhs_state.value)
```

Replace with:

```python
            xhs_session = XHSBrowserSession(
                xhs_state.value,
                profile_dir=config.xhs.profile_dir or None,
            )
```

(`config` is the `AppConfig` already in scope in `cmd_download`; `or None` maps the empty-string default to ephemeral mode.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_downloader_xhs_profile.py -v`
Expected: PASS.

Then sanity-compile: `python -m py_compile downloader.py` → no output (success).

- [ ] **Step 5: Commit**

```bash
git add downloader.py tests/test_downloader_xhs_profile.py
git commit -m "feat(xhs): downloader 透传 config.xhs.profile_dir 到会话"
```

---

### Task 4: xhs_login.py 持久 profile 登录脚本

仿 `cloak_douyin_login.py` 结构，但写持久 profile（不写 config）。

**Files:**
- Create: `xhs_login.py`
- Test: `tests/test_xhs_login.py`（新建）

- [ ] **Step 1: Write the failing test**

Create `tests/test_xhs_login.py`:

```python
import ast
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "xhs_login.py"


def test_xhs_login_compiles_and_no_ua_override():
    src = SRC.read_text(encoding="utf-8")
    ast.parse(src)  # syntactically valid
    # over-evasion lesson: never hardcode a user_agent string
    assert "user_agent" not in src
    assert "launch_persistent_context_async" in src


@pytest.mark.asyncio
async def test_detects_web_session_cookie(monkeypatch):
    """main() should stop polling once an xhs web_session cookie shows."""
    calls = {"closed": False}

    class FakeCtx:
        async def new_page(self):
            pg = types.SimpleNamespace()

            async def goto(*a, **k):
                return None

            pg.goto = goto
            pg.wait_for_timeout = lambda *_a, **_k: None
            return pg

        async def cookies(self):
            return [{"name": "web_session", "value": "ok",
                     "domain": ".xiaohongshu.com"}]

        async def close(self):
            calls["closed"] = True

    async def launch_persistent_context_async(**kwargs):
        calls["user_data_dir"] = kwargs.get("user_data_dir")
        return FakeCtx()

    fake = types.SimpleNamespace(
        launch_persistent_context_async=launch_persistent_context_async
    )
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake)

    import importlib
    xhs_login = importlib.import_module("xhs_login")
    importlib.reload(xhs_login)
    # run with a tiny timeout + explicit profile dir
    await xhs_login.main(profile_dir="/tmp/xhs_test_profile", timeout=5)
    assert calls["closed"] is True
    assert calls["user_data_dir"] == "/tmp/xhs_test_profile"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_xhs_login.py -v`
Expected: FAIL — `xhs_login.py` does not exist.

- [ ] **Step 3: Implement**

Create `xhs_login.py`:

```python
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

    # No user_agent override — CloakBrowser native UA is self-consistent.
    ctx = await cloakbrowser.launch_persistent_context_async(
        user_data_dir=profile_dir,
        headless=False,
        stealth_args=True,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_xhs_login.py -v`
Expected: PASS (2 passed). Then `python -m py_compile xhs_login.py` → success.

- [ ] **Step 5: Commit**

```bash
git add xhs_login.py tests/test_xhs_login.py
git commit -m "feat(xhs): xhs_login.py 持久 profile 扫码登录脚本"
```

---

### Task 5: README 文档 + 全量回归

**Files:**
- Modify: `README.md`（XHS 反检测/持久 profile 用法说明）
- 全量回归验证

- [ ] **Step 1: README 补充**

Read `README.md`; find the configuration section (Chinese, `###` headings, near the subtitle section added earlier). Add a subsection matching that style:

````markdown
### XHS 反检测（CloakBrowser）

XHS 数据抓取走 CloakBrowser（源码级 C++ 反检测，自洽原生指纹）。两种模式：

- **注入模式（默认）**：`xhs.profile_dir` 留空，用 `cookies.xhs` 的 Cookie；首次/过期会开窗口让你确认或扫码。
- **持久 profile 模式（更强）**：

```yaml
xhs:
  profile_dir: /你的/路径/.xhs_profile
```

先跑一次 `python xhs_login.py` 扫码登录（profile 持久化、获取指纹=使用指纹），之后下载自动复用，无需每次确认、可 headless。

依赖：`pip install cloakbrowser`（缺它 XHS 会明确报错并跳过，不静默降级；抖音不受影响）。
````

- [ ] **Step 2: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿（既有 + 新增 xhs 测试；既有 `test_xhs_browser_session.py` / subtitle / config 无回归）。报告通过数。

Run: `python -m py_compile core/platforms/xhs_browser.py downloader.py xhs_login.py core/config.py core/models.py`
Expected: 全部成功无输出。

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(xhs): README 补充 CloakBrowser 反检测与持久 profile 用法"
```

---

## Self-Review 结论

- **Spec 覆盖**：双模式重写 → Task 1；持久=信任/注入=确认门控 → Task 1（test_persistent_mode_skips_interactive / test_ephemeral_headed_runs_interactive）；缺依赖硬报错不降级 → Task 1（test_missing_cloakbrowser_hard_fails）；不覆盖 UA / humanize → Task 1（kwargs 断言）；删 JS webdriver 补丁/new_context/--disable-blink → Task 1（整文件重写，新实现无这些）；配置 xhs.profile_dir + 向后兼容 → Task 2；downloader 透传 → Task 3；xhs_login.py 持久登录 → Task 4；README → Task 5；接口不变（page/start/close + 既有 test_xhs_browser_session.py 全绿）→ Task 1 Step 4 显式验证。spec §8 未决项（add_cookies/persistent 首次行为/旧测试断言）：add_cookies 由 Task 1 mock 契约验证，真实行为实现期 Step 4 跑真包前若需可加确认；旧 test_xhs_browser_session.py 已确认只测构造器（本计划已核对，不涉旧 Playwright 内部，构造器逻辑原样保留）。
- **占位符**：无。每步含完整代码/命令/预期。Task 1 Step 2 关于 `pytest.mark.asyncio` 的说明是让实现者对齐项目既有 async 测试约定（`tests/test_xhs_client_integration.py`），非占位。
- **类型一致**：`XHSBrowserSession(cookie_header, *, headless, interactive, profile_dir)`、`_cookie_header_to_playwright`、`start/page/close`、`XHSConfig.profile_dir`、`config.xhs.profile_dir`、`cloakbrowser.launch_context_async` / `launch_persistent_context_async(user_data_dir=)`、`xhs_login.main(profile_dir, timeout)` 全计划一致。
- **已知实现期确认**：CloakBrowser ctx 真实 `add_cookies` 支持 / persistent 首次空 profile 行为 / 项目 async 测试装饰器约定——均在对应 Task 内注明先以真实调用或既有文件核对。
