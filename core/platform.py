# core/platform.py
"""Platform abstraction for multi-source downloaders.

Defines the data model and protocols that let DownloadPipeline route
content to the right platform plugin (douyin, xhs, ...) without knowing
any platform-specific details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MediaAsset:
    """A single downloadable file in a MediaItem.

    Attributes:
        url: Primary download URL.
        kind: Asset classification — one of ``"video_main"``, ``"video_live"``,
            ``"image"``, ``"cover"``, ``"music"``.
        ext: File extension without leading dot (``"mp4"``, ``"jpg"``, etc.).
        fallback_urls: Alternate URLs tried on 403 or network failure.
        suggested_filename: Optional stem; the engine may override based on
            ``kind`` and the containing item's description.
    """

    url: str
    kind: str
    ext: str
    fallback_urls: list[str] = field(default_factory=list)
    suggested_filename: str | None = None


@dataclass
class MediaItem:
    """A single post normalized across platforms.

    Attributes:
        platform: Short platform identifier (e.g. ``"douyin"``, ``"xhs"``).
        id: Platform-scoped unique ID (aweme_id / note_id).
        author: Author display name, used for directory naming.
        desc: Post description / caption.
        create_time: Unix timestamp seconds.
        assets: Downloadable files (videos / images / cover / music).
        raw: Original API response dict, persisted as ``_data.json``.
    """

    platform: str
    id: str
    author: str
    desc: str
    create_time: float
    assets: list[MediaAsset]
    raw: dict


@dataclass
class ContentRef:
    """Reference to a piece of content parsed from a user-provided URL.

    Attributes:
        platform: Short platform identifier.
        content_type: One of ``"single"``, ``"user"``, ``"collection"``,
            ``"music"``, ``"search"``, ``"topic"``.
        resource_id: The primary ID (aweme_id, sec_uid, mix_id, note_id,
            user_id, keyword, ...). ``None`` when not applicable.
        resolved_url: Fully resolved (non-short) URL.
        extra: Platform-specific bag (search params, sort order, etc.).
    """

    platform: str
    content_type: str
    resource_id: str | None
    resolved_url: str
    extra: dict = field(default_factory=dict)


@dataclass
class ListPage:
    """One page of results from a paginated list fetch.

    Attributes:
        items: MediaItem instances on this page.
        next_cursor: Opaque pagination token (int or str or None).
        has_more: Whether another page exists.
    """

    items: list[MediaItem]
    next_cursor: str | int | None
    has_more: bool
