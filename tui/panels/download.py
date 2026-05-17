"""Download panel: drives core DownloadPipeline in a Textual worker via
TextualSink. TUI never blocks on input() — XHS injection mode is forced
non-interactive; persistent profile is used when config.xhs.profile_dir
is set.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet, Static

from core.config import ConfigLoader


def parse_concurrency(raw: str) -> "int | None":
    """解析并发数输入。空/非正整数/非数字 → None；正整数 → int。"""
    s = raw.strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n > 0 else None


def build_pipeline_args(
    source: str, manual_url: str, config_path: str
) -> dict[str, Any]:
    """Resolve the link list + fixed TUI flags. Pure → unit-testable."""
    if source == "manual":
        links = [manual_url.strip()] if manual_url.strip() else []
    else:
        links = list(ConfigLoader(config_path).load().links)
    return {
        "links": links,
        "config_path": config_path,
        "interactive": False,  # TUI never calls input()
    }


class DownloadPanel(Static):
    def __init__(self, config_path: str = "config.yml", **kwargs) -> None:
        super().__init__(id="panel-download", **kwargs)
        self._config_path = config_path
        self._worker = None
        self._xhs_session = None

    def _concurrency_placeholder(self) -> str:
        """读一次 config.thread 作为并发 Input 的占位文案；读失败兜底。"""
        try:
            t = ConfigLoader(self._config_path).load().thread
            return f"并发数（留空用配置 {t}）"
        except Exception:
            return "并发数（留空用配置）"

    def compose(self) -> ComposeResult:
        with Vertical():
            with RadioSet(id="dl-source"):
                yield RadioButton("config.yml", value=True, id="src-config")
                yield RadioButton("手动输入 URL", id="src-manual")
            yield Input(placeholder="https://v.douyin.com/...",
                        id="dl-url")
            yield Input(placeholder=self._concurrency_placeholder(),
                        id="dl-concurrency")
            yield Checkbox("同时提取字幕", id="dl-subtitle")
            yield Button("开始下载", id="dl-start", variant="primary")
            yield Button("停止", id="dl-stop")
            yield Label("", id="dl-msg")

    async def start_download(
        self,
        source: str,
        manual_url: str,
        concurrency_override: "int | None" = None,
        extract_subtitle: bool = False,
    ) -> None:
        args = build_pipeline_args(source, manual_url, self._config_path)
        sink = self._make_sink()
        try:
            msg = self.query_one("#dl-msg", Label)
        except Exception:
            msg = None
        if not args["links"]:
            if msg is not None:
                msg.update("无链接：请选 config.yml 或输入 URL")
            return
        if msg is not None:
            msg.update("下载中…")
        await self._run_download(
            args["links"], sink, args["interactive"], concurrency_override,
            extract_subtitle,
        )

    def _make_sink(self):
        from tui.sink import TextualSink

        # start_download runs as an ASYNC worker (event-loop thread).
        # _on_event touches widgets on that same thread → call it
        # directly. (call_from_thread is only for thread=True workers
        # and RAISES if called from the event-loop thread.)
        return TextualSink(self._on_event)

    def _on_event(self, event) -> None:
        from tui.widgets import LogPane

        try:
            log = self.app.query_one(LogPane)
        except Exception:
            # Not mounted under a live app (e.g. unit test with a
            # monkeypatched runner). UI/log failures must never break a
            # download (spec error-isolation) — silently skip rendering.
            return
        kind = event["kind"]
        p = event["payload"]
        if kind == "status":
            log.write(f"[b]{p['message']}[/b]")
        elif kind == "log":
            log.write(" ".join(str(a) for a in p.get("args", [])))
        elif kind in ("add_task", "update_task"):
            log.write(f"{kind}: {p}")
        elif kind == "current_item":
            log.write(
                f"正在下载: {p.get('desc','')!r} "
                f"[{p.get('index',0)}/{p.get('total',0)}]"
            )
        elif kind == "bytes_progress":
            bt = p.get("bytes_total", 0)
            pct = int(p["bytes_done"] / bt * 100) if bt else 0
            log.write(f"  ↳ {p.get('name','')}: {pct}%")

    async def _run_download(self, links, sink, interactive,
                            concurrency_override=None,
                            extract_subtitle: bool = False) -> None:
        """Construct and run the real DownloadPipeline. Heavy wiring lives
        here so tests can monkeypatch this method wholesale.

        Mirrors downloader.py's cmd_download assembly.
        """
        import asyncio
        import uuid
        from pathlib import Path

        from core.api_client import DouyinAPIClient
        from core.cookie import CookieManager
        from core.downloader_engine import DownloadEngine
        from core.logger import DualLogger
        from core.pipeline import DownloadPipeline
        from core.platform import PlatformRegistry
        from core.platforms.douyin import (DouyinPlatform,
                                           DouyinPlatformClient)
        from core.platforms.xhs import XHSPlatform, XHSPlatformClient
        from core.platforms.xhs_browser import XHSBrowserSession
        from core.tracer import Tracer

        api = engine = tracer = dl = xhs_session = None
        try:
            cfg = ConfigLoader(self._config_path).load()
            cfg.links = links
            if concurrency_override is not None:
                cfg.thread = concurrency_override
            if extract_subtitle:
                cfg.subtitle.enabled = True
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            dl = DualLogger(log_dir=log_dir, console_level="INFO")
            tracer = Tracer(log_dir=log_dir,
                            session_id=uuid.uuid4().hex[:8])
            cookie_mgr = CookieManager(cfg, tracer=tracer,
                                       logger=dl.get("cookie"))
            api = DouyinAPIClient(cookie_state=None, tracer=tracer,
                                  logger=dl.get("api"), rate_limit=2.0,
                                  max_retries=cfg.retry_times)
            engine = DownloadEngine(
                save_path=cfg.save_path, tracer=tracer,
                logger=dl.get("engine"), concurrency=cfg.thread,
                download_music=cfg.download.music,
                download_cover=cfg.download.cover,
                download_json=cfg.download.json,
            )
            registry = PlatformRegistry()
            registry.register(DouyinPlatform(), DouyinPlatformClient(api))
            xhs_platform = XHSPlatform()
            has_xhs = any(
                xhs_platform.match_url(u) is not None for u in links
            )
            if not has_xhs:
                registry.register(xhs_platform, XHSPlatformClient(None))
            else:
                try:
                    st = await cookie_mgr.ensure_valid_cookie(
                        platform="xhs")
                    xhs_session = XHSBrowserSession(
                        st.value, interactive=interactive,
                        profile_dir=cfg.xhs.profile_dir or None,
                    )
                    await xhs_session.start()
                    self._xhs_session = xhs_session
                    registry.register(xhs_platform,
                                      XHSPlatformClient(xhs_session))
                except Exception as exc:
                    sink.set_status(
                        f"XHS 跳过（{exc}），抖音不受影响")
                    registry.register(xhs_platform,
                                      XHSPlatformClient(None))
            pipeline = DownloadPipeline(
                config=cfg, registry=registry, engine=engine,
                cookie_mgr=cookie_mgr, tracer=tracer,
                logger=dl.get("pipeline"), dashboard=sink,
            )
            try:
                st = await cookie_mgr.ensure_valid_cookie(
                    platform="douyin")
                api.update_cookie(st)
            except Exception as exc:
                sink.set_status(f"抖音 Cookie 获取失败: {exc}")
            await pipeline.run()
        except Exception as exc:
            sink.set_status(f"下载失败: {exc}")
        finally:
            if api is not None:
                await api.close()
            if engine is not None:
                await engine.close()
            if xhs_session is not None:
                await xhs_session.close()
                self._xhs_session = None
            if tracer is not None:
                tracer.close()
            if dl is not None:
                dl.close()
            await asyncio.sleep(0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dl-start":
            src = ("manual"
                   if self.query_one("#src-manual", RadioButton).value
                   else "config")
            url = self.query_one("#dl-url", Input).value
            raw_concurrency = self.query_one("#dl-concurrency", Input).value
            concurrency_override = parse_concurrency(raw_concurrency)
            extract_subtitle = self.query_one("#dl-subtitle", Checkbox).value
            self._worker = self.run_worker(
                self.start_download(src, url, concurrency_override,
                                    extract_subtitle),
                exclusive=True,
            )
        elif event.button.id == "dl-stop":
            if self._worker is not None:
                self._worker.cancel()
                self.query_one("#dl-msg", Label).update("已停止")
