import time
from pathlib import Path
from unittest.mock import MagicMock
from core.cookie import CookieManager
from core.models import AppConfig, DownloadOptions, CookieState


def _make_config(**overrides):
    defaults = dict(
        links=["https://example.com"],
        save_path=Path("./dl"),
        cookies={},
        cookie_mode="none",
        mode=["post"],
        number={"post": 0},
        start_time=None,
        end_time=None,
        download=DownloadOptions(),
        thread=5,
        database=True,
        increase={"post": True},
        retry_times=3,
        log_level="INFO",
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


def test_parse_cookie_string():
    raw = "ttwid=abc123; sessionid=xyz789; other=val"
    parsed = CookieManager.parse_cookie_string(raw)
    assert parsed["ttwid"] == "abc123"
    assert parsed["sessionid"] == "xyz789"


def test_parse_cookie_string_with_quotes():
    raw = 'ttwid="abc123"; sessionid="xyz789"'
    parsed = CookieManager.parse_cookie_string(raw)
    assert parsed["ttwid"] == "abc123"


def test_validate_required_fields_missing():
    missing, warnings = CookieManager.check_cookie_fields({"other": "val"})
    assert "ttwid" in missing


def test_validate_with_ttwid():
    missing, warnings = CookieManager.check_cookie_fields({"ttwid": "abc"})
    assert len(missing) == 0
    assert len(warnings) > 0


def test_cookie_state_from_config():
    config = _make_config(cookies={"douyin": "ttwid=abc; sessionid=xyz"}, cookie_mode="string")
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is not None
    assert state.source == "config"
    assert "ttwid=abc" in state.value


def test_cookie_state_from_config_none():
    config = _make_config(cookies={}, cookie_mode="none")
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is None


def test_cookie_state_from_dict():
    # New platform-keyed dict format: {"douyin": "<cookie string>"}
    config = _make_config(
        cookies={"douyin": "ttwid=abc; sessionid=xyz"},
        cookie_mode="dict",
    )
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is not None
    assert "ttwid=abc" in state.value


import pytest as _pytest_for_async
from core.models import CookieState as _CookieStateImport


@_pytest_for_async.mark.asyncio
async def test_ensure_valid_cookie_xhs_from_config(tmp_path):
    from core.cookie import CookieManager
    from core.models import AppConfig, DownloadOptions
    from pathlib import Path as _P

    cfg = AppConfig(
        links=[], save_path=_P("."),
        cookies={"xhs": "a1=abc; web_session=def"},
        cookie_mode="dict",
        mode=["post"], number={"post": 0},
        start_time=None, end_time=None,
        download=DownloadOptions(),
        thread=1, database=False, increase={}, retry_times=3,
        log_level="INFO",
    )

    class _L:
        def info(self, *a, **k): pass
        def warn(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass

    mgr = CookieManager(cfg, tracer=None, logger=_L())
    state = await mgr.ensure_valid_cookie(platform="xhs")
    assert state.platform == "xhs"
    assert "a1=abc" in state.value
    assert "web_session=def" in state.value
    # second call returns the cached state
    state2 = await mgr.ensure_valid_cookie(platform="xhs")
    assert state2 is state


@_pytest_for_async.mark.asyncio
async def test_ensure_valid_cookie_xhs_no_config_raises(tmp_path):
    from core.cookie import CookieManager
    from core.errors import CookieExpiredError
    from core.models import AppConfig, DownloadOptions
    from pathlib import Path as _P

    cfg = AppConfig(
        links=[], save_path=_P("."),
        cookies={"douyin": "msToken=abc"},
        cookie_mode="string",
        mode=["post"], number={"post": 0},
        start_time=None, end_time=None,
        download=DownloadOptions(),
        thread=1, database=False, increase={}, retry_times=3,
        log_level="INFO",
    )

    class _L:
        def info(self, *a, **k): pass
        def warn(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass

    mgr = CookieManager(cfg, tracer=None, logger=_L())
    with _pytest_for_async.raises(CookieExpiredError):
        await mgr.ensure_valid_cookie(platform="xhs")


@_pytest_for_async.mark.asyncio
async def test_ensure_valid_cookie_unknown_platform_raises():
    from core.cookie import CookieManager
    from core.errors import CookieExpiredError
    from core.models import AppConfig, DownloadOptions
    from pathlib import Path as _P

    cfg = AppConfig(
        links=[], save_path=_P("."),
        cookies={"douyin": "msToken=abc"},
        cookie_mode="string",
        mode=["post"], number={"post": 0},
        start_time=None, end_time=None,
        download=DownloadOptions(),
        thread=1, database=False, increase={}, retry_times=3,
        log_level="INFO",
    )

    class _L:
        def info(self, *a, **k): pass
        def warn(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass

    mgr = CookieManager(cfg, tracer=None, logger=_L())
    with _pytest_for_async.raises(CookieExpiredError):
        await mgr.ensure_valid_cookie(platform="bilibili")


def test_get_for_url_routes_by_domain():
    from core.cookie import CookieManager
    from core.models import AppConfig, CookieState, DownloadOptions
    from pathlib import Path as _P

    cfg = AppConfig(
        links=[], save_path=_P("."),
        cookies={"douyin": "msToken=abc", "xhs": "a1=xyz"},
        cookie_mode="dict",
        mode=["post"], number={"post": 0},
        start_time=None, end_time=None,
        download=DownloadOptions(),
        thread=1, database=False, increase={}, retry_times=3,
        log_level="INFO",
    )

    class _L:
        def info(self, *a, **k): pass
        def warn(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass

    mgr = CookieManager(cfg, tracer=None, logger=_L())
    # populate the cache manually (ensure_valid_cookie would normally do this)
    mgr._states["douyin"] = CookieState(
        value="msToken=abc", source="config",
        obtained_at=0.0, platform="douyin",
    )
    mgr._states["xhs"] = CookieState(
        value="a1=xyz", source="config",
        obtained_at=0.0, platform="xhs",
    )

    d = mgr.get_for_url("https://www.douyin.com/video/123")
    assert d is not None and d.platform == "douyin"

    for url in [
        "https://www.xiaohongshu.com/explore/abc",
        "https://xhslink.com/m/xxx",
    ]:
        x = mgr.get_for_url(url)
        assert x is not None and x.platform == "xhs"

    assert mgr.get_for_url("https://example.com/foo") is None
