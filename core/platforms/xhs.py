# core/platforms/xhs.py
"""XHS (小红书) platform plugin: URL recognition.

Stage A implementation: URL matching only. API client, signer, and
MediaItem conversion arrive in Plan 3.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.platform import ContentRef, ListPage, MediaAsset, MediaItem


_SHORT_URL_RE = re.compile(r"^https?://xhslink\.com/[\w/]+")
_EXPLORE_RE = re.compile(r"xiaohongshu\.com/explore/([0-9a-fA-F]+)")
_DISCOVERY_RE = re.compile(r"xiaohongshu\.com/discovery/item/([0-9a-fA-F]+)")
_USER_RE = re.compile(r"xiaohongshu\.com/user/profile/([0-9a-fA-F]+)")
_BOARD_RE = re.compile(r"xiaohongshu\.com/board/([0-9a-fA-F]+)")
_TOPIC_RE = re.compile(r"xiaohongshu\.com/page/topics/([0-9a-fA-F]+)")
_SEARCH_RE = re.compile(r"xiaohongshu\.com/search_result")


class XHSPlatform:
    """URL recognition for Xiaohongshu (小红书).

    Precedence: short > explore > discovery > user > board > search > topic.
    """

    name = "xhs"

    def match_url(self, url: str) -> ContentRef | None:
        if _SHORT_URL_RE.match(url):
            return ContentRef(
                platform="xhs",
                content_type="short",
                resource_id=None,
                resolved_url=url,
            )

        m = _EXPLORE_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _DISCOVERY_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _USER_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="user",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _BOARD_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="collection",
                resource_id=m.group(1),
                resolved_url=url,
            )

        if _SEARCH_RE.search(url):
            keyword = ""
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            kw_list = params.get("keyword", [])
            if kw_list:
                keyword = kw_list[0]
            return ContentRef(
                platform="xhs",
                content_type="search",
                resource_id=keyword or None,
                resolved_url=url,
                extra={"keyword": keyword} if keyword else {},
            )

        m = _TOPIC_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="topic",
                resource_id=m.group(1),
                resolved_url=url,
            )

        return None


def note_to_media_item(note: dict) -> MediaItem:
    """Convert an XHS SSR note dict into a MediaItem.

    Accepts the ``note`` value from
    ``window.__INITIAL_STATE__.note.noteDetailMap[<note_id>]`` — i.e.
    the rich note object, NOT the outer
    ``{comments, currentTime, note, widgets}`` wrapper.

    Handles ``type="video"`` and ``type="normal"`` notes plus live
    photos (动图 — imageList entries whose ``stream.h264[0].masterUrl``
    is set).

    XHS wire format is camelCase (imageList, urlDefault, masterUrl,
    backupUrls, originVideoKey, noteId) — snake_case variants are NOT
    accepted.

    Raises:
        RuntimeError: For ``type="video"`` notes where neither
            ``video.consumer.originVideoKey`` nor
            ``video.media.stream.{h264,h265}`` yields a URL — this
            is the failure mode of JoeanAmier/XHS-Downloader#324.
            Surfaced at task level by the pipeline; does NOT kill
            the batch.
    """
    note_type = note.get("type", "normal")
    assets: list[MediaAsset] = []

    if note_type == "video":
        video_asset = _video_to_asset(note.get("video") or {})
        if video_asset is None:
            note_id = note.get("noteId") or note.get("id") or "?"
            raise RuntimeError(
                f"XHS video URL extraction failed for note {note_id}: "
                f"both originVideoKey fast-path "
                f"(video.consumer.originVideoKey) and stream fallback "
                f"(video.media.stream.h264/h265) yielded nothing. "
                f"Likely API schema drift — inspect note['video'] shape."
            )
        assets.append(video_asset)
        cover_asset = _cover_to_asset(note)
        if cover_asset is not None:
            assets.append(cover_asset)
    else:
        for img in note.get("imageList") or []:
            img_asset = _image_to_asset(img)
            if img_asset is not None:
                assets.append(img_asset)
            live_asset = _live_to_asset(img)
            if live_asset is not None:
                assets.append(live_asset)

    user = note.get("user") or {}
    author = (
        user.get("nickname")
        or user.get("nickName")
        or user.get("name")
        or "unknown"
    )

    # XHS 'time' / 'lastUpdateTime' are Unix milliseconds.
    raw_time = note.get("time") or note.get("lastUpdateTime") or 0
    try:
        raw_time = int(raw_time)
    except (TypeError, ValueError):
        raw_time = 0
    create_time = raw_time / 1000.0 if raw_time > 1e11 else float(raw_time)

    desc = note.get("desc") or note.get("title") or ""

    note_id = note.get("noteId") or note.get("id") or ""

    return MediaItem(
        platform="xhs",
        id=str(note_id),
        author=str(author),
        desc=str(desc),
        create_time=create_time,
        assets=assets,
        raw=note,
    )


def _video_to_asset(video: dict) -> MediaAsset | None:
    """Pick the highest-quality video URL from note.video.

    Preference (matches upstream XHS-Downloader deal_video_link):
      1. ``video.consumer.originVideoKey`` → unwatermarked original on
         sns-video-bd.xhscdn.com. Only exists for some notes.
      2. ``video.media.stream.{h264,h265,av1}[]`` sorted by height desc;
         pick ``backupUrls[0]`` if present else ``masterUrl``.

    Returns None when both paths yield nothing. Caller turns that into
    a RuntimeError with schema-drift diagnostics.
    """
    consumer = video.get("consumer") or {}
    origin_key = consumer.get("originVideoKey")
    if origin_key:
        primary = f"https://sns-video-bd.xhscdn.com/{origin_key}"
        return MediaAsset(
            url=primary, kind="video_main", ext="mp4",
            fallback_urls=_stream_backup_urls(video),
        )

    entries = _collect_stream_entries(video)
    if not entries:
        return None
    entries_sorted = sorted(entries, key=lambda e: e.get("height") or 0)
    best = entries_sorted[-1]
    primary = (best.get("backupUrls") or [None])[0] or best.get("masterUrl")
    if not primary:
        return None

    fallbacks: list[str] = []
    for u in best.get("backupUrls") or []:
        if u and u != primary and u not in fallbacks:
            fallbacks.append(u)
    master = best.get("masterUrl")
    if master and master != primary and master not in fallbacks:
        fallbacks.append(master)
    for e in entries_sorted[:-1]:
        for u in e.get("backupUrls") or []:
            if u and u not in fallbacks and u != primary:
                fallbacks.append(u)
        m = e.get("masterUrl")
        if m and m not in fallbacks and m != primary:
            fallbacks.append(m)

    return MediaAsset(
        url=primary, kind="video_main", ext="mp4",
        fallback_urls=fallbacks,
    )


def _collect_stream_entries(video: dict) -> list[dict]:
    """Flatten video.media.stream.{h264,h265,av1}[] into one list."""
    stream = (video.get("media") or {}).get("stream") or {}
    out: list[dict] = []
    for codec in ("h264", "h265", "av1"):
        out.extend(stream.get(codec) or [])
    return out


def _stream_backup_urls(video: dict) -> list[str]:
    """Gather every stream URL — used as fallback for originVideoKey primary."""
    out: list[str] = []
    for e in _collect_stream_entries(video):
        for u in e.get("backupUrls") or []:
            if u and u not in out:
                out.append(u)
        master = e.get("masterUrl")
        if master and master not in out:
            out.append(master)
    return out


def _image_to_asset(img: dict) -> MediaAsset | None:
    """Extract the static image URL, regenerating to unwatermarked CDN."""
    raw = img.get("urlDefault") or img.get("url") or ""
    if not raw:
        return None

    token = _extract_image_token(raw)
    if token:
        primary = f"https://sns-img-bd.xhscdn.com/{token}"
    else:
        primary = raw.split("!")[0]

    fallbacks: list[str] = []
    for key in ("url", "urlPre"):
        u = img.get(key)
        if u:
            cleaned = u.split("!")[0]
            if cleaned != primary and cleaned not in fallbacks:
                fallbacks.append(cleaned)
    raw_clean = raw.split("!")[0]
    if raw_clean != primary and raw_clean not in fallbacks:
        fallbacks.append(raw_clean)

    ext = "jpg"
    for sniff in (primary, raw):
        low = sniff.split("?")[0].lower()
        if low.endswith(".webp"):
            ext = "webp"
            break
        if low.endswith(".png"):
            ext = "png"
            break

    return MediaAsset(
        url=primary, kind="image", ext=ext, fallback_urls=fallbacks,
    )


def _extract_image_token(url: str) -> str | None:
    """Return the CDN token from an XHS image URL, else None."""
    parts = url.split("/")
    if len(parts) < 6:
        return None
    tail = "/".join(parts[5:])
    token = tail.split("!")[0]
    return token or None


def _live_to_asset(img: dict) -> MediaAsset | None:
    """If this imageList entry is a live photo (动图), emit the video."""
    stream = img.get("stream") or {}
    entries = stream.get("h264") or []
    if not entries:
        return None
    first = entries[0]
    master = first.get("masterUrl")
    if not master:
        return None
    fallbacks: list[str] = []
    for u in first.get("backupUrls") or []:
        if u and u != master and u not in fallbacks:
            fallbacks.append(u)
    return MediaAsset(
        url=master, kind="video_live", ext="mp4",
        fallback_urls=fallbacks,
    )


def _cover_to_asset(note: dict) -> MediaAsset | None:
    """Pick the cover URL for a video note from imageList[0]."""
    images = note.get("imageList") or []
    if not images:
        return None
    first = images[0]
    raw = first.get("urlDefault") or first.get("url")
    if not raw:
        return None

    token = _extract_image_token(raw)
    primary = (
        f"https://sns-img-bd.xhscdn.com/{token}"
        if token else raw.split("!")[0]
    )

    fallbacks: list[str] = []
    for key in ("url", "urlPre"):
        u = first.get(key)
        if u:
            cleaned = u.split("!")[0]
            if cleaned != primary and cleaned not in fallbacks:
                fallbacks.append(cleaned)

    return MediaAsset(
        url=primary, kind="cover", ext="jpg", fallback_urls=fallbacks,
    )


class XHSPlatformClient:
    """Stage A placeholder. Real implementation lands in Plan 3.

    Exists so DownloadPipeline can detect "XHS URL matched but downloader
    not yet wired" and report a clear error instead of silently skipping.
    """

    async def resolve_short_url(self, url: str) -> str:
        raise NotImplementedError(
            "XHS short URL resolution not yet implemented "
            "(pending Plan 3 Stage B)"
        )

    async def fetch_single(self, ref, span):
        raise NotImplementedError(
            "XHS single-note fetch not yet implemented "
            "(pending Plan 3 Stage B)"
        )

    async def fetch_list(self, ref, cursor, span):
        raise NotImplementedError(
            "XHS list fetch not yet implemented "
            "(pending Plan 3 Stage B)"
        )
