import time
from pathlib import Path
from unittest.mock import MagicMock
from core.cookie import CookieManager
from core.models import AppConfig, DownloadOptions, CookieState


def _make_config(**overrides):
    defaults = dict(
        links=["https://example.com"],
        save_path=Path("./dl"),
        cookies=None,
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
    config = _make_config(cookies="ttwid=abc; sessionid=xyz", cookie_mode="string")
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is not None
    assert state.source == "config"
    assert "ttwid=abc" in state.value


def test_cookie_state_from_config_none():
    config = _make_config(cookies=None, cookie_mode="none")
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is None


def test_cookie_state_from_dict():
    config = _make_config(cookies={"ttwid": "abc", "sessionid": "xyz"}, cookie_mode="dict")
    mgr = CookieManager(config, tracer=None, logger=MagicMock())
    state = mgr._state_from_config()
    assert state is not None
    assert "ttwid=abc" in state.value
