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
        content_type: Platform-specific classification. For Douyin:
            ``"short"`` (unresolved v.douyin.com link), ``"video"``,
            ``"image"`` (note-style image posts), ``"user"``, ``"mix"``
            (playlist/collection), ``"music"`` (posts using a sound).
            For XHS (future): ``"single"``, ``"user"``, ``"collection"``,
            ``"search"``, ``"topic"``. Pipeline routes ``"single"`` /
            ``"video"`` / ``"image"`` to the single-item handler; all
            other types route to the paginated-list handler.
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


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class Platform(Protocol):
    """URL-matching and content-type classification for a single source.

    Implementations register with ``PlatformRegistry`` and are queried for
    every input URL. The first match wins.
    """

    name: str

    def match_url(self, url: str) -> ContentRef | None:
        """Return a ContentRef for URLs this platform handles, else None."""
        ...


class PlatformClient(Protocol):
    """Asynchronous content fetcher for a platform."""

    async def resolve_short_url(self, url: str) -> str:
        """Resolve a short URL to its canonical form (returns input if N/A)."""
        ...

    async def fetch_single(self, ref: "ContentRef", span) -> "MediaItem":
        """Fetch a single post (video/image note) and return a MediaItem."""
        ...

    async def fetch_list(
        self, ref: "ContentRef", cursor: str | int | None, span,
        *, limit: int = 0,
    ) -> "ListPage":
        """Fetch one page of a paginated list (user posts, collection, ...).

        ``limit`` is a soft hint: if > 0, the implementation MAY early-stop
        once it has accumulated at least ``limit`` items. Implementations
        that fetch one cheap page at a time (e.g. Douyin's cursor-paginated
        APIs) can ignore it — the pipeline will truncate. Implementations
        that do expensive per-item enrichment before returning (e.g. XHS,
        which hydrates every note via a separate browser navigation) MUST
        honor the hint to avoid wasting work.
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PlatformRegistry:
    """Registry of platform plugins keyed by ``Platform.name``.

    Queried by DownloadPipeline for every input URL. First registered
    platform whose ``match_url`` returns non-None wins.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[Platform, PlatformClient]] = []
        self._by_name: dict[str, PlatformClient] = {}

    def register(self, platform: Platform, client: PlatformClient) -> None:
        """Register a platform plugin.

        Raises:
            ValueError: A platform with the same name is already registered.
        """
        if platform.name in self._by_name:
            raise ValueError(f"platform {platform.name!r} already registered")
        self._entries.append((platform, client))
        self._by_name[platform.name] = client

    def match(
        self, url: str,
    ) -> tuple[Platform, PlatformClient, ContentRef] | None:
        """Find the platform handling ``url``, return (platform, client, ref).

        Returns ``None`` if no registered platform matches.
        """
        for platform, client in self._entries:
            ref = platform.match_url(url)
            if ref is not None:
                return platform, client, ref
        return None

    def get_client(self, platform_name: str) -> PlatformClient | None:
        """Return the client for a registered platform name, or None."""
        return self._by_name.get(platform_name)
