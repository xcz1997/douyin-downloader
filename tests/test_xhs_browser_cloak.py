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


@pytest.mark.asyncio
async def test_close_before_start_is_noop():
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    await s.close()  # never started — must not raise


@pytest.mark.asyncio
async def test_close_is_idempotent(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    s = XHSBrowserSession("a=b", headless=True, interactive=False)
    await s.start()
    await s.close()
    await s.close()  # second close — must not raise
    assert rec["ctx"].closed is True


@pytest.mark.asyncio
async def test_whitespace_profile_dir_falls_back_to_ephemeral(monkeypatch):
    rec: dict = {}
    monkeypatch.setitem(sys.modules, "cloakbrowser", _fake_cloakbrowser(rec))
    s = XHSBrowserSession(
        "a=b", headless=True, interactive=False, profile_dir="   "
    )
    await s.start()
    assert rec["mode"] == "ephemeral"  # whitespace path must NOT be persistent
