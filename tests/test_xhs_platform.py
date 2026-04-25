# tests/test_xhs_platform.py
from core.platforms.xhs import XHSPlatform


def test_match_short_url_with_a_prefix():
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/a/ABCDEFG")
    assert ref is not None
    assert ref.platform == "xhs"
    assert ref.content_type == "short"
    assert ref.resource_id is None


def test_match_short_url_with_m_prefix():
    """Real-world short link format: xhslink.com/m/xxx."""
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/m/5kcCust1t6Z")
    assert ref is not None
    assert ref.content_type == "short"


def test_match_short_url_plain():
    """Bare xhslink.com/xxx (no /m/ or /a/) also qualifies."""
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/Abc123xyz")
    assert ref is not None
    assert ref.content_type == "short"


def test_match_explore_note():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/explore/65f8a1b2c3d4e5f6a7b8c9d0"
    )
    assert ref is not None
    assert ref.content_type == "single"
    assert ref.resource_id == "65f8a1b2c3d4e5f6a7b8c9d0"


def test_match_discovery_item_note():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/discovery/item/65f8a1b2c3d4e5f6a7b8c9d0"
    )
    assert ref is not None
    assert ref.content_type == "single"
    assert ref.resource_id == "65f8a1b2c3d4e5f6a7b8c9d0"


def test_match_user_profile():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/5abc123456789def0abcdef1"
    )
    assert ref is not None
    assert ref.content_type == "user"
    assert ref.resource_id == "5abc123456789def0abcdef1"


def test_match_user_profile_with_query():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/5abc123?xhsshare=1&appuid=2"
    )
    assert ref is not None
    assert ref.content_type == "user"
    assert ref.resource_id == "5abc123"


def test_match_board_collection():
    p = XHSPlatform()
    ref = p.match_url("https://www.xiaohongshu.com/board/65f8a1b2c3d4")
    assert ref is not None
    assert ref.content_type == "collection"
    assert ref.resource_id == "65f8a1b2c3d4"


def test_match_search_with_keyword():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/search_result?keyword=%E5%92%96%E5%95%A1&source=web"
    )
    assert ref is not None
    assert ref.content_type == "search"
    # keyword is URL-decoded into extra
    assert ref.extra.get("keyword") == "咖啡"


def test_match_topic():
    p = XHSPlatform()
    ref = p.match_url("https://www.xiaohongshu.com/page/topics/65f8a1b2")
    assert ref is not None
    assert ref.content_type == "topic"
    assert ref.resource_id == "65f8a1b2"


def test_no_match_douyin():
    p = XHSPlatform()
    assert p.match_url("https://www.douyin.com/video/7123456") is None


def test_no_match_random():
    p = XHSPlatform()
    assert p.match_url("https://example.com/foo") is None


def test_explore_url_preserves_xsec():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/explore/abc123"
        "?xsec_token=TOKEN&xsec_source=app_share"
    )
    assert ref is not None
    assert ref.extra.get("xsec_token") == "TOKEN"
    assert ref.extra.get("xsec_source") == "app_share"


def test_user_url_preserves_xsec():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/5abc123456789def0abcdef1"
        "?xsec_token=TOK"
    )
    assert ref is not None
    assert ref.extra.get("xsec_token") == "TOK"
