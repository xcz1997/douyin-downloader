# core/platforms/xhs.py
"""XHS (小红书) platform plugin: URL recognition.

Stage A implementation: URL matching only. API client, signer, and
MediaItem conversion arrive in Plan 3.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.platform import ContentRef


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
