import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

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

    async def download_media(
        self, aweme: dict, parent_span: TraceSpan,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> DownloadResult:
        task = DownloadTask(
            task_id=aweme.get("aweme_id", "unknown"),
            trace_id=parent_span.trace_id,
            url="",
            content_type="image" if aweme.get("images") else "video",
        )
        t0 = time.time()
        save_dir = self._build_save_dir(aweme)
        files_written = 0
        total_bytes = 0
        success = True
        folder_name = save_dir.name

        if aweme.get("images"):
            images = aweme.get("images", [])
            for i, img in enumerate(images):
                best_url, fallbacks, ext = self._get_best_image_url(img)
                if best_url:
                    path = save_dir / f"image_{i+1}.{ext}"
                    ok, nbytes = await self.download_file(
                        best_url, path, parent_span,
                        fallback_urls=fallbacks, on_progress=on_progress,
                    )
                    if ok:
                        files_written += 1
                        total_bytes += nbytes
                    else:
                        success = False
                    await asyncio.sleep(0.3)
        else:
            video_url = self._get_video_url(aweme)
            if video_url:
                fallbacks = self._get_video_fallbacks(aweme)
                path = save_dir / f"{folder_name}.mp4"
                ok, nbytes = await self.download_file(
                    video_url, path, parent_span,
                    fallback_urls=fallbacks, on_progress=on_progress,
                )
                if ok:
                    files_written += 1
                    total_bytes += nbytes
                else:
                    success = False

            if self._download_music:
                music_url = self._get_music_url(aweme)
                if music_url:
                    path = save_dir / f"{folder_name}_music.mp3"
                    ok, nbytes = await self.download_file(
                        music_url, path, parent_span, on_progress=on_progress,
                    )
                    if ok:
                        files_written += 1
                        total_bytes += nbytes

        if self._download_cover:
            cover_url, cover_fallbacks = self._get_cover_urls(aweme)
            if cover_url:
                path = save_dir / f"{folder_name}_cover.jpg"
                ok, nbytes = await self.download_file(
                    cover_url, path, parent_span,
                    fallback_urls=cover_fallbacks, on_progress=on_progress,
                )
                if ok:
                    files_written += 1
                    total_bytes += nbytes

        if self._download_json:
            json_path = save_dir / f"{folder_name}_data.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(aweme, f, ensure_ascii=False, indent=2)
            files_written += 1

        task.file_paths = [str(save_dir)]
        return DownloadResult(
            task=task, success=success,
            files_written=files_written, elapsed=time.time() - t0,
            bytes_downloaded=total_bytes,
        )

    async def download_file(
        self, url: str, path: Path, parent_span: TraceSpan,
        fallback_urls: list[str] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[bool, int]:
        if path.exists() and path.stat().st_size > 0:
            self._tracer.add_event(parent_span, "file_skip", path=str(path.name))
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
                        elif resp.status == 403 and i < len(all_urls) - 1:
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if i < len(all_urls) - 1:
                        continue

        self._log.warn("文件下载失败", file=path.name, urls_tried=len(all_urls))
        return (False, 0)

    def _get_best_image_url(self, img: dict) -> tuple[str | None, list[str], str]:
        dl_urls = img.get("download_url_list", [])
        url_list = img.get("url_list", [])

        if dl_urls:
            best = dl_urls[0]
            fallbacks = dl_urls[1:] + url_list
        elif url_list:
            best = url_list[0]
            fallbacks = url_list[1:]
        else:
            return (None, [], "jpg")

        ext = "webp" if ".webp" in best.split("?")[0] else "jpg"
        return (best, fallbacks, ext)

    def _get_video_url(self, aweme: dict) -> str | None:
        video = aweme.get("video", {})
        bit_rate = video.get("bit_rate", [])
        if bit_rate:
            best = max(bit_rate, key=lambda b: b.get("bit_rate", 0))
            pa = best.get("play_addr", {})
            urls = pa.get("url_list", [])
            if urls:
                return urls[0].replace("playwm", "play")

        for key in ("play_addr_h264", "play_addr"):
            addr = video.get(key)
            if addr and addr.get("url_list"):
                url = addr["url_list"][0].replace("playwm", "play")
                url = url.replace("720p", "1080p")
                return url
        return None

    def _get_video_fallbacks(self, aweme: dict) -> list[str]:
        video = aweme.get("video", {})
        urls = []
        bit_rate = video.get("bit_rate", [])
        if bit_rate:
            sorted_br = sorted(bit_rate, key=lambda b: b.get("bit_rate", 0), reverse=True)
            for br in sorted_br:
                pa = br.get("play_addr", {})
                urls.extend(pa.get("url_list", []))

        for key in ("play_addr_h264", "play_addr", "download_addr"):
            addr = video.get(key)
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

    def _get_cover_urls(self, aweme: dict) -> tuple[str | None, list[str]]:
        all_urls = []
        for key in ("origin_cover", "cover", "dynamic_cover"):
            src = aweme.get("video", {}).get(key, {})
            if src and src.get("url_list"):
                all_urls.extend(src["url_list"])
        if not all_urls:
            return (None, [])
        return (all_urls[0], all_urls[1:])

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
