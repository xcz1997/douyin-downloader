import sys
import types

import pytest

from tui.panels.login import LoginPanel, StdoutToLog


def test_stdout_to_log_forwards_writes():
    lines = []
    s = StdoutToLog(lines.append)
    s.write("hello\n")
    s.write("world")
    s.flush()
    assert "hello" in "".join(lines)


@pytest.mark.asyncio
async def test_douyin_login_invokes_main(monkeypatch):
    called = {}

    async def fake_main(*a, **k):
        called["douyin"] = True

    fake_mod = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "cloak_douyin_login", fake_mod)

    panel = LoginPanel()
    await panel.run_douyin_login()
    assert called["douyin"] is True


@pytest.mark.asyncio
async def test_xhs_login_invokes_main(monkeypatch):
    called = {}

    async def fake_main(*a, **k):
        called["xhs"] = True

    fake_mod = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "xhs_login", fake_mod)

    panel = LoginPanel()
    await panel.run_xhs_login()
    assert called["xhs"] is True


@pytest.mark.asyncio
async def test_login_stdout_reaches_logpane(monkeypatch, tmp_path):
    # Regression guard for the call_from_thread-on-async-worker bug:
    # a login main's stdout must reach the LogPane (no RuntimeError
    # silently dropping it).
    from tui.app import DownloaderApp
    from tui.widgets import LogPane

    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")

    async def fake_main(*a, **k):
        print("扫码提示一行")

    monkeypatch.setitem(sys.modules, "cloak_douyin_login",
                        types.SimpleNamespace(main=fake_main))

    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("登录")
        await pilot.pause()
        panel = app.query_one(LoginPanel)
        await panel.run_douyin_login()
        await pilot.pause()
        lines = app.query_one(LogPane).lines
        assert any("扫码提示一行" in str(l) for l in lines)
