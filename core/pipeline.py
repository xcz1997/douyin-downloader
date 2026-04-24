"""Download pipeline orchestrator for Douyin content.

Coordinates URL resolution, content-type detection, task scheduling,
and error recovery across the API client, download engine, cookie
manager, and dashboard.
"""

from __future__ import annotations

import asyncio
import re
import time
import traceback

import aiohttp

from core.api_client import DouyinAPIClient
from core.cookie import CookieManager
from core.dashboard import Dashboard
from core.downloader_engine import DownloadEngine
from core.errors import CookieExpiredError, RetryableError, SkippableError
from core.logger import BoundLogger
from core.models import AppConfig, DownloadTask, TraceSpan
from core.tracer import Tracer

# ---------------------------------------------------------------------------
# URL pattern constants
# ---------------------------------------------------------------------------

# NOTE: These regexes are temporarily duplicated in core/platforms/douyin.py
# during the Phase 1-2 migration. Task 8 of the XHS integration plan removes
# them from this file once DownloadPipeline routes through PlatformRegistry.
_SHORT_URL_RE = re.compile(r"https?://v\.douyin\.com/\w+")
_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")
_NOTE_RE = re.compile(r"douyin\.com/note/(\d+)")
_USER_RE = re.compile(r"(?:sec_uid=|/user/)(MS4wLjAB[\w\-]+)")
_MIX_RE = re.compile(r"mix_id=(\d+)|/collection/(\d+)")
_MUSIC_RE = re.compile(r"music/(\d+)")


class DownloadPipeline:
    """Orchestrates the full download lifecycle for a batch of URLs.

    Responsibilities:
    - Validate and refresh cookies before starting.
    - Resolve short URLs to canonical long URLs.
    - Detect content type and extract the relevant ID from each URL.
    - Route each task to the appropriate handler (single post, user,
      mix playlist, or music track).
    - Handle errors uniformly: retry on ``CookieExpiredError``, skip
      on ``SkippableError``, mark failed on ``RetryableError`` or any
      other exception.
    - Keep the dashboard updated throughout.

    Args:
        config: Application configuration (links, modes, limits, etc.).
        api: Authenticated Douyin API client.
        engine: File download engine.
        cookie_mgr: Cookie acquisition and validation manager.
        tracer: Distributed tracing backend.
        logger: Structured logger bound to this component.
        dashboard: Live progress dashboard.
    """

    def __init__(
        self,
        config: AppConfig,
        api: DouyinAPIClient,
        engine: DownloadEngine,
        cookie_mgr: CookieManager,
        tracer: Tracer,
        logger: BoundLogger,
        dashboard: Dashboard,
    ) -> None:
        self._config = config
        self._api = api
        self._engine = engine
        self._cookie_mgr = cookie_mgr
        self._tracer = tracer
        self._log = logger
        self._dashboard = dashboard

    def _progress_cb(self, done: int, total: int, name: str) -> None:
        self._dashboard.update_bytes_progress(done, total, name)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Execute the full download pipeline for all configured URLs.

        Flow:
        1. Ensure a valid cookie is present, refreshing if needed.
        2. Prepare tasks (resolve short URLs, detect types, extract IDs).
        3. Register all tasks with the dashboard.
        4. Execute each task in sequence, refreshing the dashboard after each.
        5. End the root trace span.
        """
        session_span = self._tracer.start_trace("session", url="batch")

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            cookie_state = await self._cookie_mgr.ensure_valid_cookie()
            cs.attributes["source"] = cookie_state.source
            self._api.update_cookie(cookie_state)
            self._dashboard.set_cookie_state(cookie_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")

        for task in tasks:
            self._dashboard.add_task(task)

        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

    # ------------------------------------------------------------------
    # Task preparation
    # ------------------------------------------------------------------

    async def _prepare_tasks(self, parent_span: TraceSpan) -> list[DownloadTask]:
        """Build a ``DownloadTask`` for each URL in the config.

        Short URLs are resolved to their canonical form before type
        detection and ID extraction.

        Args:
            parent_span: The parent trace span for preparation spans.

        Returns:
            Ordered list of prepared ``DownloadTask`` instances.
        """
        tasks: list[DownloadTask] = []
        for i, url in enumerate(self._config.links):
            with self._tracer.context_span(parent_span, "prepare_url", url=url) as span:
                resolved = url
                if self.is_short_url(url):
                    resolved = await self._resolve_short_url(url)
                    span.attributes["resolved"] = resolved

                content_type = self.detect_content_type(resolved)
                extracted_id = self.extract_id(resolved, content_type)
                span.attributes["type"] = content_type
                span.attributes["id"] = extracted_id

                task = DownloadTask(
                    task_id=f"task_{i:03d}",
                    trace_id=parent_span.trace_id,
                    url=url,
                    content_type=content_type,
                    resolved_url=resolved,
                    extracted_id=extracted_id,
                )
                tasks.append(task)
        return tasks

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task: DownloadTask) -> None:
        """Execute a single download task with unified error handling.

        Error recovery policy:
        - ``CookieExpiredError``: re-acquire cookie and retry the task.
        - ``SkippableError``: mark failed, continue to next task.
        - ``RetryableError``: mark failed (retries exhausted by API client).
        - ``Exception``: log full traceback, mark failed, continue.

        Args:
            task: The task to execute.
        """
        root = self._tracer.start_trace(f"download_{task.content_type}", url=task.url)
        task.trace_id = root.trace_id
        task.status = "running"
        self._dashboard.update_task(task)

        try:
            match task.content_type:
                case "video" | "image":
                    await self._handle_single(task, root)
                case "user":
                    await self._handle_user(task, root)
                case "mix":
                    await self._handle_mix(task, root)
                case "music":
                    await self._handle_music(task, root)
            task.status = "done"
            self._dashboard.log_done(
                task.url[:50],
                True,
                f"{task.stats.get('downloaded', 0)} 个作品",
                trace_id=root.trace_id,
            )

        except CookieExpiredError:
            self._tracer.add_event(root, "cookie_expired")
            self._log.warn("Cookie 失效，重新获取...")
            cookie = await self._cookie_mgr.ensure_valid_cookie()
            self._api.update_cookie(cookie)
            self._dashboard.set_cookie_state(cookie)
            await self._execute_task(task)
            return

        except SkippableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id
            )

        except RetryableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, f"重试耗尽: {exc}", trace_id=root.trace_id
            )

        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            self._log.error("未预期错误", error=str(exc), tb=traceback.format_exc())
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id
            )

        finally:
            self._tracer.end_span(root, status=task.status)
            self._dashboard.update_task(task)

    # ------------------------------------------------------------------
    # Content-type handlers
    # ------------------------------------------------------------------

    async def _handle_single(self, task: DownloadTask, root: TraceSpan) -> None:
        with self._tracer.context_span(
            root, "fetch_info", aweme_id=task.extracted_id
        ) as span:
            info = await self._api.get_video_info(task.extracted_id, span)
            self._dashboard.record_api_call(True)

        desc = (info.get("desc") or "")[:40]
        author = info.get("author", {}).get("nickname", "")
        self._dashboard.set_current_item(desc=desc, author=author, index=1, total=1)

        with self._tracer.context_span(root, "download_media") as span:
            result = await self._engine.download_media(
                info, span, on_progress=self._progress_cb,
            )

        self._dashboard.clear_current_item()
        self._dashboard.add_bytes(result.bytes_downloaded)
        task.stats["downloaded"] = 1 if result.success else 0

        self._dashboard.log_item_done(
            desc or task.url[:50],
            result.success,
            f"{result.files_written} 文件, {result.elapsed:.1f}s" if result.success
            else (result.error or "下载失败"),
        )

    async def _handle_user(self, task: DownloadTask, root: TraceSpan) -> None:
        downloaded = 0
        cursor = 0
        all_posts: list[dict] = []

        self._dashboard.set_status("正在获取作品列表…")
        with self._tracer.context_span(root, "fetch_all_posts") as fetch_span:
            while True:
                if "post" in self._config.mode:
                    page = await self._api.get_user_posts(
                        task.extracted_id, cursor, fetch_span
                    )
                else:
                    page = await self._api.get_user_likes(
                        task.extracted_id, cursor, fetch_span
                    )
                self._dashboard.record_api_call(True)

                aweme_list = page.get("aweme_list", [])
                if not aweme_list:
                    break
                all_posts.extend(aweme_list)
                fetch_span.attributes["fetched"] = len(all_posts)
                self._dashboard.set_status(
                    f"正在获取作品列表… 已获取 {len(all_posts)} 个"
                )
                self._dashboard.refresh()

                if not page.get("has_more"):
                    break
                cursor = page.get("max_cursor", 0)
        self._dashboard.clear_status()

        total = len(all_posts)
        post_limit = self._config.number.get("post", 0)
        effective_total = min(total, post_limit) if post_limit > 0 else total

        with self._tracer.context_span(root, "download_posts", total=total) as dl_span:
            for i, post in enumerate(all_posts):
                if post_limit > 0 and downloaded >= post_limit:
                    break
                desc = (post.get("desc") or "")[:40]
                author = post.get("author", {}).get("nickname", "")
                self._dashboard.set_current_item(
                    desc=desc, author=author,
                    index=i + 1, total=effective_total,
                )
                with self._tracer.context_span(
                    dl_span,
                    "download_media",
                    index=i + 1,
                    aweme_id=post.get("aweme_id"),
                ) as media_span:
                    result = await self._engine.download_media(
                        post, media_span, on_progress=self._progress_cb,
                    )
                    self._dashboard.clear_current_item()
                    self._dashboard.add_bytes(result.bytes_downloaded)
                    if result.success:
                        downloaded += 1
                        self._dashboard.log_item_done(
                            desc or f"作品 {i+1}",
                            True,
                            f"{result.files_written} 文件, {result.elapsed:.1f}s",
                        )
                    else:
                        self._dashboard.log_item_done(
                            desc or f"作品 {i+1}",
                            False,
                            result.error or "下载失败",
                            trace_id=media_span.trace_id,
                        )
                self._dashboard.update_progress(task, i + 1, effective_total)
                self._dashboard.refresh()

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total

    async def _handle_mix(self, task: DownloadTask, root: TraceSpan) -> None:
        downloaded = 0
        cursor = 0
        all_posts: list[dict] = []

        self._dashboard.set_status("正在获取合集列表…")
        with self._tracer.context_span(root, "fetch_all_mix") as fetch_span:
            while True:
                page = await self._api.get_mix_items(
                    task.extracted_id, cursor, fetch_span,
                )
                self._dashboard.record_api_call(True)
                aweme_list = page.get("aweme_list", [])
                if not aweme_list:
                    break
                all_posts.extend(aweme_list)
                self._dashboard.set_status(
                    f"正在获取合集列表… 已获取 {len(all_posts)} 个"
                )
                self._dashboard.refresh()
                if not page.get("has_more"):
                    break
                cursor = page.get("cursor", 0)
        self._dashboard.clear_status()

        total = len(all_posts)
        with self._tracer.context_span(root, "download_posts", total=total) as dl_span:
            for i, post in enumerate(all_posts):
                desc = (post.get("desc") or "")[:40]
                author = post.get("author", {}).get("nickname", "")
                self._dashboard.set_current_item(
                    desc=desc, author=author, index=i + 1, total=total,
                )
                with self._tracer.context_span(
                    dl_span, "download_media",
                    index=i + 1, aweme_id=post.get("aweme_id"),
                ) as span:
                    result = await self._engine.download_media(
                        post, span, on_progress=self._progress_cb,
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
                            result.error or "下载失败", trace_id=span.trace_id,
                        )
                self._dashboard.update_progress(task, i + 1, total)
                self._dashboard.refresh()

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total

    async def _handle_music(self, task: DownloadTask, root: TraceSpan) -> None:
        downloaded = 0
        cursor = 0
        all_posts: list[dict] = []

        self._dashboard.set_status("正在获取音乐作品列表…")
        with self._tracer.context_span(root, "fetch_all_music") as fetch_span:
            while True:
                page = await self._api.get_music_items(
                    task.extracted_id, cursor, fetch_span,
                )
                self._dashboard.record_api_call(True)
                aweme_list = page.get("aweme_list", [])
                if not aweme_list:
                    break
                all_posts.extend(aweme_list)
                self._dashboard.set_status(
                    f"正在获取音乐作品列表… 已获取 {len(all_posts)} 个"
                )
                self._dashboard.refresh()
                if not page.get("has_more"):
                    break
                cursor = page.get("cursor", 0)
        self._dashboard.clear_status()

        total = len(all_posts)
        with self._tracer.context_span(root, "download_posts", total=total) as dl_span:
            for i, post in enumerate(all_posts):
                desc = (post.get("desc") or "")[:40]
                author = post.get("author", {}).get("nickname", "")
                self._dashboard.set_current_item(
                    desc=desc, author=author, index=i + 1, total=total,
                )
                with self._tracer.context_span(
                    dl_span, "download_media",
                    index=i + 1, aweme_id=post.get("aweme_id"),
                ) as span:
                    result = await self._engine.download_media(
                        post, span, on_progress=self._progress_cb,
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
                            result.error or "下载失败", trace_id=span.trace_id,
                        )
                self._dashboard.update_progress(task, i + 1, total)
                self._dashboard.refresh()

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total

    # ------------------------------------------------------------------
    # Short-URL resolution
    # ------------------------------------------------------------------

    async def _resolve_short_url(self, url: str) -> str:
        """Follow the first redirect from a short URL to its canonical target.

        Args:
            url: A ``v.douyin.com`` short URL.

        Returns:
            The ``Location`` header value from the redirect, or the
            original URL if resolution fails.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (301, 302):
                        return str(resp.headers.get("Location", url))
        except Exception:
            pass
        return url

    # ------------------------------------------------------------------
    # Static URL utilities
    # ------------------------------------------------------------------

    @staticmethod
    def is_short_url(url: str) -> bool:
        """Return True if the URL is a Douyin short link (v.douyin.com).

        Args:
            url: The URL string to test.

        Returns:
            ``True`` when the URL matches the ``v.douyin.com`` short-link
            pattern, ``False`` otherwise.
        """
        return bool(_SHORT_URL_RE.match(url))

    @staticmethod
    def detect_content_type(url: str) -> str:
        """Classify a Douyin URL into one of the known content types.

        Precedence: note (image) > video > user > mix > music > video (default).

        Args:
            url: A resolved (non-short) Douyin URL.

        Returns:
            One of ``"video"``, ``"image"``, ``"user"``, ``"mix"``,
            ``"music"``.  Defaults to ``"video"`` when nothing matches.
        """
        if _NOTE_RE.search(url):
            return "image"
        if _VIDEO_RE.search(url):
            return "video"
        if _USER_RE.search(url):
            return "user"
        if _MIX_RE.search(url):
            return "mix"
        if _MUSIC_RE.search(url):
            return "music"
        return "video"

    @staticmethod
    def extract_id(url: str, content_type: str) -> str | None:
        """Extract the primary ID from a URL for a given content type.

        Args:
            url: A resolved Douyin URL.
            content_type: One of ``"video"``, ``"image"``, ``"user"``,
                ``"mix"``, or ``"music"``.

        Returns:
            The extracted ID string, or ``None`` if no match is found.
        """
        match content_type:
            case "video":
                m = _VIDEO_RE.search(url)
                return m.group(1) if m else None
            case "image":
                m = _NOTE_RE.search(url)
                return m.group(1) if m else None
            case "user":
                m = _USER_RE.search(url)
                return m.group(1) if m else None
            case "mix":
                m = _MIX_RE.search(url)
                return (m.group(1) or m.group(2)) if m else None
            case "music":
                m = _MUSIC_RE.search(url)
                return m.group(1) if m else None
        return None
