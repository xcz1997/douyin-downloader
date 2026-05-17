"""Settings panel: load key config.yml fields, edit, save back."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Static

import yaml

from core.config import ConfigLoader
from tui.config_io import save_config_fields

_FIELDS: list[tuple[str, str, type]] = [
    ("save_path", "保存目录", str),
    ("thread", "并发数", int),
    ("retry_times", "重试次数", int),
]


class SettingsPanel(Static):
    def __init__(self, config_path: str) -> None:
        super().__init__(id="panel-settings")
        self._config_path = config_path
        self._values: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        # Raw YAML gives the user-facing string form (e.g. "./JIN/" not
        # PosixPath('JIN')); ConfigLoader provides defaults for keys the
        # file omits. Either source failing must NOT crash the TUI
        # (spec: any core/config exception is contained) — fall back to
        # built-in defaults so the app always launches.
        try:
            raw = yaml.safe_load(
                Path(self._config_path).read_text(encoding="utf-8")
            ) or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            cfg = ConfigLoader(self._config_path).load()
            fb = {
                "save_path": str(cfg.save_path),
                "thread": str(cfg.thread),
                "retry_times": str(cfg.retry_times),
            }
        except Exception:
            fb = {"save_path": "./downloads", "thread": "5",
                  "retry_times": "3"}
        self._values = {
            "save_path": str(raw.get("save_path", fb["save_path"])),
            "thread": str(raw.get("thread",
                                  raw.get("concurrency", fb["thread"]))),
            "retry_times": str(raw.get("retry_times",
                                       raw.get("retry", fb["retry_times"]))),
        }

    def value(self, key: str) -> str:
        inp = self.query_one(f"#set-{key}", Input)
        return inp.value

    def compose(self) -> ComposeResult:
        with Vertical():
            for key, label, _ in _FIELDS:
                yield Label(label)
                yield Input(value=self._values.get(key, ""),
                            id=f"set-{key}")
            yield Button("保存", id="settings-save", variant="primary")
            yield Label("", id="settings-msg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "settings-save":
            return
        msg = self.query_one("#settings-msg", Label)
        try:
            updates: dict = {}
            for key, _, caster in _FIELDS:
                updates[key] = caster(self.value(key))
            save_config_fields(self._config_path, updates)
            msg.update("已保存（注意：yaml 回写会丢注释，项目既有行为）")
        except Exception as exc:
            msg.update(f"保存失败: {exc}")
