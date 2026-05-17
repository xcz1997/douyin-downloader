"""Login panel: runs the standalone cloak/xhs login coroutines in a
worker, piping their stdout into the persistent LogPane, then refreshes
the StatusBar cookie/profile indicators. TUI never blocks on input().
"""

from __future__ import annotations

import contextlib
import importlib
from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Static


class StdoutToLog:
    """Minimal file-like that forwards complete lines to a sink callable."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._sink(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._sink(self._buf)
            self._buf = ""


class LoginPanel(Static):
    def __init__(self) -> None:
        super().__init__(id="panel-login")

    def compose(self) -> ComposeResult:
        with Vertical():
            with Vertical(classes="card") as g:
                g.border_title = "登录方式"
                yield Button("抖音扫码登录 (CloakBrowser)",
                             id="login-douyin", variant="primary")
                yield Button("XHS 持久 profile 登录",
                             id="login-xhs", variant="primary")
            yield Label("", id="login-msg", classes="msg")

    def _log(self, line: str) -> None:
        from tui.widgets import LogPane
        # run_*_login are async workers (event-loop thread); call the
        # widget directly. call_from_thread RAISES from this thread
        # (same bug fixed in the download panel).
        try:
            self.app.query_one(LogPane).write(line)
        except Exception:
            pass

    async def _run_login(self, module_name: str) -> None:
        try:
            msg = self.query_one("#login-msg", Label)
        except Exception:
            msg = None
        if msg is not None:
            msg.update("登录中…（按提示扫码）")
        mod = importlib.import_module(module_name)
        with contextlib.redirect_stdout(StdoutToLog(self._log)):
            try:
                await mod.main()
            except SystemExit:
                pass
            except Exception as exc:
                self._log(f"[red]登录失败: {exc}[/red]")
        if msg is not None:
            msg.update("完成（查看日志与上方指示灯）")
        self._refresh_indicators()

    async def run_douyin_login(self) -> None:
        await self._run_login("cloak_douyin_login")

    async def run_xhs_login(self) -> None:
        await self._run_login("xhs_login")

    def _refresh_indicators(self) -> None:
        from tui.widgets import StatusBar
        try:
            bar = self.app.query_one(StatusBar)
            from core.config import ConfigLoader
            cfg = ConfigLoader(
                getattr(self.app, "_config_path", "config.yml")
            ).load()
            bar.set_cookie(bool(cfg.cookies.get("douyin")))
            bar.set_profile(bool(cfg.xhs.profile_dir))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-douyin":
            self.run_worker(self.run_douyin_login(), exclusive=True)
        elif event.button.id == "login-xhs":
            self.run_worker(self.run_xhs_login(), exclusive=True)
