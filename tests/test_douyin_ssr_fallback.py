# tests/test_douyin_ssr_fallback.py
from pathlib import Path

import pytest

from core.api_client import _extract_ssr_aweme
from core.errors import ContentNotFoundError

_FIXTURE = Path(__file__).parent / "fixtures" / "douyin" / "share_video.html"


def test_extract_ssr_aweme_returns_raw_aweme():
    html = _FIXTURE.read_text(encoding="utf-8")
    aweme = _extract_ssr_aweme(html)

    assert aweme["aweme_id"] == "7639371669090612411"
    # Same shape as an aweme_list item -> aweme_to_media_item can consume it
    url_list = aweme["video"]["play_addr"]["url_list"]
    assert url_list and url_list[0].startswith("http")
    assert aweme.get("desc")
    assert aweme.get("author")


def test_extract_ssr_aweme_no_router_data():
    with pytest.raises(ContentNotFoundError):
        _extract_ssr_aweme("<html><body>redirected to SPA</body></html>")
