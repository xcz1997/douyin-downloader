"""DownloaderApp: sidebar nav + content switcher + persistent log/status.

Wraps core/ — does not change core logic. Download/Subtitle/Login panels
land in later phases; here they are placeholders.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, Label, Static

from tui.panels.settings import SettingsPanel
from tui.widgets import LogPane, StatusBar

_SECTIONS = ["下载", "字幕", "登录", "设置"]

# Textual IDs must be ASCII-only — map section names to stable ASCII IDs.
_SECTION_ID: dict[str, str] = {
    "下载": "panel-download",
    "字幕": "panel-subtitle",
    "登录": "panel-login",
    "设置": "panel-settings",
}


class DownloaderApp(App):
    CSS = """
    #sidebar { width: 16; border-right: solid $accent; }
    #content { width: 1fr; }
    #logpane { height: 10; border-top: solid $accent; }
    #statusbar { height: 1; background: $panel; }
    """
    BINDINGS = [("q", "quit", "退出")]

    def __init__(self, config_path: str = "config.yml") -> None:
        super().__init__()
        self._config_path = config_path
        self.current_section = "下载"

    def nav_labels(self) -> list[str]:
        return list(_SECTIONS)

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal():
                lv = ListView(
                    *[ListItem(Label(s), id=f"nav-{i}")
                      for i, s in enumerate(_SECTIONS)],
                    id="sidebar",
                )
                yield lv
                with Vertical(id="content"):
                    yield SettingsPanel(self._config_path)
                    yield Static("下载（Phase 2）", id="panel-download")
                    yield Static("字幕（Phase 3）", id="panel-subtitle")
                    yield Static("登录（Phase 4）", id="panel-login")
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
