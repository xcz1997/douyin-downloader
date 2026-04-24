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
