"""XHS fetch_list must honor the `limit` hint to stop hydrating.

XHS hydration costs one chromium navigation per note (~1.3s). Without
honoring `limit`, a smoke test against a 405-note profile takes ~9 min
instead of ~30s. The pipeline-level limit only saves the *download*
phase, not the hydrate phase, so the client must enforce.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.platform import ContentRef, MediaAsset, MediaItem
from core.platforms.xhs import XHSPlatformClient


def _stub_listings(n: int) -> list[dict]:
    return [{"noteId": f"n{i}", "xsec_token": f"t{i}"} for i in range(n)]


def _stub_item(note_id: str) -> MediaItem:
    return MediaItem(
        platform="xhs", id=note_id, author="x", desc="",
        create_time=1700000000.0,
        assets=[MediaAsset(url=f"https://i/{note_id}.jpg", kind="image", ext="jpg")],
        raw={"noteId": note_id},
    )


def test_default_delay_ranges_are_conservative():
    """Defaults must keep us under the XHS per-IP rate-limit window.
    The 14:14-2026-04-25 smoke peaked at ~200-400 req/min and got
    throttled — anything tighter than (5, 10) on either knob risks
    re-triggering."""
    client = XHSPlatformClient(session=object())
    assert client._hydrate_delay_range[0] >= 5.0
    assert client._hydrate_delay_range[1] >= 10.0
    assert client._scroll_delay_range[0] >= 5.0
    assert client._scroll_delay_range[1] >= 10.0


@pytest.mark.asyncio
async def test_fetch_list_honors_limit():
    """`limit=3` against 10-note listing should call fetch_single 3 times."""
    client = XHSPlatformClient(session=object(), hydrate_delay_range=(0, 0))  # session presence only
    client._collect_user_listings = AsyncMock(return_value=_stub_listings(10))

    async def _fake_single(ref, span):
        return _stub_item(ref.resource_id)

    client.fetch_single = AsyncMock(side_effect=_fake_single)

    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u",
        resolved_url="", extra={"xsec_token": "TT"},
    )
    page = await client.fetch_list(ref, cursor=None, span=None, limit=3)

    assert len(page.items) == 3
    assert client.fetch_single.await_count == 3
    assert page.has_more is False


@pytest.mark.asyncio
async def test_fetch_list_no_limit_hydrates_all():
    """`limit=0` (default) hydrates the entire listing."""
    client = XHSPlatformClient(session=object(), hydrate_delay_range=(0, 0))
    client._collect_user_listings = AsyncMock(return_value=_stub_listings(7))

    async def _fake_single(ref, span):
        return _stub_item(ref.resource_id)

    client.fetch_single = AsyncMock(side_effect=_fake_single)

    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u",
        resolved_url="", extra={"xsec_token": "TT"},
    )
    page = await client.fetch_list(ref, cursor=None, span=None)

    assert len(page.items) == 7
    assert client.fetch_single.await_count == 7


@pytest.mark.asyncio
async def test_hydrate_delay_invoked_between_successes():
    """Default hydrate delay range > 0 means asyncio.sleep is called once
    per successful hydrate (not on failure)."""
    import asyncio
    client = XHSPlatformClient(
        session=object(), hydrate_delay_range=(0.01, 0.01),
    )
    client._collect_user_listings = AsyncMock(return_value=_stub_listings(3))

    async def _fake_single(ref, span):
        return _stub_item(ref.resource_id)

    client.fetch_single = AsyncMock(side_effect=_fake_single)

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _capture_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)

    import unittest.mock as _m
    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u",
        resolved_url="", extra={"xsec_token": "TT"},
    )
    with _m.patch("asyncio.sleep", _capture_sleep):
        page = await client.fetch_list(ref, cursor=None, span=None)

    assert len(page.items) == 3
    # One sleep per success. A failure path would skip the sleep, but all
    # 3 hydrates succeeded here.
    assert len(sleeps) == 3
    assert all(0.005 <= s <= 0.015 for s in sleeps)


@pytest.mark.asyncio
async def test_fetch_list_limit_skips_failed_notes_until_count_reached():
    """When some hydrate calls fail (deleted/private), keep going until
    `limit` successful items collected — failures don't count."""
    client = XHSPlatformClient(session=object(), hydrate_delay_range=(0, 0))
    client._collect_user_listings = AsyncMock(return_value=_stub_listings(10))

    # First 2 succeed, next 2 fail, next 1 succeeds → reach limit=3.
    call_count = {"n": 0}

    async def _fake_single(ref, span):
        call_count["n"] += 1
        if call_count["n"] in (3, 4):
            raise RuntimeError("note unavailable")
        return _stub_item(ref.resource_id)

    client.fetch_single = AsyncMock(side_effect=_fake_single)

    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u",
        resolved_url="", extra={"xsec_token": "TT"},
    )
    page = await client.fetch_list(ref, cursor=None, span=None, limit=3)

    assert len(page.items) == 3  # 3 successes despite 2 failures
    assert client.fetch_single.await_count == 5  # 2 ok + 2 fail + 1 ok
