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
    async def fetch_list(self, ref, cursor, span, **kw): return None


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
