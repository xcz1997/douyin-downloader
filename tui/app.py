"""DownloaderApp: sidebar nav + content switcher + persistent log/status.

Wraps core/ — does not change core logic. Download/Subtitle/Login panels
land in later phases; here they are placeholders.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (Button, Header, ListItem, ListView, Label,
                             Static)

from tui.panels.download import DownloadPanel
from tui.panels.login import LoginPanel
from tui.panels.settings import SettingsPanel
from tui.panels.subtitle import SubtitlePanel
from tui.widgets import LogPane, StatusBar


class QuitConfirmScreen(ModalScreen):
    """Minimal confirmation modal shown when a download is in progress."""

    CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    #quit-dialog {
        width: 40;
        height: auto;
        border: solid $accent;
        padding: 1 2;
    }
    #quit-buttons {
        height: auto;
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-dialog"):
            yield Label("下载进行中，确认退出？")
            with Horizontal(id="quit-buttons"):
                yield Button("确认退出", id="quit-confirm", variant="error")
                yield Button("取消", id="quit-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit-confirm")

_SECTIONS = ["下载", "字幕", "登录", "设置"]

# VSCode-activity-bar style icons (Nerd Font, FontAwesome-classic
# codepoints — present in every Nerd Font patch). Shown above the
# label; falls back to tofu boxes only if no Nerd Font is installed.
_NAV_ICONS = [chr(0xF019), chr(0xF0F6), chr(0xF090), chr(0xF013)]

# Textual IDs must be ASCII-only — map section names to stable ASCII IDs.
_SECTION_ID: dict[str, str] = {
    "下载": "panel-download",
    "字幕": "panel-subtitle",
    "登录": "panel-login",
    "设置": "panel-settings",
}


class DownloaderApp(App):
    # opencode-inspired palette: dark neutral surface, single warm-orange
    # accent used sparingly (focus / primary / selected), everything else
    # muted. Source: https://opencode.ai/docs/themes/
    CSS = """
    Screen { background: #16161a; color: #d4d4d8; }

    Header {
        background: #16161a;
        color: #fab283;
        text-style: bold;
    }

    #sidebar {
        width: 14;
        background: #16161a;
        align: center middle;
    }
    #nav {
        width: 100%;
        height: auto;
        background: transparent;
        border: none;
        scrollbar-size: 0 0;
    }
    #nav > ListItem {
        height: 4;
        width: 100%;
        padding: 0;
        color: #7a7a85;
        content-align: center middle;
    }
    #nav > ListItem > Label {
        width: 100%;
        text-align: center;
    }
    #nav > ListItem.-highlight {
        color: #fab283;
        text-style: bold;
        border-left: thick #fab283;
        background: #1d1d22;
    }
    #nav:focus > ListItem.-highlight {
        color: #fab283;
        background: #1d1d22;
    }

    #content { width: 1fr; padding: 1 3; }
    #content > Static { height: 1fr; }
    #content VerticalScroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-color: #fab283;
        scrollbar-background: #16161a;
    }

    #main-row { height: 1fr; }

    .card {
        border: round #2e2e36;
        border-title-color: #6e6e78;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
        background: #1a1a1f;
    }

    .actions { height: auto; margin-top: 1; }
    .actions Button { margin-right: 2; }

    Button {
        min-width: 14;
        background: #26262e;
        color: #d4d4d8;
        border: none;
    }
    Button:hover { background: #30303a; }
    Button.-primary {
        background: #fab283;
        color: #16161a;
        text-style: bold;
    }
    Button.-primary:hover { background: #ffc79a; }
    Button.-error { background: #e06c75; color: #16161a; }

    Input {
        height: 3;
        margin-bottom: 1;
        background: #1f1f25;
        border: tall #2e2e36;
        color: #d4d4d8;
    }
    Input:focus { border: tall #fab283; }
    Checkbox { margin-bottom: 1; background: transparent; }
    Checkbox.-on > .toggle--button { color: #fab283; }
    RadioButton.-on > .toggle--button { color: #fab283; }

    .msg { color: #6e6e78; margin-top: 1; }

    #logpane {
        height: 10;
        border: round #2e2e36;
        border-title-color: #6e6e78;
        background: #16161a;
        color: #9a9aa5;
        padding: 0 1;
    }
    #statusbar {
        height: 1;
        background: #1a1a1f;
        color: #7a7a85;
        padding: 0 2;
    }
    """
    BINDINGS = [("q", "quit", "退出")]
    TITLE = "抖音下载器"

    def __init__(self, config_path: str = "config.yml") -> None:
        super().__init__()
        self._config_path = config_path
        self.current_section = "下载"

    def nav_labels(self) -> list[str]:
        return list(_SECTIONS)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Header(show_clock=False)
            with Horizontal(id="main-row"):
                with Vertical(id="sidebar"):
                    yield ListView(
                        *[ListItem(
                            Label(f"{_NAV_ICONS[i]}\n{s}"),
                            id=f"nav-{i}")
                          for i, s in enumerate(_SECTIONS)],
                        id="nav",
                    )
                with Vertical(id="content"):
                    yield SettingsPanel(self._config_path)
                    yield DownloadPanel(self._config_path)
                    yield SubtitlePanel()
                    yield LoginPanel()
            yield LogPane()
            yield StatusBar()

    def on_mount(self) -> None:
        self.show_section("下载")

    def show_section(self, name: str) -> None:
        self.current_section = name
        for section, panel_id in _SECTION_ID.items():
            try:
                w = self.query_one(f"#{panel_id}", Static)
                w.display = (section == name)
            except Exception:
                pass

    def settings_value(self, key: str) -> str:
        return self.query_one(SettingsPanel).value(key)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = int(event.item.id.split("-")[1])
        self.show_section(_SECTIONS[idx])

    def on_list_view_highlighted(
        self, event: ListView.Highlighted
    ) -> None:
        # Switch on arrow-key navigation too, not only Enter/click —
        # otherwise the highlight moves but content stays stale.
        if event.item is None or event.item.id is None:
            return
        idx = int(event.item.id.split("-")[1])
        self.show_section(_SECTIONS[idx])

    async def _close_session_and_exit(self) -> None:
        """Close the live xhs_session (if any), then exit. Pure: callers must
        cancel workers *before* invoking this so it never cancels itself."""
        try:
            panel = self.query_one(DownloadPanel)
            xhs_session = panel._xhs_session
            if xhs_session is not None:
                panel._xhs_session = None
                await xhs_session.close()
        except Exception:
            pass
        self.exit()

    def _on_quit_confirmed(self, confirmed: bool) -> None:
        """Callback from QuitConfirmScreen: proceed with exit if confirmed."""
        if confirmed:
            # Cancel the download worker (holding the session) BEFORE the
            # cleanup worker exists, so cancel_all() never self-cancels it.
            self.workers.cancel_all()
            self.run_worker(self._close_session_and_exit())

    async def action_quit(self) -> None:
        """Override: confirm if download is in progress, then clean up."""
        try:
            panel = self.query_one(DownloadPanel)
            has_active = panel._xhs_session is not None
        except Exception:
            has_active = False

        if has_active:
            self.push_screen(QuitConfirmScreen(), callback=self._on_quit_confirmed)
        else:
            # action handler is not a worker; cancel workers, then close+exit.
            self.workers.cancel_all()
            await self._close_session_and_exit()
