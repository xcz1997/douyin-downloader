"""XHSBrowserSession headless default + env override.

Going headed by default reduces XHS bot-detection signals (no
navigator.webdriver, no HeadlessChrome in UA). CI/server runs without
a display set XHS_HEADLESS=1 to opt back into headless.
"""
from __future__ import annotations

import os

from core.platforms.xhs_browser import XHSBrowserSession


def test_default_is_headed(monkeypatch):
    """No XHS_HEADLESS env → headed (XHS bot-detection avoidance)."""
    monkeypatch.delenv("XHS_HEADLESS", raising=False)
    s = XHSBrowserSession(cookie_header="a=b")
    assert s._headless is False


def test_env_var_one_enables_headless(monkeypatch):
    monkeypatch.setenv("XHS_HEADLESS", "1")
    s = XHSBrowserSession(cookie_header="a=b")
    assert s._headless is True


def test_env_var_zero_keeps_headed(monkeypatch):
    monkeypatch.setenv("XHS_HEADLESS", "0")
    s = XHSBrowserSession(cookie_header="a=b")
    assert s._headless is False


def test_explicit_kwarg_overrides_env(monkeypatch):
    """Caller passing `headless=True` wins even when XHS_HEADLESS=0."""
    monkeypatch.setenv("XHS_HEADLESS", "0")
    s = XHSBrowserSession(cookie_header="a=b", headless=True)
    assert s._headless is True

    monkeypatch.setenv("XHS_HEADLESS", "1")
    s2 = XHSBrowserSession(cookie_header="a=b", headless=False)
    assert s2._headless is False


def test_interactive_default_tracks_headless(monkeypatch):
    """`interactive` defaults to True when headed (operator can confirm
    login), False when headless (no display to inspect)."""
    monkeypatch.delenv("XHS_HEADLESS", raising=False)
    headed = XHSBrowserSession(cookie_header="a=b")
    assert headed._headless is False
    assert headed._interactive is True

    monkeypatch.setenv("XHS_HEADLESS", "1")
    headless = XHSBrowserSession(cookie_header="a=b")
    assert headless._headless is True
    assert headless._interactive is False


def test_interactive_explicit_overrides_default():
    """Caller can opt out of interactive even when headed (e.g. CI run
    that injected a fresh cookie and trusts it)."""
    s = XHSBrowserSession(
        cookie_header="a=b", headless=False, interactive=False,
    )
    assert s._headless is False
    assert s._interactive is False
