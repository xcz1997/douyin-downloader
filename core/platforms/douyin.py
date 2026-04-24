# core/platforms/douyin.py
"""Douyin platform plugin: URL matching and MediaItem adaptation."""

from __future__ import annotations

import re

from core.platform import ContentRef


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
