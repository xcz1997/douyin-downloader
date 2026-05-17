"""Subtitle panel: drives core SubtitleRunner in a Textual thread worker.

SubtitleRunner.run() is synchronous/blocking; progress granularity is
one result line per video (the runner exposes no finer callback — a
documented limitation, core is not changed for this).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Label, Static

_SOURCES = ["track", "ocr", "asr"]
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"}


def parse_ocr_param(raw: str) -> float | None:
    """解析 OCR interval/similarity 输入；正浮点 → float，否则 None。"""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        v = float(stripped)
    except ValueError:
        return None
    return v if v > 0 else None


def build_runner_spec(
    path: str, sources: list[str], asr_model: str,
    ocr_interval: float | None = None,
    ocr_similarity: float | None = None,
) -> dict[str, Any]:
    """Pure resolver → unit-testable."""
    if not sources:
        return {"error": "未选择任何字幕源"}
    return {
        "path": path,
        "sources": sources,
        "asr_model": asr_model,
        "ocr_interval": ocr_interval,
        "ocr_similarity": ocr_similarity,
    }


class SubtitlePanel(Static):
    def __init__(self) -> None:
        super().__init__(id="panel-subtitle")
        self._worker = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="视频文件或目录路径", id="sub-path")
            for s in _SOURCES:
                yield Checkbox(s, value=(s == "ocr"), id=f"sub-src-{s}")
            yield Input(value="0.6b", id="sub-asr-model")
            yield Label("OCR 抽帧间隔（秒）", id="sub-ocr-interval-label")
            yield Input(value="0.5", id="sub-ocr-interval")
            yield Label("OCR 相似度阈值（0-1）", id="sub-ocr-similarity-label")
            yield Input(value="0.7", id="sub-ocr-similarity")
            yield Button("开始提取", id="sub-start", variant="primary")
            yield Button("停止", id="sub-stop")
            yield Label("", id="sub-msg")

    def _dispatch(self, path: str, sources: list[str],
                  asr_model: str) -> None:
        try:
            raw_interval = self.query_one("#sub-ocr-interval", Input).value
            raw_similarity = self.query_one("#sub-ocr-similarity", Input).value
        except Exception:
            raw_interval = ""
            raw_similarity = ""
        ocr_interval = parse_ocr_param(raw_interval)
        _sim = parse_ocr_param(raw_similarity)
        ocr_similarity = _sim if (_sim is not None and 0 < _sim <= 1) else None
        spec = build_runner_spec(path, sources, asr_model,
                                 ocr_interval=ocr_interval,
                                 ocr_similarity=ocr_similarity)
        try:
            msg = self.query_one("#sub-msg", Label)
        except Exception:
            msg = None
        if "error" in spec:
            if msg is not None:
                msg.update(spec["error"])
            return
        if msg is not None:
            msg.update("提取中…")
        self._run_subtitle(spec)

    async def start_subtitle(self, path: str, sources: list[str],
                             asr_model: str) -> None:
        self._dispatch(path, sources, asr_model)

    def _run_subtitle(self, spec: dict) -> None:
        """Run SubtitleRunner in a thread worker. Tests monkeypatch this
        method wholesale so no real OCR/ASR runs."""
        from tui.widgets import LogPane

        try:
            log = self.app.query_one(LogPane)
        except Exception:
            return  # not mounted; nothing to log

        def work() -> None:
            try:
                from core.subtitle.ocr_source import OCRSource
                from core.subtitle.runner import SubtitleRunner

                impls = []
                if "ocr" in spec["sources"]:
                    ocr_kw = {
                        k: v for k, v in (
                            ("interval", spec.get("ocr_interval")),
                            ("similarity", spec.get("ocr_similarity")),
                        ) if v is not None
                    }
                    impls.append(OCRSource(**ocr_kw))
                if "track" in spec["sources"]:
                    from core.subtitle.track_source import TrackSource
                    impls.append(TrackSource())
                if "asr" in spec["sources"]:
                    from core.subtitle.asr_source import ASRSource
                    impls.append(ASRSource(model=spec["asr_model"]))
                runner = SubtitleRunner(impls, sources=spec["sources"])
                p = Path(spec["path"])
                if p.is_file():
                    vids = [p]
                else:
                    vids = sorted(
                        f for f in p.rglob("*")
                        if f.suffix.lower() in _VIDEO_EXTS
                    )
                if not vids:
                    self.app.call_from_thread(
                        log.write,
                        f"[yellow]未找到视频: {spec['path']}[/yellow]"
                    )
                    return
                for v in vids:
                    try:
                        written = runner.run(v, None)
                        names = ", ".join(
                            x.name for x in written
                        ) or "无产出"
                        self.app.call_from_thread(
                            log.write, f"{v.name} → {names}"
                        )
                    except Exception as exc:
                        self.app.call_from_thread(
                            log.write,
                            f"[red]{v.name} 失败: {exc}[/red]"
                        )
            except Exception as exc:  # init/setup must not crash TUI
                self.app.call_from_thread(
                    log.write, f"[red]字幕初始化失败: {exc}[/red]"
                )

        self._worker = self.run_worker(work, thread=True, exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sub-start":
            path = self.query_one("#sub-path", Input).value
            sources = [s for s in _SOURCES
                       if self.query_one(f"#sub-src-{s}", Checkbox).value]
            model = self.query_one("#sub-asr-model", Input).value
            self._dispatch(path, sources, model)
        elif event.button.id == "sub-stop":
            if self._worker is not None:
                # Cancels the worker wrapper; a blocking OCR/ASR call
                # already in progress runs to the end of the current
                # video (thread workers aren't cooperatively cancellable).
                self._worker.cancel()
                self.query_one("#sub-msg", Label).update("已停止")
