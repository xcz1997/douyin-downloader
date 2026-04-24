# core/platforms/douyin.py
"""Douyin platform plugin: URL matching and MediaItem adaptation."""

from __future__ import annotations

import re

import aiohttp

from core.platform import ContentRef, ListPage, MediaAsset, MediaItem


_SHORT_URL_RE = re.compile(r"^https?://v\.douyin\.com/\w+")
_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")
_NOTE_RE = re.compile(r"douyin\.com/note/(\d+)")
_USER_RE = re.compile(r"(?:sec_uid=|/user/)(MS4wLjAB[\w\-]+)")
_MIX_RE = re.compile(r"mix_id=(\d+)|/collection/(\d+)")
_MUSIC_RE = re.compile(r"/music/(\d+)")


class DouyinPlatform:
    """URL recognition for Douyin (抖音).

    Precedence: short > note(image) > video > mix > music > user.
    """

    name = "douyin"

    def match_url(self, url: str) -> ContentRef | None:
        if _SHORT_URL_RE.match(url):
            return ContentRef(
                platform="douyin",
                content_type="short",
                resource_id=None,
                resolved_url=url,
            )

        m = _NOTE_RE.search(url)
        if m:
            return ContentRef(
                platform="douyin",
                content_type="image",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _VIDEO_RE.search(url)
        if m:
            return ContentRef(
                platform="douyin",
                content_type="video",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _MIX_RE.search(url)
        if m:
            return ContentRef(
                platform="douyin",
                content_type="mix",
                resource_id=m.group(1) or m.group(2),
                resolved_url=url,
            )

        m = _MUSIC_RE.search(url)
        if m:
            return ContentRef(
                platform="douyin",
                content_type="music",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _USER_RE.search(url)
        if m:
            return ContentRef(
                platform="douyin",
                content_type="user",
                resource_id=m.group(1),
                resolved_url=url,
            )

        return None


def aweme_to_media_item(aweme: dict) -> MediaItem:
    """Convert a Douyin aweme dict into the standardized MediaItem form.

    Args:
        aweme: A Douyin post dict as produced by ``DouyinAPIClient`` /
            ``apiproxy.douyin.result.Result.dataConvert``.

    Returns:
        MediaItem with ``assets`` populated for video OR images, plus
        optional cover and music tracks.
    """
    assets: list[MediaAsset] = []

    if aweme.get("images"):
        for img in aweme.get("images", []):
            asset = _image_to_asset(img)
            if asset is not None:
                assets.append(asset)
    else:
        video_asset = _video_to_asset(aweme.get("video", {}))
        if video_asset is not None:
            assets.append(video_asset)
        music_asset = _music_to_asset(aweme.get("music", {}))
        if music_asset is not None:
            assets.append(music_asset)

    cover_asset = _cover_to_asset(aweme.get("video", {}))
    if cover_asset is not None:
        assets.append(cover_asset)

    return MediaItem(
        platform="douyin",
        id=str(aweme.get("aweme_id", "")),
        author=aweme.get("author", {}).get("nickname", "unknown"),
        desc=aweme.get("desc") or "",
        create_time=float(aweme.get("create_time") or 0.0),
        assets=assets,
        raw=aweme,
    )


def _video_to_asset(video: dict) -> MediaAsset | None:
    """Pick the best-bitrate URL and collect fallbacks."""
    primary: str | None = None
    fallbacks: list[str] = []

    bit_rate = video.get("bit_rate", [])
    if bit_rate:
        sorted_br = sorted(
            bit_rate, key=lambda b: b.get("bit_rate", 0), reverse=True,
        )
        for br in sorted_br:
            for u in br.get("play_addr", {}).get("url_list", []):
                u = u.replace("playwm", "play").replace("720p", "1080p")
                if primary is None:
                    primary = u
                elif u not in fallbacks:
                    fallbacks.append(u)

    for key in ("play_addr_h264", "play_addr", "download_addr"):
        addr = video.get(key)
        if not addr:
            continue
        for u in addr.get("url_list", []):
            u = u.replace("playwm", "play").replace("720p", "1080p")
            if primary is None:
                primary = u
            elif u not in fallbacks:
                fallbacks.append(u)

    if primary is None:
        return None
    return MediaAsset(
        url=primary, kind="video_main", ext="mp4",
        fallback_urls=fallbacks,
    )


def _image_to_asset(img: dict) -> MediaAsset | None:
    dl_urls = img.get("download_url_list", [])
    url_list = img.get("url_list", [])
    if dl_urls:
        best = dl_urls[0]
        fallbacks = dl_urls[1:] + url_list
    elif url_list:
        best = url_list[0]
        fallbacks = url_list[1:]
    else:
        return None
    ext = "webp" if ".webp" in best.split("?")[0] else "jpg"
    return MediaAsset(
        url=best, kind="image", ext=ext, fallback_urls=list(fallbacks),
    )


def _music_to_asset(music: dict) -> MediaAsset | None:
    play = music.get("play_url")
    if isinstance(play, dict):
        urls = play.get("url_list", [])
        if not urls:
            return None
        return MediaAsset(
            url=urls[0], kind="music", ext="mp3",
            fallback_urls=list(urls[1:]),
        )
    if isinstance(play, str) and play:
        return MediaAsset(url=play, kind="music", ext="mp3")
    return None


def _cover_to_asset(video: dict) -> MediaAsset | None:
    primary: str | None = None
    fallbacks: list[str] = []
    for key in ("origin_cover", "cover", "dynamic_cover"):
        src = video.get(key) or {}
        for u in src.get("url_list", []):
            if primary is None:
                primary = u
            elif u not in fallbacks:
                fallbacks.append(u)
    if primary is None:
        return None
    return MediaAsset(
        url=primary, kind="cover", ext="jpg", fallback_urls=fallbacks,
    )


class DouyinPlatformClient:
    """Adapter wrapping ``DouyinAPIClient`` to the PlatformClient protocol.

    The underlying ``DouyinAPIClient`` returns raw dicts; this class converts
    them to MediaItem / ListPage so DownloadPipeline stays platform-agnostic.

    Args:
        api: An initialized ``DouyinAPIClient``.
        resolve_func: Optional coroutine to resolve ``v.douyin.com`` short
            URLs. Defaults to the built-in single-redirect resolver.
    """

    def __init__(self, api, resolve_func=None) -> None:
        self._api = api
        self._resolve_func = resolve_func or _default_resolve_short_url

    async def resolve_short_url(self, url: str) -> str:
        return await self._resolve_func(url)

    async def fetch_single(self, ref: ContentRef, span) -> MediaItem:
        aweme = await self._api.get_video_info(ref.resource_id, span)
        return aweme_to_media_item(aweme)

    async def fetch_list(
        self, ref: ContentRef, cursor, span,
    ) -> ListPage:
        if ref.content_type == "user":
            mode = ref.extra.get("mode", "post")
            if mode == "like":
                page = await self._api.get_user_likes(
                    ref.resource_id, cursor or 0, span,
                )
            else:
                page = await self._api.get_user_posts(
                    ref.resource_id, cursor or 0, span,
                )
            next_cursor = page.get("max_cursor", 0)
        elif ref.content_type == "mix":
            page = await self._api.get_mix_items(
                ref.resource_id, cursor or 0, span,
            )
            next_cursor = page.get("cursor", 0)
        elif ref.content_type == "music":
            page = await self._api.get_music_items(
                ref.resource_id, cursor or 0, span,
            )
            next_cursor = page.get("cursor", 0)
        else:
            raise ValueError(
                f"fetch_list not supported for content_type={ref.content_type}"
            )

        items = [
            aweme_to_media_item(a) for a in page.get("aweme_list", [])
        ]
        has_more = bool(page.get("has_more"))
        return ListPage(
            items=items, next_cursor=next_cursor, has_more=has_more,
        )


async def _default_resolve_short_url(url: str) -> str:
    """Follow one redirect from a v.douyin.com short URL."""
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
