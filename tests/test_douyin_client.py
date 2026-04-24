# tests/test_douyin_client.py
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
