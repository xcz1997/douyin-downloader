"""Platform-agnostic download pipeline orchestrator."""

from __future__ import annotations

import traceback

from core.dashboard import Dashboard
from core.downloader_engine import DownloadEngine
from core.errors import CookieExpiredError, RetryableError, SkippableError
from core.logger import BoundLogger
from core.models import AppConfig, DownloadTask, TraceSpan
from core.platform import (
    ContentRef, ListPage, MediaItem, PlatformRegistry,
)
from core.tracer import Tracer


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

    def _progress_cb(self, done: int, total: int, name: str) -> None:
        self._dashboard.update_bytes_progress(done, total, name)

    async def run(self) -> None:
        session_span = self._tracer.start_trace("session", url="batch")

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            cookie_state = await self._cookie_mgr.ensure_valid_cookie()
            cs.attributes["source"] = cookie_state.source
            self._dashboard.set_cookie_state(cookie_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")
        for task in tasks:
            self._dashboard.add_task(task)
        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

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

        self._dashboard.set_status(f"正在获取{label}…")
        with self._tracer.context_span(root, f"fetch_all_{ref.content_type}") as fs:
            while True:
                page: ListPage = await client.fetch_list(ref, cursor, fs)
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
                cursor = page.next_cursor
        self._dashboard.clear_status()

        total = len(all_items)
        # Behavioral note: the old pipeline only honored a per-type limit
        # for user posts (keyed as "post" in config.number). This
        # generalization lets mix/music/collection/search/topic also
        # respect a config.number[<content_type>] cap if the user sets
        # one. Missing keys default to 0 which means "no limit",
        # preserving old behavior for configs that only set `post`.
        limit_key = "post" if ref.content_type == "user" else ref.content_type
        limit = self._config.number.get(limit_key, 0)
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
                self._dashboard.add_bytes(result.bytes_downloaded)
                if result.success:
                    downloaded += 1
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
