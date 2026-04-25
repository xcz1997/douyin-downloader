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
