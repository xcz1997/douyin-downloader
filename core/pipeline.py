"""Platform-agnostic download pipeline orchestrator."""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from core.dashboard import Dashboard
from core.downloader_engine import DownloadEngine
from core.errors import CookieExpiredError, RetryableError, SkippableError
from core.logger import BoundLogger
from core.models import AppConfig, DownloadTask, TraceSpan
from core.platform import (
    ContentRef, ListPage, MediaItem, PlatformRegistry,
)
from core.subtitle.asr_source import ASRSource
from core.subtitle.ocr_source import OCRSource
from core.subtitle.runner import SubtitleRunner
from core.subtitle.track_source import TrackSource
from core.tracer import Tracer
from core.transcribe.runner import build_image_transcriber

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"}


def _collect_video_files(roots: list[str]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        p = Path(r)
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
            out.append(p)
        elif p.is_dir():
            for ext in _VIDEO_EXTS:
                out.extend(p.rglob(f"*{ext}"))
    return out


def _load_raw_json(root: str) -> dict | None:
    p = Path(root)
    if not p.is_dir():
        return None
    for j in sorted(p.glob("*_data.json")):
        try:
            return json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def build_subtitle_runner(config) -> SubtitleRunner | None:
    sub = config.subtitle
    if not sub.enabled:
        return None
    impls = [
        OCRSource(interval=sub.ocr_interval, similarity=sub.ocr_similarity),
        TrackSource(),
        ASRSource(model=sub.asr_model),
    ]
    return SubtitleRunner(impls, sources=list(sub.sources))


class DownloadPipeline:
    """Orchestrates downloading for a batch of URLs across platforms.

    Responsibilities:
    - Ensure all needed cookies are valid via ``cookie_mgr``.
    - Match every URL to a registered platform; resolve short URLs.
    - For single-item refs, fetch one MediaItem and hand to the engine.
    - For list refs (user/collection/music/search/topic), paginate via
      ``PlatformClient.fetch_list`` and download each MediaItem.
    - Uniform error handling and dashboard updates.
    """

    def __init__(
        self,
        config: AppConfig,
        registry: PlatformRegistry,
        engine: DownloadEngine,
        cookie_mgr,
        tracer: Tracer,
        logger: BoundLogger,
        dashboard: Dashboard,
    ) -> None:
        self._config = config
        self._registry = registry
        self._engine = engine
        self._cookie_mgr = cookie_mgr
        self._tracer = tracer
        self._log = logger
        self._dashboard = dashboard
        self._subtitle_runner = build_subtitle_runner(config)
        self._transcriber = build_image_transcriber(config.transcribe)

    async def _run_subtitles(self, result) -> None:
        if self._subtitle_runner is None:
            return
        if result.media_files_written <= 0:
            return
        for root in result.task.file_paths:
            raw = _load_raw_json(root)
            for v in _collect_video_files([root]):
                await asyncio.to_thread(self._subtitle_runner.run, v, raw)

    async def _run_transcribe(self, result) -> None:
        """下载成功的图文笔记，自动转录（失败只告警，不影响下载）。"""
        if self._transcriber is None:
            return
        if result.media_files_written <= 0:
            return
        for root in result.task.file_paths:
            try:
                await asyncio.to_thread(
                    self._transcriber.transcribe_dir, Path(root)
                )
            except Exception as exc:  # noqa: BLE001
                self._log.warn("自动转录失败", root=str(root), error=str(exc))

    def _progress_cb(self, done: int, total: int, name: str) -> None:
        self._dashboard.update_bytes_progress(done, total, name)

    async def run(self) -> None:
        session_span = self._tracer.start_trace("session", url="batch")

        needed_platforms = self._infer_needed_platforms()

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            primary_state = None
            for plat in needed_platforms:
                try:
                    state = await self._cookie_mgr.ensure_valid_cookie(
                        platform=plat,
                    )
                except Exception as exc:
                    self._log.warn(
                        f"平台 {plat} Cookie 获取失败",
                        error=str(exc),
                    )
                    continue
                if primary_state is None:
                    primary_state = state
            cs.attributes["platforms"] = ",".join(needed_platforms)
            if primary_state is not None:
                self._dashboard.set_cookie_state(primary_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")
        for task in tasks:
            self._dashboard.add_task(task)
        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

    def _infer_needed_platforms(self) -> list[str]:
        """Return platform names whose cookies this batch needs.

        Inspects ``config.links`` and uses the registry to match each
        URL. An unresolvable URL doesn't add a platform — if it turns
        out unhandled later, ``_prepare_tasks`` logs and skips.
        """
        seen: list[str] = []
        for url in self._config.links:
            match = self._registry.match(url)
            if match is None:
                continue
            name = match[0].name
            if name not in seen:
                seen.append(name)
        return seen

    async def _prepare_tasks(
        self, parent_span: TraceSpan,
    ) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for i, url in enumerate(self._config.links):
            with self._tracer.context_span(
                parent_span, "prepare_url", url=url,
            ) as span:
                match = self._registry.match(url)
                if match is None:
                    self._log.warn("未识别的 URL 来源", url=url)
                    continue
                platform, client, ref = match

                if ref.content_type == "short":
                    resolved = await client.resolve_short_url(url)
                    span.attributes["resolved"] = resolved
                    match2 = self._registry.match(resolved)
                    if match2 is None:
                        self._log.warn("短链解析后仍无法识别", url=resolved)
                        continue
                    if match2[0].name != platform.name:
                        self._log.warn(
                            "短链跨平台跳转",
                            orig=platform.name,
                            resolved=match2[0].name,
                            url=resolved,
                        )
                    platform, client, ref = match2

                # Inject user-mode into ContentRef extra for douyin user.
                if ref.content_type == "user" and platform.name == "douyin":
                    if "like" in self._config.mode:
                        ref.extra["mode"] = "like"
                    else:
                        ref.extra["mode"] = "post"

                span.attributes["platform"] = platform.name
                span.attributes["type"] = ref.content_type
                span.attributes["id"] = ref.resource_id

                task = DownloadTask(
                    task_id=f"task_{i:03d}",
                    trace_id=parent_span.trace_id,
                    url=url,
                    content_type=ref.content_type,
                    resolved_url=ref.resolved_url,
                    extracted_id=ref.resource_id,
                )
                task.stats["platform"] = platform.name
                task.stats["_ref"] = ref
                task.stats["_client"] = client
                tasks.append(task)
        return tasks

    async def _execute_task(self, task: DownloadTask) -> None:
        ref: ContentRef = task.stats.pop("_ref")
        client = task.stats.pop("_client")

        root = self._tracer.start_trace(
            f"download_{ref.platform}_{ref.content_type}",
            url=task.url,
        )
        task.trace_id = root.trace_id
        task.status = "running"
        self._dashboard.update_task(task)

        try:
            if ref.content_type in ("single", "video", "image"):
                await self._handle_single(task, ref, client, root)
            else:
                await self._handle_list(task, ref, client, root)
            task.status = "done"
            self._dashboard.log_done(
                task.url[:50], True,
                f"{task.stats.get('downloaded', 0)} 个作品",
                trace_id=root.trace_id,
            )

        except CookieExpiredError:
            self._tracer.add_event(root, "cookie_expired")
            self._log.warn("Cookie 失效，重新获取...")
            cookie_state = await self._cookie_mgr.ensure_valid_cookie()
            self._dashboard.set_cookie_state(cookie_state)
            task.stats["_ref"] = ref
            task.stats["_client"] = client
            await self._execute_task(task)
            return

        except SkippableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id,
            )

        except RetryableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, f"重试耗尽: {exc}",
                trace_id=root.trace_id,
            )

        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            self._log.error(
                "未预期错误", error=str(exc), tb=traceback.format_exc(),
            )
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id,
            )

        finally:
            self._tracer.end_span(root, status=task.status)
            self._dashboard.update_task(task)

    async def _handle_single(
        self, task: DownloadTask, ref: ContentRef, client, root: TraceSpan,
    ) -> None:
        with self._tracer.context_span(
            root, "fetch_info", resource_id=ref.resource_id,
        ) as span:
            item: MediaItem = await client.fetch_single(ref, span)
            self._dashboard.record_api_call(True)

        desc = item.desc[:40]
        self._dashboard.set_current_item(
            desc=desc, author=item.author, index=1, total=1,
        )
        with self._tracer.context_span(root, "download_media") as span:
            result = await self._engine.download_media(
                item, span, on_progress=self._progress_cb,
            )
        self._dashboard.clear_current_item()
        await self._run_subtitles(result)
        await self._run_transcribe(result)
        self._dashboard.add_bytes(result.bytes_downloaded)
        task.stats["downloaded"] = 1 if result.success else 0
        self._dashboard.log_item_done(
            desc or task.url[:50],
            result.success,
            f"{result.files_written} 文件, {result.elapsed:.1f}s"
            if result.success else (result.error or "下载失败"),
        )

    async def _handle_list(
        self, task: DownloadTask, ref: ContentRef, client, root: TraceSpan,
    ) -> None:
        downloaded = 0
        cursor: str | int | None = 0
        all_items: list[MediaItem] = []

        label_map = {
            "user": "作品列表", "mix": "合集列表",
            "music": "音乐作品列表", "collection": "合集列表",
            "search": "搜索结果", "topic": "话题笔记",
        }
        label = label_map.get(ref.content_type, "列表")

        # Compute limit up front so we can hand it to fetch_list as a
        # soft hint and short-circuit our own paging loop. The old code
        # only honored a per-type cap for user posts (key "post"); we
        # generalize so other content types can opt in via
        # config.number[<content_type>].
        limit_key = "post" if ref.content_type == "user" else ref.content_type
        limit = self._config.number.get(limit_key, 0)

        self._dashboard.set_status(f"正在获取{label}…")
        with self._tracer.context_span(root, f"fetch_all_{ref.content_type}") as fs:
            while True:
                page: ListPage = await client.fetch_list(
                    ref, cursor, fs, limit=limit,
                )
                self._dashboard.record_api_call(True)
                if not page.items:
                    break
                all_items.extend(page.items)
                fs.attributes["fetched"] = len(all_items)
                self._dashboard.set_status(
                    f"正在获取{label}… 已获取 {len(all_items)} 个"
                )
                self._dashboard.refresh()
                if not page.has_more:
                    break
                if limit > 0 and len(all_items) >= limit:
                    break
                cursor = page.next_cursor
        self._dashboard.clear_status()

        total = len(all_items)
        effective_total = min(total, limit) if limit > 0 else total

        with self._tracer.context_span(
            root, "download_posts", total=total,
        ) as dl_span:
            for i, item in enumerate(all_items):
                if limit > 0 and downloaded >= limit:
                    break
                desc = item.desc[:40]
                self._dashboard.set_current_item(
                    desc=desc, author=item.author,
                    index=i + 1, total=effective_total,
                )
                with self._tracer.context_span(
                    dl_span, "download_media",
                    index=i + 1, item_id=item.id,
                ) as media_span:
                    result = await self._engine.download_media(
                        item, media_span, on_progress=self._progress_cb,
                    )
                self._dashboard.clear_current_item()
                await self._run_subtitles(result)
                await self._run_transcribe(result)
                self._dashboard.add_bytes(result.bytes_downloaded)
                # limit counter: count notes that produced at least one real
                # media file. result.success goes False on any partial-asset
                # failure (e.g. cover 403 while video succeeded), which would
                # falsely starve the counter and disable `number.post` —
                # media_files_written excludes the always-written _data.json
                # so it reflects "did we actually fetch content for this note".
                if result.media_files_written > 0:
                    downloaded += 1
                if result.success:
                    self._dashboard.log_item_done(
                        desc or f"作品 {i+1}", True,
                        f"{result.files_written} 文件, {result.elapsed:.1f}s",
                    )
                else:
                    self._dashboard.log_item_done(
                        desc or f"作品 {i+1}", False,
                        result.error or "下载失败",
                        trace_id=media_span.trace_id,
                    )
                self._dashboard.update_progress(task, i + 1, effective_total)
                self._dashboard.refresh()

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total
