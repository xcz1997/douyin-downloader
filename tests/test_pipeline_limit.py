"""Pipeline `number.post` limit must count notes that produced media,
not the engine's all-or-nothing `success` flag.

Before this fix, `_handle_list` only incremented `downloaded` when
`result.success=True`. The engine flips `success=False` on any partial
asset failure (e.g. cover 403 while video succeeded), so a batch where
every note had at least one failing asset would scan the entire list
even with `number.post: 3` set — defeating the smoke-test escape hatch.

The new contract: count notes whose `media_files_written > 0`. A note
that fully failed (no media, only `_data.json`) is intentionally NOT
counted, so a fully-broken cookie/UA scenario surfaces every failure
to the operator instead of silently stopping at N.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models import DownloadResult, DownloadTask
from core.pipeline import DownloadPipeline
from core.platform import ContentRef, ListPage, MediaAsset, MediaItem


def _make_items(n: int) -> list[MediaItem]:
    return [
        MediaItem(
            platform="xhs", id=f"note_{i}", author="alice", desc=f"d{i}",
            create_time=1700000000.0,
            assets=[MediaAsset(url=f"https://i/{i}.jpg", kind="image", ext="jpg")],
            raw={"id": f"note_{i}"},
        )
        for i in range(n)
    ]


def _result(success: bool, media: int) -> DownloadResult:
    task = DownloadTask(
        task_id="x", trace_id="t", url="", content_type="user",
    )
    return DownloadResult(
        task=task, success=success,
        files_written=media + 1,  # +1 for _data.json
        elapsed=0.1,
        media_files_written=media,
    )


def _make_pipeline(engine, config_number):
    config = MagicMock()
    config.number = config_number

    tracer = MagicMock()
    span_ctx = MagicMock()
    span_ctx.__enter__ = MagicMock(return_value=MagicMock(attributes={}))
    span_ctx.__exit__ = MagicMock(return_value=None)
    tracer.context_span.return_value = span_ctx

    dashboard = MagicMock()
    dashboard.set_current_item = MagicMock()
    dashboard.clear_current_item = MagicMock()
    dashboard.set_status = MagicMock()
    dashboard.clear_status = MagicMock()
    dashboard.add_bytes = MagicMock()
    dashboard.update_progress = MagicMock()
    dashboard.refresh = MagicMock()
    dashboard.record_api_call = MagicMock()
    dashboard.log_item_done = MagicMock()

    return DownloadPipeline(
        config=config,
        registry=MagicMock(),
        engine=engine,
        cookie_mgr=MagicMock(),
        tracer=tracer,
        logger=MagicMock(),
        dashboard=dashboard,
    )


@pytest.mark.asyncio
async def test_limit_honored_when_partial_asset_failures():
    """Each note has success=False but at least 1 media file landed.
    With limit=3, only 3 notes should be attempted."""
    engine = MagicMock()
    engine.download_media = AsyncMock(return_value=_result(False, 1))

    pl = _make_pipeline(engine, {"post": 3})

    client = MagicMock()
    client.fetch_list = AsyncMock(
        return_value=ListPage(items=_make_items(5), next_cursor=None, has_more=False),
    )

    task = DownloadTask(
        task_id="t1", trace_id="tr", url="https://x/y", content_type="user",
    )
    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u1",
        resolved_url="https://x/y",
    )
    await pl._handle_list(task, ref, client, MagicMock())

    assert engine.download_media.await_count == 3, \
        "limit=3 should stop after 3 notes when each landed media"


@pytest.mark.asyncio
async def test_limit_keeps_scanning_when_all_assets_fail():
    """Every note had zero media files (all 403 / cookie expired / UA blocked).
    Limit should NOT stop the scan — operator needs every failure surfaced
    for diagnosis, not a silent early-exit."""
    engine = MagicMock()
    engine.download_media = AsyncMock(return_value=_result(False, 0))

    pl = _make_pipeline(engine, {"post": 3})

    client = MagicMock()
    client.fetch_list = AsyncMock(
        return_value=ListPage(items=_make_items(5), next_cursor=None, has_more=False),
    )

    task = DownloadTask(
        task_id="t1", trace_id="tr", url="https://x/y", content_type="user",
    )
    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u1",
        resolved_url="https://x/y",
    )
    await pl._handle_list(task, ref, client, MagicMock())

    assert engine.download_media.await_count == 5, \
        "all-fail scenario must scan whole list; limit only fires on success"


@pytest.mark.asyncio
async def test_limit_honored_on_full_success():
    """Baseline: when everything succeeds, limit=3 stops at 3 (unchanged)."""
    engine = MagicMock()
    engine.download_media = AsyncMock(return_value=_result(True, 2))

    pl = _make_pipeline(engine, {"post": 3})

    client = MagicMock()
    client.fetch_list = AsyncMock(
        return_value=ListPage(items=_make_items(10), next_cursor=None, has_more=False),
    )

    task = DownloadTask(
        task_id="t1", trace_id="tr", url="https://x/y", content_type="user",
    )
    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u1",
        resolved_url="https://x/y",
    )
    await pl._handle_list(task, ref, client, MagicMock())

    assert engine.download_media.await_count == 3


@pytest.mark.asyncio
async def test_no_limit_means_full_scan():
    """`number.post` missing or 0 → no cap, scan everything."""
    engine = MagicMock()
    engine.download_media = AsyncMock(return_value=_result(True, 1))

    pl = _make_pipeline(engine, {})  # no limit configured

    client = MagicMock()
    client.fetch_list = AsyncMock(
        return_value=ListPage(items=_make_items(4), next_cursor=None, has_more=False),
    )

    task = DownloadTask(
        task_id="t1", trace_id="tr", url="https://x/y", content_type="user",
    )
    ref = ContentRef(
        platform="xhs", content_type="user", resource_id="u1",
        resolved_url="https://x/y",
    )
    await pl._handle_list(task, ref, client, MagicMock())

    assert engine.download_media.await_count == 4
