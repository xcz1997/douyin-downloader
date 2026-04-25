"""Platform-agnostic file download engine consuming MediaItem."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiohttp

from core.logger import BoundLogger
from core.models import DownloadResult, DownloadTask
from core.platform import MediaAsset, MediaItem
from core.tracer import Tracer, TraceSpan


class DownloadEngine:
    """Downloads MediaAssets from a MediaItem to the save directory.

    Directory layout: ``save_path / {platform} / {author} / {ts}_{desc} / ...``

    Args:
        save_path: Root output directory.
        tracer: Tracer for span events (e.g. skip / retry).
        logger: Bound logger for structured messages.
        concurrency: Semaphore limit for parallel file downloads.
        download_music: If False, skip ``kind == "music"`` assets.
        download_cover: If False, skip ``kind == "cover"`` assets.
        download_json: If False, skip writing ``_data.json``.
        download_live_photo: If False, skip ``kind == "video_live"`` assets.
    """

    def __init__(
        self,
        save_path: Path,
        tracer: Tracer,
        logger: BoundLogger,
        concurrency: int = 5,
        download_music: bool = True,
        download_cover: bool = True,
        download_json: bool = True,
        download_live_photo: bool = True,
    ) -> None:
        self._save_path = save_path
        self._tracer = tracer
        self._log = logger
        self._semaphore = asyncio.Semaphore(concurrency)
        self._session: aiohttp.ClientSession | None = None
        self._download_music = download_music
        self._download_cover = download_cover
        self._download_json = download_json
        self._download_live_photo = download_live_photo

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            # Browser UA + Referer required: XHS image CDNs (sns-img-bd,
            # sns-webpic-qc) return 403 to aiohttp's default UA. Douyin
            # CDNs accept this UA without issue.
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.xiaohongshu.com/",
                },
            )

    def _build_save_dir(self, item: MediaItem) -> Path:
        author = item.author or "unknown"
        desc = (item.desc or "").replace("/", "_").replace("\\", "_")[:50]
        if item.create_time:
            ts = datetime.fromtimestamp(item.create_time).strftime(
                "%Y-%m-%d_%H-%M-%S"
            )
        else:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder = f"{ts}_{desc}" if desc else ts
        path = self._save_path / item.platform / author / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _filename_for(
        self, asset: MediaAsset, folder_name: str, index: int,
    ) -> str:
        """Decide the on-disk filename for an asset."""
        if asset.suggested_filename:
            return f"{asset.suggested_filename}.{asset.ext}"
        if asset.kind == "video_main":
            return f"{folder_name}.{asset.ext}"
        if asset.kind == "music":
            return f"{folder_name}_music.{asset.ext}"
        if asset.kind == "cover":
            return f"{folder_name}_cover.{asset.ext}"
        # image / video_live without suggested_filename falls back
        return f"{asset.kind}_{index}.{asset.ext}"

    def _should_skip(self, asset: MediaAsset) -> bool:
        if asset.kind == "music" and not self._download_music:
            return True
        if asset.kind == "cover" and not self._download_cover:
            return True
        if asset.kind == "video_live" and not self._download_live_photo:
            return True
        return False

    async def download_media(
        self,
        item: MediaItem,
        parent_span: TraceSpan,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> DownloadResult:
        """Download all (selected) assets of a MediaItem into one folder."""
        task = DownloadTask(
            task_id=item.id or "unknown",
            trace_id=parent_span.trace_id,
            url="",
            content_type=item.platform,
        )
        t0 = time.time()
        save_dir = self._build_save_dir(item)
        folder_name = save_dir.name
        files_written = 0
        total_bytes = 0
        success = True

        for i, asset in enumerate(item.assets):
            if self._should_skip(asset):
                continue
            path = save_dir / self._filename_for(asset, folder_name, i)
            ok, nbytes = await self.download_file(
                asset.url, path, parent_span,
                fallback_urls=asset.fallback_urls,
                on_progress=on_progress,
            )
            if ok:
                files_written += 1
                total_bytes += nbytes
            else:
                success = False
            if asset.kind == "image":
                await asyncio.sleep(0.3)

        if self._download_json:
            json_path = save_dir / f"{folder_name}_data.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(item.raw, f, ensure_ascii=False, indent=2)
            files_written += 1

        task.file_paths = [str(save_dir)]
        return DownloadResult(
            task=task, success=success,
            files_written=files_written, elapsed=time.time() - t0,
            bytes_downloaded=total_bytes,
        )

    async def download_file(
        self,
        url: str,
        path: Path,
        parent_span: TraceSpan,
        fallback_urls: list[str] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[bool, int]:
        if path.exists() and path.stat().st_size > 0:
            self._tracer.add_event(parent_span, "file_skip", path=path.name)
            return (True, 0)

        await self._ensure_session()
        async with self._semaphore:
            all_urls = [url] + (fallback_urls or [])
            for i, u in enumerate(all_urls):
                try:
                    u = u.replace("playwm", "play")
                    async with self._session.get(u) as resp:
                        if resp.status == 200:
                            content_length = int(
                                resp.headers.get("Content-Length", 0)
                            )
                            chunks: list[bytes] = []
                            bytes_read = 0
                            if on_progress:
                                on_progress(0, content_length, path.name)
                            async for chunk in resp.content.iter_chunked(65536):
                                chunks.append(chunk)
                                bytes_read += len(chunk)
                                if on_progress:
                                    on_progress(
                                        bytes_read, content_length, path.name,
                                    )
                            data = b"".join(chunks)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                            self._log.debug(
                                "文件已下载", file=path.name,
                                size_kb=len(data) // 1024,
                            )
                            return (True, len(data))
                        if resp.status == 403 and i < len(all_urls) - 1:
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if i < len(all_urls) - 1:
                        continue

        self._log.warn("文件下载失败", file=path.name, urls_tried=len(all_urls))
        return (False, 0)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
