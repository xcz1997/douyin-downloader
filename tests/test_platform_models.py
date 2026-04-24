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
