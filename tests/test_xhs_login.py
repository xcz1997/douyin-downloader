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

    monkeypatch.delitem(sys.modules, "xhs_login", raising=False)
    import importlib
    xhs_login = importlib.import_module("xhs_login")
    await xhs_login.main(profile_dir="/tmp/xhs_test_profile", timeout=5)
    assert calls["closed"] is True
    assert calls["user_data_dir"] == "/tmp/xhs_test_profile"


@pytest.mark.asyncio
async def test_timeout_when_no_web_session(monkeypatch, capsys):
    calls = {"closed": False}

    class FakeCtx:
        async def new_page(self):
            pg = types.SimpleNamespace()

            async def goto(*a, **k):
                return None

            pg.goto = goto
            return pg

        async def cookies(self):
            return []  # web_session never appears

        async def close(self):
            calls["closed"] = True

    async def launch_persistent_context_async(**kwargs):
        return FakeCtx()

    fake = types.SimpleNamespace(
        launch_persistent_context_async=launch_persistent_context_async
    )
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake)
    monkeypatch.setitem(sys.modules, "asyncio", __import__("asyncio"))

    monkeypatch.delitem(sys.modules, "xhs_login", raising=False)
    import importlib
    xhs_login = importlib.import_module("xhs_login")

    # timeout=0 → deadline already passed → loop body never finds web_session,
    # exits immediately with logged_in=False; must not raise.
    await xhs_login.main(profile_dir="/tmp/xhs_test_profile", timeout=0)
    assert calls["closed"] is True
    out = capsys.readouterr().out
    assert "超时" in out
