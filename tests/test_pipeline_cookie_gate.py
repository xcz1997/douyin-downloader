"""Pipeline's upfront cookie check must be platform-aware.

Before this fix, run() unconditionally required a Douyin cookie via
ensure_valid_cookie() (default platform="douyin"). XHS-only batches
that legitimately have no Douyin cookie configured would crash before
any URL was even processed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.errors import CookieExpiredError
from core.pipeline import DownloadPipeline
from core.platform import PlatformRegistry


def _stub_config(links: list[str]):
    cfg = MagicMock()
    cfg.links = links
    cfg.number = {}
    cfg.mode = []
    return cfg


@pytest.mark.asyncio
async def test_pipeline_skips_douyin_cookie_for_xhs_only_batch():
    """XHS-only batch must not crash when douyin cookie is missing."""
    cookie_mgr = MagicMock()

    async def _ensure(platform="douyin"):
        if platform == "douyin":
            raise CookieExpiredError("no douyin cookie")
        return MagicMock(source="config", is_valid=True)

    cookie_mgr.ensure_valid_cookie = _ensure

    from core.platforms.xhs import XHSPlatform
    registry = PlatformRegistry()

    class _Dummy:
        async def resolve_short_url(self, u):
            return u

        async def fetch_single(self, ref, span):
            raise AssertionError("unreached")

        async def fetch_list(self, ref, c, span, **kw):
            raise AssertionError("unreached")

    registry.register(XHSPlatform(), _Dummy())

    dashboard = MagicMock()
    dashboard.add_task = MagicMock()
    dashboard.set_cookie_state = MagicMock()
    dashboard.refresh = MagicMock()

    tracer = MagicMock()
    tracer.start_trace.return_value = MagicMock(trace_id="t1")
    span_ctx = MagicMock()
    span_ctx.__enter__ = MagicMock(return_value=MagicMock(attributes={}))
    span_ctx.__exit__ = MagicMock(return_value=None)
    tracer.context_span.return_value = span_ctx

    logger = MagicMock()

    pl = DownloadPipeline(
        config=_stub_config(["https://xhslink.com/m/5kcCust1t6Z"]),
        registry=registry,
        engine=MagicMock(),
        cookie_mgr=cookie_mgr,
        tracer=tracer,
        logger=logger,
        dashboard=dashboard,
    )
    # Short-circuit task preparation/execution to keep the test focused on
    # the cookie gate itself.
    pl._prepare_tasks = AsyncMock(return_value=[])

    # Should NOT raise CookieExpiredError even though douyin cookie failed.
    await pl.run()


@pytest.mark.asyncio
async def test_pipeline_uses_douyin_cookie_for_douyin_only_batch():
    """Douyin-only batch should still acquire the douyin cookie."""
    captured: list[str] = []
    cookie_mgr = MagicMock()

    async def _ensure(platform="douyin"):
        captured.append(platform)
        return MagicMock(source="config", is_valid=True)

    cookie_mgr.ensure_valid_cookie = _ensure

    from core.platforms.douyin import DouyinPlatform
    registry = PlatformRegistry()

    class _Dummy:
        async def resolve_short_url(self, u):
            return u

        async def fetch_single(self, ref, span):
            raise AssertionError("unreached")

        async def fetch_list(self, ref, c, span, **kw):
            raise AssertionError("unreached")

    registry.register(DouyinPlatform(), _Dummy())

    dashboard = MagicMock()
    tracer = MagicMock()
    tracer.start_trace.return_value = MagicMock(trace_id="t1")
    span_ctx = MagicMock()
    span_ctx.__enter__ = MagicMock(return_value=MagicMock(attributes={}))
    span_ctx.__exit__ = MagicMock(return_value=None)
    tracer.context_span.return_value = span_ctx

    pl = DownloadPipeline(
        config=_stub_config(["https://www.douyin.com/video/123"]),
        registry=registry,
        engine=MagicMock(),
        cookie_mgr=cookie_mgr,
        tracer=tracer,
        logger=MagicMock(),
        dashboard=dashboard,
    )
    pl._prepare_tasks = AsyncMock(return_value=[])

    await pl.run()
    assert "douyin" in captured
    assert "xhs" not in captured
