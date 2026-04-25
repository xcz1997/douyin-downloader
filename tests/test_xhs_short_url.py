"""Tests for XHS xhslink short URL resolution."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.platforms.xhs import _resolve_xhslink


@pytest.mark.asyncio
async def test_resolve_xhslink_follows_location():
    fake_resp = MagicMock()
    fake_resp.status_code = 302
    fake_resp.headers = {
        "Location": (
            "https://www.xiaohongshu.com/user/profile/"
            "55c726695894464ef542aea0?xsec_token=ABC"
        ),
    }

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == (
        "https://www.xiaohongshu.com/user/profile/"
        "55c726695894464ef542aea0?xsec_token=ABC"
    )


@pytest.mark.asyncio
async def test_resolve_xhslink_returns_input_on_non_redirect():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {}

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == "https://xhslink.com/m/5kcCust1t6Z"


@pytest.mark.asyncio
async def test_resolve_xhslink_tolerates_network_error():
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(side_effect=OSError("boom"))

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == "https://xhslink.com/m/5kcCust1t6Z"
