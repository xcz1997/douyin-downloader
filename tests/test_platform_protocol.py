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
