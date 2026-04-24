# XHS 整合 Plan 1：平台抽象 + 抖音适配

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 pipeline 从"抖音专用编排器"重构为"平台无关编排器"，抖音作为第一个插件注册进去，为后续 XHS 接入打地基。对用户可见行为零变化。

**Architecture:** 新增 `core/platform.py` 定义 `Platform` / `PlatformClient` Protocol 和 `MediaItem` / `MediaAsset` / `ContentRef` / `ListPage` 数据模型；新增 `core/platforms/douyin.py` 把现有 `DouyinAPIClient` 包成 `DouyinPlatformClient`；`DownloadEngine` 改为消费 `MediaItem`；`DownloadPipeline` 通过 `PlatformRegistry` 路由。

**Tech Stack:** Python 3.11+, aiohttp, dataclasses, typing.Protocol

**Spec:** `docs/specs/2026-04-24-xhs-integration-design.md`

**这份 Plan 覆盖 Spec 的**：Phase 1（平台抽象骨架）+ Phase 2（抖音适配层）

**后续 Plan**：Plan 2（目录分平台 + 数据库）、Plan 3（XHS 接入）—— 在本 Plan 执行完成后继续。

---

## Phase 1: 平台抽象骨架

### Task 1: 数据模型（MediaAsset / MediaItem / ContentRef / ListPage）

**Files:**
- Create: `core/platform.py`
- Create: `tests/test_platform_models.py`

- [ ] **Step 1: 写数据模型测试**

```python
# tests/test_platform_models.py
from core.platform import MediaAsset, MediaItem, ContentRef, ListPage


def test_media_asset_defaults():
    a = MediaAsset(url="https://x/a.mp4", kind="video_main", ext="mp4")
    assert a.url == "https://x/a.mp4"
    assert a.fallback_urls == []
    assert a.suggested_filename is None


def test_media_asset_with_fallbacks():
    a = MediaAsset(
        url="https://x/a.mp4",
        kind="video_main",
        ext="mp4",
        fallback_urls=["https://y/a.mp4"],
        suggested_filename="my_video",
    )
    assert a.fallback_urls == ["https://y/a.mp4"]
    assert a.suggested_filename == "my_video"


def test_media_item_minimal():
    item = MediaItem(
        platform="douyin",
        id="123",
        author="alice",
        desc="test",
        create_time=1700000000.0,
        assets=[],
        raw={},
    )
    assert item.platform == "douyin"
    assert item.assets == []


def test_content_ref():
    ref = ContentRef(
        platform="xhs",
        content_type="single",
        resource_id="abc",
        resolved_url="https://www.xiaohongshu.com/explore/abc",
    )
    assert ref.platform == "xhs"
    assert ref.extra == {}


def test_list_page_has_more():
    p = ListPage(items=[], next_cursor="tok123", has_more=True)
    assert p.has_more is True
    assert p.next_cursor == "tok123"


def test_list_page_end():
    p = ListPage(items=[], next_cursor=None, has_more=False)
    assert p.has_more is False
    assert p.next_cursor is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_platform_models.py -v`
Expected: ModuleNotFoundError / ImportError — `core.platform` 不存在

- [ ] **Step 3: 实现数据模型**

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_platform_models.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add core/platform.py tests/test_platform_models.py
git commit -m "feat(platform): add MediaItem/MediaAsset/ContentRef/ListPage data models"
```

---

### Task 2: Platform / PlatformClient Protocol

**Files:**
- Modify: `core/platform.py`（追加 Protocol 定义）
- Create: `tests/test_platform_protocol.py`

- [ ] **Step 1: 写 Protocol 测试**

```python
# tests/test_platform_protocol.py
"""Verify Platform / PlatformClient are structural (Protocol) types."""

from core.platform import (
    ContentRef, MediaItem, ListPage, Platform, PlatformClient,
)


class FakePlatform:
    name = "fake"

    def match_url(self, url):
        if "fake.com" in url:
            return ContentRef(
                platform="fake",
                content_type="single",
                resource_id="1",
                resolved_url=url,
            )
        return None


class FakeClient:
    async def resolve_short_url(self, url):
        return url

    async def fetch_single(self, ref, span):
        return MediaItem(
            platform="fake", id="1", author="", desc="",
            create_time=0.0, assets=[], raw={},
        )

    async def fetch_list(self, ref, cursor, span):
        return ListPage(items=[], next_cursor=None, has_more=False)


def test_fake_platform_conforms():
    p: Platform = FakePlatform()
    assert p.name == "fake"
    ref = p.match_url("https://fake.com/x")
    assert ref is not None and ref.content_type == "single"
    assert p.match_url("https://other.com/x") is None


def test_fake_client_conforms():
    c: PlatformClient = FakeClient()
    assert c is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_platform_protocol.py -v`
Expected: ImportError — `Platform`/`PlatformClient` 尚未导出

- [ ] **Step 3: 追加 Protocol 定义到 core/platform.py**

在 `core/platform.py` 末尾追加：

```python
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
    ) -> "ListPage":
        """Fetch one page of a paginated list (user posts, collection, ...)."""
        ...
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_platform_protocol.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/platform.py tests/test_platform_protocol.py
git commit -m "feat(platform): add Platform/PlatformClient Protocols"
```

---

### Task 3: PlatformRegistry

**Files:**
- Modify: `core/platform.py`（追加 Registry）
- Create: `tests/test_platform_registry.py`

- [ ] **Step 1: 写 Registry 测试**

```python
# tests/test_platform_registry.py
import pytest

from core.platform import ContentRef, MediaItem, ListPage, PlatformRegistry


class DouyinFake:
    name = "douyin"

    def match_url(self, url):
        if "douyin.com" in url:
            return ContentRef(
                platform="douyin", content_type="video",
                resource_id="1", resolved_url=url,
            )
        return None


class XhsFake:
    name = "xhs"

    def match_url(self, url):
        if "xiaohongshu.com" in url:
            return ContentRef(
                platform="xhs", content_type="single",
                resource_id="a", resolved_url=url,
            )
        return None


class ClientFake:
    async def resolve_short_url(self, url): return url
    async def fetch_single(self, ref, span): return None
    async def fetch_list(self, ref, cursor, span): return None


def test_registry_match_first_platform():
    r = PlatformRegistry()
    r.register(DouyinFake(), ClientFake())
    r.register(XhsFake(), ClientFake())

    match = r.match("https://www.douyin.com/video/123")
    assert match is not None
    platform, client, ref = match
    assert platform.name == "douyin"
    assert ref.content_type == "video"


def test_registry_match_second_platform():
    r = PlatformRegistry()
    r.register(DouyinFake(), ClientFake())
    r.register(XhsFake(), ClientFake())

    match = r.match("https://www.xiaohongshu.com/explore/a")
    assert match is not None
    platform, _, ref = match
    assert platform.name == "xhs"


def test_registry_no_match():
    r = PlatformRegistry()
    r.register(DouyinFake(), ClientFake())
    assert r.match("https://unknown.com/foo") is None


def test_registry_duplicate_raises():
    r = PlatformRegistry()
    r.register(DouyinFake(), ClientFake())
    with pytest.raises(ValueError, match="already registered"):
        r.register(DouyinFake(), ClientFake())


def test_registry_get_client_by_name():
    r = PlatformRegistry()
    c = ClientFake()
    r.register(DouyinFake(), c)
    assert r.get_client("douyin") is c
    assert r.get_client("missing") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_platform_registry.py -v`
Expected: ImportError — `PlatformRegistry` 不存在

- [ ] **Step 3: 追加 PlatformRegistry 到 core/platform.py**

在 `core/platform.py` 末尾追加：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_platform_registry.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/platform.py tests/test_platform_registry.py
git commit -m "feat(platform): add PlatformRegistry"
```

---

## Phase 2: 抖音适配层

### Task 4: DouyinPlatform (URL 识别)

**Files:**
- Create: `core/platforms/__init__.py`
- Create: `core/platforms/douyin.py`
- Create: `tests/test_douyin_platform.py`

现有抖音 URL 正则在 `core/pipeline.py` 顶部（`_SHORT_URL_RE` / `_VIDEO_RE` / ...）。把它们搬到 `core/platforms/douyin.py`，`DouyinPlatform.match_url` 返回 `ContentRef`。

- [ ] **Step 1: 创建 platforms 包**

```python
# core/platforms/__init__.py
"""Per-platform plugins implementing the Platform / PlatformClient protocols."""
```

- [ ] **Step 2: 写 DouyinPlatform URL 识别测试**

```python
# tests/test_douyin_platform.py
from core.platforms.douyin import DouyinPlatform


def test_match_short_url():
    p = DouyinPlatform()
    ref = p.match_url("https://v.douyin.com/abcdef/")
    assert ref is not None
    assert ref.platform == "douyin"
    assert ref.content_type == "short"
    assert ref.resource_id is None


def test_match_video():
    p = DouyinPlatform()
    ref = p.match_url("https://www.douyin.com/video/7123456789")
    assert ref is not None
    assert ref.content_type == "video"
    assert ref.resource_id == "7123456789"


def test_match_note_image():
    p = DouyinPlatform()
    ref = p.match_url("https://www.douyin.com/note/7111111")
    assert ref is not None
    assert ref.content_type == "image"
    assert ref.resource_id == "7111111"


def test_match_user():
    p = DouyinPlatform()
    ref = p.match_url(
        "https://www.douyin.com/user/MS4wLjABAAAAabcdef-1234"
    )
    assert ref is not None
    assert ref.content_type == "user"
    assert ref.resource_id.startswith("MS4wLjAB")


def test_match_mix_query():
    p = DouyinPlatform()
    ref = p.match_url(
        "https://www.douyin.com/user/MS4wLjABxxx?modal_id=1&mix_id=999"
    )
    assert ref is not None
    assert ref.content_type == "mix"
    assert ref.resource_id == "999"


def test_match_mix_collection_path():
    p = DouyinPlatform()
    ref = p.match_url("https://www.douyin.com/collection/7222")
    assert ref is not None
    assert ref.content_type == "mix"
    assert ref.resource_id == "7222"


def test_match_music():
    p = DouyinPlatform()
    ref = p.match_url("https://www.douyin.com/music/6333")
    assert ref is not None
    assert ref.content_type == "music"
    assert ref.resource_id == "6333"


def test_no_match_xhs():
    p = DouyinPlatform()
    assert p.match_url("https://www.xiaohongshu.com/explore/xxx") is None


def test_no_match_random():
    p = DouyinPlatform()
    assert p.match_url("https://example.com/foo") is None
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/test_douyin_platform.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 4: 实现 DouyinPlatform**

```python
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
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/test_douyin_platform.py -v`
Expected: 9 passed

注意：`_MIX_RE` 放在 `_VIDEO_RE` 之前已经被 pipeline 的老逻辑验证过是安全的（因为 `/collection/123` 不会匹配 `/video/123`）。但 `_USER_RE` 要放在最后，因为 `/video/...` URL 有时带查询参数 `sec_uid=...`，会被误匹配成 user。

- [ ] **Step 6: Commit**

```bash
git add core/platforms/__init__.py core/platforms/douyin.py tests/test_douyin_platform.py
git commit -m "feat(platform): DouyinPlatform URL recognition"
```

---

### Task 5: aweme_to_media_item 转换

**Files:**
- Modify: `core/platforms/douyin.py`（追加转换函数）
- Create: `tests/test_douyin_convert.py`

从 `core/downloader_engine.py` 的 `_get_video_url` / `_get_best_image_url` / `_get_music_url` / `_get_cover_urls` 抽取逻辑，改写成输出 `MediaAsset` 列表。

- [ ] **Step 1: 写 aweme 视频转换测试**

```python
# tests/test_douyin_convert.py
from core.platform import MediaItem
from core.platforms.douyin import aweme_to_media_item


def test_video_post():
    aweme = {
        "aweme_id": "123",
        "desc": "hello",
        "create_time": 1700000000,
        "author": {"nickname": "alice"},
        "video": {
            "bit_rate": [
                {
                    "bit_rate": 1000000,
                    "play_addr": {"url_list": ["https://low/a.mp4"]},
                },
                {
                    "bit_rate": 2000000,
                    "play_addr": {"url_list": ["https://hi/a.mp4"]},
                },
            ],
            "play_addr": {"url_list": ["https://fall/a.mp4"]},
            "cover": {"url_list": ["https://cov/c.jpg"]},
        },
        "music": {"play_url": {"url_list": ["https://m/x.mp3"]}},
    }
    item = aweme_to_media_item(aweme)
    assert isinstance(item, MediaItem)
    assert item.platform == "douyin"
    assert item.id == "123"
    assert item.author == "alice"
    assert item.desc == "hello"
    assert item.create_time == 1700000000

    kinds = [a.kind for a in item.assets]
    assert "video_main" in kinds
    assert "cover" in kinds
    assert "music" in kinds

    video_asset = next(a for a in item.assets if a.kind == "video_main")
    assert video_asset.url == "https://hi/a.mp4"
    assert "https://low/a.mp4" in video_asset.fallback_urls
    assert "https://fall/a.mp4" in video_asset.fallback_urls


def test_image_post():
    aweme = {
        "aweme_id": "999",
        "desc": "图集",
        "create_time": 1700000000,
        "author": {"nickname": "bob"},
        "images": [
            {
                "download_url_list": ["https://d/1.webp"],
                "url_list": ["https://u/1.jpg"],
            },
            {
                "url_list": ["https://u/2.jpg"],
            },
        ],
        "video": {"cover": {"url_list": ["https://cov/c.jpg"]}},
    }
    item = aweme_to_media_item(aweme)
    image_assets = [a for a in item.assets if a.kind == "image"]
    assert len(image_assets) == 2
    assert image_assets[0].url == "https://d/1.webp"
    assert image_assets[0].ext == "webp"
    assert image_assets[1].url == "https://u/2.jpg"
    assert image_assets[1].ext == "jpg"


def test_missing_music():
    aweme = {
        "aweme_id": "1",
        "desc": "",
        "create_time": 0,
        "author": {"nickname": "x"},
        "video": {
            "play_addr": {"url_list": ["https://v/1.mp4"]},
        },
    }
    item = aweme_to_media_item(aweme)
    assert not any(a.kind == "music" for a in item.assets)


def test_cover_fallbacks():
    aweme = {
        "aweme_id": "1",
        "desc": "",
        "create_time": 0,
        "author": {"nickname": "x"},
        "video": {
            "play_addr": {"url_list": ["https://v/1.mp4"]},
            "origin_cover": {"url_list": ["https://cov/o.jpg"]},
            "cover": {"url_list": ["https://cov/c.jpg"]},
            "dynamic_cover": {"url_list": ["https://cov/d.jpg"]},
        },
    }
    item = aweme_to_media_item(aweme)
    covers = [a for a in item.assets if a.kind == "cover"]
    assert len(covers) == 1
    assert covers[0].url == "https://cov/o.jpg"
    assert "https://cov/c.jpg" in covers[0].fallback_urls
    assert "https://cov/d.jpg" in covers[0].fallback_urls


def test_raw_preserved():
    aweme = {
        "aweme_id": "1", "desc": "", "create_time": 0,
        "author": {"nickname": "x"},
        "video": {"play_addr": {"url_list": ["https://v/1.mp4"]}},
    }
    item = aweme_to_media_item(aweme)
    assert item.raw == aweme
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_douyin_convert.py -v`
Expected: ImportError — `aweme_to_media_item` 不存在

- [ ] **Step 3: 实现转换函数**

在 `core/platforms/douyin.py` 末尾追加：

```python
from core.platform import MediaAsset, MediaItem


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
                u = u.replace("playwm", "play")
                if primary is None:
                    primary = u
                elif u not in fallbacks:
                    fallbacks.append(u)

    for key in ("play_addr_h264", "play_addr", "download_addr"):
        addr = video.get(key)
        if not addr:
            continue
        for u in addr.get("url_list", []):
            u = u.replace("playwm", "play")
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_douyin_convert.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/platforms/douyin.py tests/test_douyin_convert.py
git commit -m "feat(douyin): aweme dict → MediaItem conversion"
```

---

### Task 6: DouyinPlatformClient

**Files:**
- Modify: `core/platforms/douyin.py`（追加 Client 类）
- Create: `tests/test_douyin_client.py`

包一层把 `DouyinAPIClient` 的 dict 返回转成 `MediaItem` / `ListPage`。

- [ ] **Step 1: 写 DouyinPlatformClient 测试（用 mock）**

```python
# tests/test_douyin_client.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.platform import ContentRef, MediaItem, ListPage
from core.platforms.douyin import DouyinPlatformClient


def _make_aweme(aweme_id: str, desc: str = "x") -> dict:
    return {
        "aweme_id": aweme_id,
        "desc": desc,
        "create_time": 1700000000,
        "author": {"nickname": "alice"},
        "video": {
            "play_addr": {"url_list": [f"https://v/{aweme_id}.mp4"]},
            "cover": {"url_list": ["https://cov/c.jpg"]},
        },
    }


@pytest.mark.asyncio
async def test_fetch_single_video():
    api = MagicMock()
    api.get_video_info = AsyncMock(return_value=_make_aweme("1", "hello"))
    client = DouyinPlatformClient(api)
    span = MagicMock()

    ref = ContentRef(
        platform="douyin", content_type="video",
        resource_id="1", resolved_url="https://www.douyin.com/video/1",
    )
    item = await client.fetch_single(ref, span)

    assert isinstance(item, MediaItem)
    assert item.id == "1"
    assert item.desc == "hello"
    api.get_video_info.assert_awaited_once_with("1", span)


@pytest.mark.asyncio
async def test_fetch_list_user_posts():
    api = MagicMock()
    api.get_user_posts = AsyncMock(return_value={
        "aweme_list": [_make_aweme("1"), _make_aweme("2")],
        "has_more": 1,
        "max_cursor": 12345,
    })
    client = DouyinPlatformClient(api)
    span = MagicMock()

    ref = ContentRef(
        platform="douyin", content_type="user",
        resource_id="MS4abc", resolved_url="https://www.douyin.com/user/MS4abc",
    )
    page = await client.fetch_list(ref, cursor=0, span=span)

    assert isinstance(page, ListPage)
    assert len(page.items) == 2
    assert page.has_more is True
    assert page.next_cursor == 12345
    api.get_user_posts.assert_awaited_once_with("MS4abc", 0, span)


@pytest.mark.asyncio
async def test_fetch_list_user_likes_dispatch():
    api = MagicMock()
    api.get_user_likes = AsyncMock(return_value={
        "aweme_list": [_make_aweme("9")],
        "has_more": 0,
        "max_cursor": 0,
    })
    client = DouyinPlatformClient(api)
    span = MagicMock()

    ref = ContentRef(
        platform="douyin", content_type="user",
        resource_id="MS4abc", resolved_url="...",
        extra={"mode": "like"},
    )
    page = await client.fetch_list(ref, cursor=0, span=span)
    assert len(page.items) == 1
    assert page.has_more is False
    api.get_user_likes.assert_awaited_once_with("MS4abc", 0, span)


@pytest.mark.asyncio
async def test_fetch_list_mix():
    api = MagicMock()
    api.get_mix_items = AsyncMock(return_value={
        "aweme_list": [_make_aweme("3")],
        "has_more": 1,
        "cursor": 50,
    })
    client = DouyinPlatformClient(api)
    span = MagicMock()
    ref = ContentRef(
        platform="douyin", content_type="mix",
        resource_id="777", resolved_url="...",
    )
    page = await client.fetch_list(ref, cursor=0, span=span)
    assert page.next_cursor == 50
    assert page.has_more is True


@pytest.mark.asyncio
async def test_resolve_short_url():
    api = MagicMock()

    async def fake_resolve(url):
        return "https://www.douyin.com/video/123"

    client = DouyinPlatformClient(api, resolve_func=fake_resolve)
    out = await client.resolve_short_url("https://v.douyin.com/abc")
    assert out == "https://www.douyin.com/video/123"
```

`pytest-asyncio` 已是测试依赖（查 `requirements.txt` / `tests/` 目录；若缺失则在前置步骤安装：`pip install pytest-asyncio`，并在项目根添加 `pyproject.toml` 或 `pytest.ini` 配置 `asyncio_mode = "auto"`，或在每个 async 测试上加 `@pytest.mark.asyncio`）。

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_douyin_client.py -v`
Expected: ImportError — `DouyinPlatformClient` 不存在

- [ ] **Step 3: 实现 DouyinPlatformClient**

在 `core/platforms/douyin.py` 末尾追加：

```python
import aiohttp


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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_douyin_client.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/platforms/douyin.py tests/test_douyin_client.py
git commit -m "feat(douyin): DouyinPlatformClient adapter"
```

---

### Task 7: DownloadEngine 去抖音化

**Files:**
- Modify: `core/downloader_engine.py`
- Create: `tests/test_download_engine_media_item.py`

目标：`download_media` 的入参从 `aweme: dict` 换成 `item: MediaItem`，循环 `item.assets` 按 `kind` 生成文件名、调用已有的 `download_file`。删除 `_get_video_url` / `_get_best_image_url` / `_get_music_url` / `_get_cover_urls` / `_get_video_fallbacks`。

- [ ] **Step 1: 写新接口测试**

```python
# tests/test_download_engine_media_item.py
"""DownloadEngine consumes MediaItem (platform-agnostic)."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models import DownloadResult
from core.platform import MediaAsset, MediaItem
from core.downloader_engine import DownloadEngine


def _make_item() -> MediaItem:
    return MediaItem(
        platform="douyin",
        id="1",
        author="alice",
        desc="hello world",
        create_time=1700000000.0,
        assets=[
            MediaAsset(url="https://v/a.mp4", kind="video_main", ext="mp4"),
            MediaAsset(url="https://m/a.mp3", kind="music", ext="mp3"),
            MediaAsset(url="https://c/a.jpg", kind="cover", ext="jpg"),
        ],
        raw={"aweme_id": "1"},
    )


@pytest.fixture
def tmp_engine(tmp_path):
    tracer = MagicMock()
    tracer.add_event = MagicMock()
    logger = MagicMock()
    engine = DownloadEngine(
        save_path=tmp_path,
        tracer=tracer,
        logger=logger,
        concurrency=2,
    )
    engine.download_file = AsyncMock(return_value=(True, 1024))
    return engine, tmp_path


@pytest.mark.asyncio
async def test_download_media_video(tmp_engine):
    engine, root = tmp_engine
    span = MagicMock()
    result = await engine.download_media(_make_item(), span)

    assert isinstance(result, DownloadResult)
    assert result.success is True
    assert result.files_written == 4  # video + music + cover + data.json

    # Directory layout: save_path/douyin/alice/<ts>_hello world/
    subdirs = list(root.iterdir())
    assert len(subdirs) == 1
    assert subdirs[0].name == "douyin"
    alice = subdirs[0] / "alice"
    assert alice.exists()
    post_dirs = list(alice.iterdir())
    assert len(post_dirs) == 1
    post = post_dirs[0]
    assert post.name.endswith("_hello world")

    data_json = list(post.glob("*_data.json"))
    assert len(data_json) == 1
    assert json.loads(data_json[0].read_text(encoding="utf-8")) == {
        "aweme_id": "1"
    }


@pytest.mark.asyncio
async def test_image_item_live_photo(tmp_engine):
    engine, root = tmp_engine
    span = MagicMock()
    item = MediaItem(
        platform="xhs",
        id="note1",
        author="bob",
        desc="",
        create_time=1700000000.0,
        assets=[
            MediaAsset(
                url="https://i/1.jpg", kind="image", ext="jpg",
                suggested_filename="image_1",
            ),
            MediaAsset(
                url="https://v/1.mp4", kind="video_live", ext="mp4",
                suggested_filename="image_1_live",
            ),
            MediaAsset(
                url="https://i/2.jpg", kind="image", ext="jpg",
                suggested_filename="image_2",
            ),
        ],
        raw={"note_id": "note1"},
    )
    result = await engine.download_media(item, span)
    assert result.success is True
    # 3 assets + 1 json
    assert result.files_written == 4

    post = next(iter(
        (root / "xhs" / "bob").iterdir()
    ))
    # Filenames reflect suggested_filename
    calls = [c.args for c in engine.download_file.await_args_list]
    paths = [c[1] for c in calls]  # arg 1 is `path`
    names = sorted(p.name for p in paths)
    assert "image_1.jpg" in names
    assert "image_1_live.mp4" in names
    assert "image_2.jpg" in names


@pytest.mark.asyncio
async def test_flags_skip_music_cover(tmp_path):
    tracer = MagicMock(); tracer.add_event = MagicMock()
    logger = MagicMock()
    engine = DownloadEngine(
        save_path=tmp_path, tracer=tracer, logger=logger,
        concurrency=2,
        download_music=False, download_cover=False, download_json=False,
    )
    engine.download_file = AsyncMock(return_value=(True, 100))
    span = MagicMock()

    result = await engine.download_media(_make_item(), span)
    # Only video, no music / no cover / no json
    assert result.files_written == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_download_engine_media_item.py -v`
Expected: 类型错误或目录结构不对

- [ ] **Step 3: 重写 DownloadEngine**

替换 `core/downloader_engine.py` 全文为：

```python
"""Platform-agnostic file download engine consuming MediaItem."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiohttp

from core.errors import DownloadFileError  # noqa: F401 (public re-export)
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
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
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
        self, asset: MediaAsset, folder_name: str,
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
        return f"{asset.kind}_{int(time.time()*1000)}.{asset.ext}"

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

        for asset in item.assets:
            if self._should_skip(asset):
                continue
            path = save_dir / self._filename_for(asset, folder_name)
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
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `python -m pytest tests/test_download_engine_media_item.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/downloader_engine.py tests/test_download_engine_media_item.py
git commit -m "refactor(engine): consume MediaItem, add platform-subdir layout"
```

注意：**这一步目录结构从 `save_path/author/...` 变成了 `save_path/douyin/author/...`**。现有 pipeline 还没改，接下来 Task 8 配套调整。整体 regression 到 Task 9 之后才能跑。

---

### Task 8: Pipeline 泛型化

**Files:**
- Modify: `core/pipeline.py`

改成通过 `PlatformRegistry` 路由，统一 `_handle_single` / `_handle_list`，保留错误处理和 dashboard 交互。

- [ ] **Step 1: 重写 pipeline.py**

全文替换 `core/pipeline.py` 为：

```python
"""Platform-agnostic download pipeline orchestrator."""

from __future__ import annotations

import traceback

from core.dashboard import Dashboard
from core.downloader_engine import DownloadEngine
from core.errors import CookieExpiredError, RetryableError, SkippableError
from core.logger import BoundLogger
from core.models import AppConfig, DownloadTask, TraceSpan
from core.platform import (
    ContentRef, ListPage, MediaItem, PlatformRegistry,
)
from core.tracer import Tracer


class DownloadPipeline:
    """Orchestrates downloading for a batch of URLs across platforms.

    Responsibilities:
    - Ensure all needed cookies are valid via ``cookie_mgr``.
    - Match every URL to a registered platform; resolve short URLs.
    - For single-item refs, fetch one MediaItem and hand to the engine.
    - For list refs (user/collection/music/search/topic), paginate via
      ``PlatformClient.fetch_list`` and download each MediaItem.
    - Uniform error handling and dashboard updates.
    """

    def __init__(
        self,
        config: AppConfig,
        registry: PlatformRegistry,
        engine: DownloadEngine,
        cookie_mgr,
        tracer: Tracer,
        logger: BoundLogger,
        dashboard: Dashboard,
    ) -> None:
        self._config = config
        self._registry = registry
        self._engine = engine
        self._cookie_mgr = cookie_mgr
        self._tracer = tracer
        self._log = logger
        self._dashboard = dashboard

    def _progress_cb(self, done: int, total: int, name: str) -> None:
        self._dashboard.update_bytes_progress(done, total, name)

    async def run(self) -> None:
        session_span = self._tracer.start_trace("session", url="batch")

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            cookie_state = await self._cookie_mgr.ensure_valid_cookie()
            cs.attributes["source"] = cookie_state.source
            self._dashboard.set_cookie_state(cookie_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")
        for task in tasks:
            self._dashboard.add_task(task)
        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

    async def _prepare_tasks(
        self, parent_span: TraceSpan,
    ) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for i, url in enumerate(self._config.links):
            with self._tracer.context_span(
                parent_span, "prepare_url", url=url,
            ) as span:
                match = self._registry.match(url)
                if match is None:
                    self._log.warn("未识别的 URL 来源", url=url)
                    continue
                platform, client, ref = match

                if ref.content_type == "short":
                    resolved = await client.resolve_short_url(url)
                    span.attributes["resolved"] = resolved
                    match2 = self._registry.match(resolved)
                    if match2 is None:
                        self._log.warn("短链解析后仍无法识别", url=resolved)
                        continue
                    platform, client, ref = match2

                # Inject user-mode into ContentRef extra for douyin user.
                if ref.content_type == "user" and platform.name == "douyin":
                    if "like" in self._config.mode:
                        ref.extra["mode"] = "like"
                    else:
                        ref.extra["mode"] = "post"

                span.attributes["platform"] = platform.name
                span.attributes["type"] = ref.content_type
                span.attributes["id"] = ref.resource_id

                task = DownloadTask(
                    task_id=f"task_{i:03d}",
                    trace_id=parent_span.trace_id,
                    url=url,
                    content_type=ref.content_type,
                    resolved_url=ref.resolved_url,
                    extracted_id=ref.resource_id,
                )
                task.stats["platform"] = platform.name
                task.stats["_ref"] = ref
                task.stats["_client"] = client
                tasks.append(task)
        return tasks

    async def _execute_task(self, task: DownloadTask) -> None:
        ref: ContentRef = task.stats.pop("_ref")
        client = task.stats.pop("_client")

        root = self._tracer.start_trace(
            f"download_{ref.platform}_{ref.content_type}",
            url=task.url,
        )
        task.trace_id = root.trace_id
        task.status = "running"
        self._dashboard.update_task(task)

        try:
            if ref.content_type in ("single", "video", "image"):
                await self._handle_single(task, ref, client, root)
            else:
                await self._handle_list(task, ref, client, root)
            task.status = "done"
            self._dashboard.log_done(
                task.url[:50], True,
                f"{task.stats.get('downloaded', 0)} 个作品",
                trace_id=root.trace_id,
            )

        except CookieExpiredError:
            self._tracer.add_event(root, "cookie_expired")
            self._log.warn("Cookie 失效，重新获取...")
            await self._cookie_mgr.ensure_valid_cookie()
            task.stats["_ref"] = ref
            task.stats["_client"] = client
            await self._execute_task(task)
            return

        except SkippableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id,
            )

        except RetryableError as exc:
            task.status = "failed"
            task.error = str(exc)
            self._dashboard.log_done(
                task.url[:50], False, f"重试耗尽: {exc}",
                trace_id=root.trace_id,
            )

        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            self._log.error(
                "未预期错误", error=str(exc), tb=traceback.format_exc(),
            )
            self._dashboard.log_done(
                task.url[:50], False, str(exc), trace_id=root.trace_id,
            )

        finally:
            self._tracer.end_span(root, status=task.status)
            self._dashboard.update_task(task)

    async def _handle_single(
        self, task: DownloadTask, ref: ContentRef, client, root: TraceSpan,
    ) -> None:
        with self._tracer.context_span(
            root, "fetch_info", resource_id=ref.resource_id,
        ) as span:
            item: MediaItem = await client.fetch_single(ref, span)
            self._dashboard.record_api_call(True)

        desc = item.desc[:40]
        self._dashboard.set_current_item(
            desc=desc, author=item.author, index=1, total=1,
        )
        with self._tracer.context_span(root, "download_media") as span:
            result = await self._engine.download_media(
                item, span, on_progress=self._progress_cb,
            )
        self._dashboard.clear_current_item()
        self._dashboard.add_bytes(result.bytes_downloaded)
        task.stats["downloaded"] = 1 if result.success else 0
        self._dashboard.log_item_done(
            desc or task.url[:50],
            result.success,
            f"{result.files_written} 文件, {result.elapsed:.1f}s"
            if result.success else (result.error or "下载失败"),
        )

    async def _handle_list(
        self, task: DownloadTask, ref: ContentRef, client, root: TraceSpan,
    ) -> None:
        downloaded = 0
        cursor: str | int | None = 0
        all_items: list[MediaItem] = []

        label_map = {
            "user": "作品列表", "mix": "合集列表",
            "music": "音乐作品列表", "collection": "合集列表",
            "search": "搜索结果", "topic": "话题笔记",
        }
        label = label_map.get(ref.content_type, "列表")

        self._dashboard.set_status(f"正在获取{label}…")
        with self._tracer.context_span(root, f"fetch_all_{ref.content_type}") as fs:
            while True:
                page: ListPage = await client.fetch_list(ref, cursor, fs)
                self._dashboard.record_api_call(True)
                if not page.items:
                    break
                all_items.extend(page.items)
                fs.attributes["fetched"] = len(all_items)
                self._dashboard.set_status(
                    f"正在获取{label}… 已获取 {len(all_items)} 个"
                )
                self._dashboard.refresh()
                if not page.has_more:
                    break
                cursor = page.next_cursor
        self._dashboard.clear_status()

        total = len(all_items)
        limit_key = "post" if ref.content_type == "user" else ref.content_type
        limit = self._config.number.get(limit_key, 0)
        effective_total = min(total, limit) if limit > 0 else total

        with self._tracer.context_span(
            root, "download_posts", total=total,
        ) as dl_span:
            for i, item in enumerate(all_items):
                if limit > 0 and downloaded >= limit:
                    break
                desc = item.desc[:40]
                self._dashboard.set_current_item(
                    desc=desc, author=item.author,
                    index=i + 1, total=effective_total,
                )
                with self._tracer.context_span(
                    dl_span, "download_media",
                    index=i + 1, item_id=item.id,
                ) as media_span:
                    result = await self._engine.download_media(
                        item, media_span, on_progress=self._progress_cb,
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
                        result.error or "下载失败",
                        trace_id=media_span.trace_id,
                    )
                self._dashboard.update_progress(task, i + 1, effective_total)
                self._dashboard.refresh()

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total
```

- [ ] **Step 2: 跑已有全部测试看没破坏什么**

Run: `python -m pytest tests/ -v`
Expected: 新增的 platform/douyin/engine 测试通过；旧有测试如果依赖老 pipeline API，先记下来（Task 9 会修）。

- [ ] **Step 3: Commit**

```bash
git add core/pipeline.py
git commit -m "refactor(pipeline): route via PlatformRegistry, generic single/list handlers"
```

---

### Task 9: downloader.py 入口装配

**Files:**
- Modify: `downloader.py`

把 `DouyinAPIClient` 和 `DouyinPlatform` / `DouyinPlatformClient` 注册进 `PlatformRegistry`，pipeline 的构造参数从 `api` 改成 `registry`。

- [ ] **Step 1: 修改 downloader.py 的 cmd_download**

找到 `downloader.py` 里 `cmd_download` 函数，改成：

```python
async def cmd_download(config: AppConfig, args: argparse.Namespace):
    from core.platform import PlatformRegistry
    from core.platforms.douyin import DouyinPlatform, DouyinPlatformClient

    session_id = uuid.uuid4().hex[:8]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    console_level = "DEBUG" if args.verbose else "INFO"
    dual_logger = DualLogger(log_dir=log_dir, console_level=console_level)
    log = dual_logger.get("main")
    tracer = Tracer(log_dir=log_dir, session_id=session_id)

    log.info("抖音下载器 v4.0 启动", session_id=session_id)

    cookie_mgr = CookieManager(config, tracer=tracer, logger=dual_logger.get("cookie"))
    api = DouyinAPIClient(
        cookie_state=None,
        tracer=tracer,
        logger=dual_logger.get("api"),
        rate_limit=2.0,
        max_retries=config.retry_times,
    )
    engine = DownloadEngine(
        save_path=config.save_path,
        tracer=tracer,
        logger=dual_logger.get("engine"),
        concurrency=config.thread,
        download_music=config.download.music,
        download_cover=config.download.cover,
        download_json=config.download.json,
    )
    dashboard = Dashboard(
        total_tasks=len(config.links),
        concurrency=config.thread,
    )

    registry = PlatformRegistry()
    registry.register(DouyinPlatform(), DouyinPlatformClient(api))

    pipeline = DownloadPipeline(
        config=config, registry=registry, engine=engine,
        cookie_mgr=cookie_mgr, tracer=tracer,
        logger=dual_logger.get("pipeline"), dashboard=dashboard,
    )

    # Cookie needs to flow into DouyinAPIClient; CookieManager already
    # updates it via reference, but we also push it explicitly once:
    cookie_state = await cookie_mgr.ensure_valid_cookie()
    api.update_cookie(cookie_state)

    if not args.no_dashboard:
        dashboard.start()

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        log.warn("用户中断")
    finally:
        dashboard.stop()
        await api.close()
        await engine.close()
        tracer.close()
        dual_logger.close()

    state = dashboard.get_state()
    print(f"\n{'=' * 60}")
    print(f"完成: {state['completed']}✓ {state['failed']}✗ / {state['total']}总 | 耗时 {state['elapsed']:.1f}s")
    print(f"Session: {session_id} | 日志: logs/")
```

- [ ] **Step 2: 跑全部测试确保没有导入错误**

Run: `python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 3: 跑语法/导入检查**

Run: `python -c "import downloader; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add downloader.py
git commit -m "refactor(entry): wire DouyinPlatform into PlatformRegistry"
```

---

### Task 10: 手工回归测试

**Files:** 无代码变更，纯验证。

- [ ] **Step 1: 跑单个短链下载**

在备份 `JIN/` 目录后，执行：

```bash
cp -r JIN JIN_pre_plan1
python downloader.py -c config.yml --no-dashboard 2>&1 | tee /tmp/regression.log
```

Expected:
- 下载成功完成
- 文件落在 `JIN/douyin/<作者>/<时间>_<desc>/` 而不是 `JIN/<作者>/...`
- 产物包含 `.mp4`、`_music.mp3`、`_cover.jpg`、`_data.json`

- [ ] **Step 2: 验证产物结构**

```bash
# 每个下载任务应该有 mp4 / music / cover / json 四类文件
find JIN/douyin -mindepth 3 -maxdepth 3 -type d | while read d; do
  ls "$d" | awk 'END { print NR, "files in", ARGV[1] }' ARGV[1]="$d"
done
```

Expected: 每个作品目录 ≥ 3 个文件（mp4/json 必有，music/cover 视 download 配置）。

- [ ] **Step 3: 验证日志中没有 ERROR**

```bash
grep -E "ERROR|Traceback" /tmp/regression.log || echo "clean"
```

Expected: `clean`

- [ ] **Step 4: 如果回归通过，打 tag**

```bash
git tag xhs-plan-1-done
```

如果回归失败，回退对应 commit 定位问题（`git log --oneline` + `git bisect`）。

---

## Plan 1 完成标准

- [ ] 所有单元测试通过（`python -m pytest tests/ -v`）
- [ ] 对现有 `config.yml` 的回归下载行为一致（仅目录多一层 `douyin/`）
- [ ] `downloader.py --replay` / `--validate-cookie` / `--generate-config` 仍可用
- [ ] `core/pipeline.py` 中不再存在任何抖音专用正则或字段引用
- [ ] `PlatformRegistry` 里只注册了 `douyin`；`core/platforms/` 目录预留可扩展结构

## 交接给 Plan 2 的上下文

完成本 Plan 后，代码具备以下能力，Plan 2 可直接在其上工作：

1. `PlatformRegistry` 可以注册新平台（XHS 会是第二个）
2. `DownloadEngine` 按 `MediaItem` 工作，对所有平台一视同仁
3. 目录结构已分平台（为 XHS 的 `xhs/` 根目录做好准备）
4. `ContentRef.extra` 可携带平台特定参数（search 关键词等）

Plan 2 的主题：**目录迁移命令（`--migrate-layout`）+ 数据库层 `DownloadRepository`（替代 legacy `apiproxy/douyin/database.py`）+ 增量跳过接入 pipeline**。
