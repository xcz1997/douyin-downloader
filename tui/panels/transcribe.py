"""转录面板：驱动 ImageTranscriber，在 Textual 线程 worker 中跑。"""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static

# 复用 runner 的纯函数；TUI 自己的 spec 校验也走它
from core.transcribe.runner import build_transcribe_spec  # noqa: F401


class TranscribePanel(Static):
    def __init__(self) -> None:
        super().__init__(id="panel-transcribe")
        self._worker = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(classes="card") as g_in:
                g_in.border_title = "输入"
                yield Input(placeholder="笔记目录或其父目录",
                            id="tr-path")
            with Vertical(classes="card") as g_opt:
                g_opt.border_title = "选项"
                yield Checkbox("覆盖重跑（默认已存在则跳过）",
                               value=False, id="tr-overwrite")
                yield Label("模型（留空用配置）")
                yield Input(placeholder="如 qwen-vl-max", id="tr-model")
            with Horizontal(classes="actions"):
                yield Button("开始转录", id="tr-start", variant="primary")
                yield Button("停止", id="tr-stop")
            yield Label("", id="tr-msg", classes="msg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tr-start":
            path = self.query_one("#tr-path", Input).value
            overwrite = self.query_one("#tr-overwrite", Checkbox).value
            model = self.query_one("#tr-model", Input).value.strip()
            self._dispatch(path, overwrite, model)
        elif event.button.id == "tr-stop":
            if self._worker is not None:
                self._worker.cancel()
                self.query_one("#tr-msg", Label).update("已停止")

    def _dispatch(self, path: str, overwrite: bool, model: str) -> None:
        spec = build_transcribe_spec(path)
        msg = self.query_one("#tr-msg", Label)
        if "error" in spec:
            msg.update(spec["error"])
            return
        msg.update("转录中…")
        self._run(spec["path"], overwrite, model)

    def _run(self, path: str, overwrite: bool, model: str) -> None:
        """线程 worker 跑 ImageTranscriber。测试整体 monkeypatch 此方法。"""
        from tui.widgets import LogPane
        try:
            log = self.app.query_one(LogPane)
        except Exception:
            return

        def work() -> None:
            try:
                from core.config import ConfigLoader
                from core.transcribe.client import VLMClient, VLMError
                from core.transcribe.runner import (
                    ImageTranscriber, find_data_json,
                )
                cfg = ConfigLoader("config.yml").load().transcribe
                if overwrite:
                    cfg.overwrite = True
                if model:
                    cfg.model = model
                api_key = os.environ.get(cfg.api_key_env, "")
                try:
                    client = VLMClient(
                        base_url=cfg.base_url, model=cfg.model,
                        api_key=api_key, timeout=cfg.timeout, retry=cfg.retry)
                except VLMError as exc:
                    self.app.call_from_thread(
                        log.write, f"[red]转录启动失败: {exc}[/red]")
                    return
                transcriber = ImageTranscriber(client, cfg)
                p = Path(path)
                if find_data_json(p) is not None:
                    dirs = [p]
                else:
                    dirs = sorted({dj.parent
                                   for dj in p.rglob("*_data.json")})
                if not dirs:
                    self.app.call_from_thread(
                        log.write, f"[yellow]未找到笔记目录: {path}[/yellow]")
                    return
                for d in dirs:
                    try:
                        out = transcriber.transcribe_dir(d)
                        self.app.call_from_thread(
                            log.write,
                            f"{d.name} → {out.name if out else '跳过'}")
                    except Exception as exc:  # noqa: BLE001
                        self.app.call_from_thread(
                            log.write, f"[red]{d.name} 失败: {exc}[/red]")
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(
                    log.write, f"[red]转录初始化失败: {exc}[/red]")

        self._worker = self.run_worker(work, thread=True, exclusive=True)
