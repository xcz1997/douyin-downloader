"""Settings panel: load key config.yml fields, edit, save back."""

from __future__ import annotations

from typing import Any

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static

import yaml

from core.config import ConfigLoader
from tui.config_io import save_config_fields

_FIELDS: list[tuple[str, str, type]] = [
    ("save_path", "保存目录", str),
    ("thread", "并发数", int),
    ("retry_times", "重试次数", int),
]

_DEFAULT_SOURCES = ["track", "ocr", "asr"]


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base*, returning a new dict.

    Both sides are dict-copied at each level; *base* is never mutated.
    Lists are replaced wholesale (not merged) — sufficient for
    subtitle/xhs sub-blocks.
    """
    result = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class SettingsPanel(Static):
    def __init__(self, config_path: str) -> None:
        super().__init__(id="panel-settings")
        self._config_path = config_path
        self._values: dict[str, Any] = {}
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
                "subtitle_enabled": cfg.subtitle.enabled,
                "subtitle_sources": cfg.subtitle.sources,
                "subtitle_asr_model": cfg.subtitle.asr_model,
                "subtitle_ocr_interval": cfg.subtitle.ocr_interval,
                "subtitle_ocr_similarity": cfg.subtitle.ocr_similarity,
                "xhs_profile_dir": cfg.xhs.profile_dir,
                "transcribe_enabled": cfg.transcribe.enabled,
                "transcribe_auto": cfg.transcribe.auto_after_download,
                "transcribe_base_url": cfg.transcribe.base_url,
                "transcribe_model": cfg.transcribe.model,
                "transcribe_api_key": cfg.transcribe.api_key,
                "transcribe_max_images": cfg.transcribe.max_images,
                "transcribe_overwrite": cfg.transcribe.overwrite,
            }
        except Exception:
            fb = {
                "save_path": "./downloads",
                "thread": "5",
                "retry_times": "3",
                "subtitle_enabled": False,
                "subtitle_sources": list(_DEFAULT_SOURCES),
                "subtitle_asr_model": "0.6b",
                "subtitle_ocr_interval": 0.5,
                "subtitle_ocr_similarity": 0.7,
                "xhs_profile_dir": "",
                "transcribe_enabled": False,
                "transcribe_auto": False,
                "transcribe_base_url":
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "transcribe_model": "qwen-vl-max",
                "transcribe_api_key": "",
                "transcribe_max_images": 0,
                "transcribe_overwrite": False,
            }

        _sub = raw.get("subtitle", {}) or {}
        _sub_asr = _sub.get("asr", {}) or {}
        _sub_ocr = _sub.get("ocr", {}) or {}
        _xhs = raw.get("xhs", {}) or {}
        _tr = raw.get("transcribe", {}) or {}

        self._values = {
            "save_path": str(raw.get("save_path", fb["save_path"])),
            "thread": str(raw.get("thread",
                                  raw.get("concurrency", fb["thread"]))),
            "retry_times": str(raw.get("retry_times",
                                       raw.get("retry", fb["retry_times"]))),
            # subtitle nested fields
            "subtitle_enabled": bool(
                _sub.get("enabled", fb["subtitle_enabled"])
            ),
            "subtitle_sources": ",".join(
                _sub.get("sources", fb["subtitle_sources"])
            ),
            "subtitle_asr_model": str(
                _sub_asr.get("model", fb["subtitle_asr_model"])
            ),
            "subtitle_ocr_interval": str(
                _sub_ocr.get("interval", fb["subtitle_ocr_interval"])
            ),
            "subtitle_ocr_similarity": str(
                _sub_ocr.get("similarity", fb["subtitle_ocr_similarity"])
            ),
            # xhs nested fields
            "xhs_profile_dir": str(
                _xhs.get("profile_dir", fb["xhs_profile_dir"]) or ""
            ),
            # transcribe nested fields
            "transcribe_enabled": bool(
                _tr.get("enabled", fb["transcribe_enabled"])
            ),
            "transcribe_auto": bool(
                _tr.get("auto_after_download", fb["transcribe_auto"])
            ),
            "transcribe_base_url": str(
                _tr.get("base_url", fb["transcribe_base_url"])
            ),
            "transcribe_model": str(
                _tr.get("model", fb["transcribe_model"])
            ),
            "transcribe_api_key": str(
                _tr.get("api_key", fb["transcribe_api_key"]) or ""
            ),
            "transcribe_max_images": str(
                _tr.get("max_images", fb["transcribe_max_images"])
            ),
            "transcribe_overwrite": bool(
                _tr.get("overwrite", fb["transcribe_overwrite"])
            ),
        }

    def value(self, key: str) -> str:
        inp = self.query_one(f"#set-{key}", Input)
        return inp.value

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(classes="card") as g_dl:
                g_dl.border_title = "下载设置"
                for key, label, _ in _FIELDS:
                    yield Label(label)
                    yield Input(value=self._values.get(key, ""),
                                id=f"set-{key}")
            with Vertical(classes="card") as g_sub:
                g_sub.border_title = "字幕设置"
                # subtitle.enabled (bool → Checkbox)
                yield Checkbox(
                    "启用字幕",
                    value=self._values.get("subtitle_enabled", False),
                    id="set-subtitle-enabled",
                )
                # subtitle.sources (list → comma string)
                yield Label("字幕来源（逗号分隔，如 track,ocr,asr）")
                yield Input(
                    value=self._values.get("subtitle_sources", ""),
                    id="set-subtitle-sources",
                )
                # subtitle.asr.model
                yield Label("ASR 模型（如 0.6b / 1.7b）")
                yield Input(
                    value=self._values.get("subtitle_asr_model", ""),
                    id="set-subtitle-asr-model",
                )
                # subtitle.ocr.interval
                yield Label("OCR 截帧间隔（秒，如 0.5）")
                yield Input(
                    value=self._values.get("subtitle_ocr_interval", ""),
                    id="set-subtitle-ocr-interval",
                )
                # subtitle.ocr.similarity
                yield Label("OCR 相似度阈值（0-1，如 0.7）")
                yield Input(
                    value=self._values.get("subtitle_ocr_similarity", ""),
                    id="set-subtitle-ocr-similarity",
                )
            with Vertical(classes="card") as g_xhs:
                g_xhs.border_title = "小红书"
                # xhs.profile_dir
                yield Label("XHS 浏览器 profile 目录")
                yield Input(
                    value=self._values.get("xhs_profile_dir", ""),
                    id="set-xhs-profile-dir",
                )
            with Vertical(classes="card") as g_tr:
                g_tr.border_title = "图片转录"
                yield Checkbox(
                    "启用图片转录",
                    value=self._values.get("transcribe_enabled", False),
                    id="set-transcribe-enabled",
                )
                yield Checkbox(
                    "下载图文笔记后自动转录",
                    value=self._values.get("transcribe_auto", False),
                    id="set-transcribe-auto",
                )
                yield Label("API 地址（OpenAI 兼容 endpoint）")
                yield Input(
                    value=self._values.get("transcribe_base_url", ""),
                    id="set-transcribe-base-url",
                )
                yield Label("模型名")
                yield Input(
                    value=self._values.get("transcribe_model", ""),
                    id="set-transcribe-model",
                )
                yield Label("API Key（直接填则存入配置；留空走环境变量）")
                yield Input(
                    value=self._values.get("transcribe_api_key", ""),
                    password=True,
                    id="set-transcribe-api-key",
                )
                yield Label("单笔记最多转录张数（0=不限）")
                yield Input(
                    value=self._values.get("transcribe_max_images", ""),
                    id="set-transcribe-max-images",
                )
                yield Checkbox(
                    "覆盖重跑（默认已存在则跳过）",
                    value=self._values.get("transcribe_overwrite", False),
                    id="set-transcribe-overwrite",
                )
            with Horizontal(classes="actions"):
                yield Button("保存", id="settings-save",
                             variant="primary")
            yield Label("", id="settings-msg", classes="msg")

    def _build_subtitle_overlay(self) -> dict:
        """Build the subtitle sub-dict from form values (for deep-merge)."""
        sources_raw = self.query_one("#set-subtitle-sources", Input).value.strip()
        if sources_raw:
            sources = [s.strip() for s in sources_raw.split(",") if s.strip()]
            if not sources:
                sources = list(_DEFAULT_SOURCES)
        else:
            sources = list(_DEFAULT_SOURCES)

        return {
            "enabled": self.query_one("#set-subtitle-enabled", Checkbox).value,
            "sources": sources,
            "asr": {
                "model": self.query_one("#set-subtitle-asr-model", Input).value,
            },
            "ocr": {
                "interval": float(
                    self.query_one("#set-subtitle-ocr-interval", Input).value
                ),
                "similarity": float(
                    self.query_one("#set-subtitle-ocr-similarity", Input).value
                ),
            },
        }

    def _build_transcribe_overlay(self) -> dict:
        """Build the transcribe sub-dict from form values (for deep-merge)."""
        return {
            "enabled":
                self.query_one("#set-transcribe-enabled", Checkbox).value,
            "auto_after_download":
                self.query_one("#set-transcribe-auto", Checkbox).value,
            "base_url":
                self.query_one("#set-transcribe-base-url", Input).value,
            "model": self.query_one("#set-transcribe-model", Input).value,
            "api_key":
                self.query_one("#set-transcribe-api-key", Input).value,
            "max_images": int(
                self.query_one("#set-transcribe-max-images", Input).value
            ),
            "overwrite":
                self.query_one("#set-transcribe-overwrite", Checkbox).value,
        }

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "settings-save":
            return
        msg = self.query_one("#settings-msg", Label)
        try:
            updates: dict = {}
            for key, _, caster in _FIELDS:
                updates[key] = caster(self.value(key))

            # Read existing raw sub-blocks to deep-merge (preserve siblings)
            try:
                raw = yaml.safe_load(
                    Path(self._config_path).read_text(encoding="utf-8")
                ) or {}
            except Exception:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}

            # subtitle sub-block: deep-merge existing + form values
            existing_sub = raw.get("subtitle", {}) or {}
            if not isinstance(existing_sub, dict):
                existing_sub = {}
            updates["subtitle"] = _deep_merge(
                existing_sub, self._build_subtitle_overlay()
            )

            # xhs sub-block: deep-merge existing + form value
            existing_xhs = raw.get("xhs", {}) or {}
            if not isinstance(existing_xhs, dict):
                existing_xhs = {}
            updates["xhs"] = _deep_merge(
                existing_xhs,
                {"profile_dir": self.query_one(
                    "#set-xhs-profile-dir", Input
                ).value},
            )

            # transcribe sub-block: deep-merge existing + form values
            existing_tr = raw.get("transcribe", {}) or {}
            if not isinstance(existing_tr, dict):
                existing_tr = {}
            updates["transcribe"] = _deep_merge(
                existing_tr, self._build_transcribe_overlay()
            )

            save_config_fields(self._config_path, updates)
            msg.update("已保存（注意：yaml 回写会丢注释，项目既有行为）")
        except Exception as exc:
            msg.update(f"保存失败: {exc}")
