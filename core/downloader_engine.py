import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from core.models import TraceSpan, DownloadResult, DownloadTask
from core.errors import DownloadFileError
from core.tracer import Tracer
from core.logger import BoundLogger


class DownloadEngine:
    def __init__(self, save_path: Path, tracer: Tracer, logger: BoundLogger,
                 concurrency: int = 5, download_music: bool = True,
                 download_cover: bool = True, download_json: bool = True):
        self._save_path = save_path
        self._tracer = tracer
        self._log = logger
        self._semaphore = asyncio.Semaphore(concurrency)
        self._session: aiohttp.ClientSession | None = None
        self._download_music = download_music
        self._download_cover = download_cover
        self._download_json = download_json

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
            )

    def _build_save_dir(self, aweme: dict) -> Path:
        author = aweme.get("author", {}).get("nickname", "unknown")
        desc = (aweme.get("desc") or "")[:50].replace("/", "_").replace("\\", "_")
        raw_time = aweme.get("create_time")
        if isinstance(raw_time, (int, float)):
            dt = datetime.fromtimestamp(raw_time)
        else:
            dt = datetime.now()
        ts = dt.strftime("%Y-%m-%d_%H-%M-%S")
        folder = f"{ts}_{desc}" if desc else ts
        path = self._save_path / author / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def download_media(self, aweme: dict, parent_span: TraceSpan) -> DownloadResult:
        task = DownloadTask(
            task_id=aweme.get("aweme_id", "unknown"),
            trace_id=parent_span.trace_id,
            url="",
            content_type="image" if aweme.get("images") else "video",
        )
        t0 = time.time()
        save_dir = self._build_save_dir(aweme)
        files_written = 0
        success = True
        folder_name = save_dir.name

        if aweme.get("images"):
            images = aweme.get("images", [])
            for i, img in enumerate(images):
                url_list = img.get("url_list", [])
                if url_list:
                    path = save_dir / f"image_{i+1}.jpg"
                    ok = await self.download_file(url_list[0], path, parent_span,
                                                  fallback_urls=url_list[1:])
                    if ok:
                        files_written += 1
                    else:
                        success = False
                    await asyncio.sleep(0.3)
        else:
            video_url = self._get_video_url(aweme)
            if video_url:
                fallbacks = self._get_video_fallbacks(aweme)
                path = save_dir / f"{folder_name}.mp4"
                ok = await self.download_file(video_url, path, parent_span,
                                              fallback_urls=fallbacks)
                if ok:
                    files_written += 1
                else:
                    success = False

            if self._download_music:
                music_url = self._get_music_url(aweme)
                if music_url:
                    path = save_dir / f"{folder_name}_music.mp3"
                    if await self.download_file(music_url, path, parent_span):
                        files_written += 1

        if self._download_cover:
            cover_url = self._get_cover_url(aweme)
            if cover_url:
                path = save_dir / f"{folder_name}_cover.jpg"
                if await self.download_file(cover_url, path, parent_span):
                    files_written += 1

        if self._download_json:
            json_path = save_dir / f"{folder_name}_data.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(aweme, f, ensure_ascii=False, indent=2)
            files_written += 1

        task.file_paths = [str(save_dir)]
        return DownloadResult(
            task=task, success=success,
            files_written=files_written, elapsed=time.time() - t0,
        )

    async def download_file(self, url: str, path: Path,
                            parent_span: TraceSpan,
                            fallback_urls: list[str] | None = None) -> bool:
        if path.exists() and path.stat().st_size > 0:
            self._tracer.add_event(parent_span, "file_skip", path=str(path.name))
            return True

        await self._ensure_session()
        async with self._semaphore:
            all_urls = [url] + (fallback_urls or [])
            for i, u in enumerate(all_urls):
                try:
                    u = u.replace("playwm", "play")
                    async with self._session.get(u) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                            self._log.debug("文件已下载", file=path.name,
                                            size_kb=len(data) // 1024)
                            return True
                        elif resp.status == 403 and i < len(all_urls) - 1:
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if i < len(all_urls) - 1:
                        continue

        self._log.warn("文件下载失败", file=path.name, urls_tried=len(all_urls))
        return False

    def _get_video_url(self, aweme: dict) -> str | None:
        for key in ("play_addr_h264", "play_addr"):
            addr = aweme.get("video", {}).get(key)
            if addr and addr.get("url_list"):
                return addr["url_list"][0].replace("playwm", "play")
        return None

    def _get_video_fallbacks(self, aweme: dict) -> list[str]:
        urls = []
        for key in ("play_addr", "play_addr_h264", "download_addr"):
            addr = aweme.get("video", {}).get(key)
            if addr and addr.get("url_list"):
                urls.extend(addr["url_list"])
        return urls

    def _get_music_url(self, aweme: dict) -> str | None:
        music = aweme.get("music", {})
        play_url = music.get("play_url", {})
        if isinstance(play_url, dict):
            url_list = play_url.get("url_list", [])
            return url_list[0] if url_list else None
        return play_url if isinstance(play_url, str) else None

    def _get_cover_url(self, aweme: dict) -> str | None:
        cover = aweme.get("video", {}).get("cover", {})
        url_list = cover.get("url_list", [])
        return url_list[0] if url_list else None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
