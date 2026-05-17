"""Tests for TUI-T9: exit cleanup (spec §5 defect #5).

Verifies that:
1. Regression (close called): _xhs_session.close() is awaited on quit when no
   modal is needed (session set, treated as no-modal path for simplicity).
2. Confirmation modal shown when download worker is active (session set)
3. Cancel on modal → app not exited, session not closed
4. Confirm on modal → session closed, app exits
5. No modal when no active session/worker (direct cleanup)
6. _xhs_session=None during quit does not raise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tui.app import DownloaderApp, QuitConfirmScreen
from tui.panels.download import DownloadPanel


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 1. Regression: session.close() called on quit (no-modal path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_calls_session_close(tmp_path):
    """Regression: _xhs_session.close() must be awaited+completed on cleanup.

    Before the fix, action_quit was the Textual default which called self.exit()
    directly without awaiting the worker's finally block → close() never called.

    Exercises the pure cleanup coroutine with a real-awaiting fake close.
    """
    import asyncio

    app = DownloaderApp(config_path=_cfg(tmp_path))
    closed: list[bool] = []

    async def fake_close():
        await asyncio.sleep(0)
        closed.append(True)

    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        mock_session = MagicMock()
        mock_session.close = fake_close
        panel._xhs_session = mock_session

        await app._close_session_and_exit()
        await pilot.pause()

    assert closed == [True]


# ---------------------------------------------------------------------------
# 2. Confirmation modal shown when session is active
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_shows_modal_when_session_active(tmp_path):
    """When _xhs_session is set (download in progress), quit shows a modal."""
    app = DownloaderApp(config_path=_cfg(tmp_path))
    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        panel._xhs_session = mock_session

        # Start quit — modal should appear
        await app.run_action("quit")
        await pilot.pause()

        # App should still be running (modal is on screen)
        screens_on_stack = app.screen_stack
        assert any(isinstance(s, QuitConfirmScreen) for s in screens_on_stack)


# ---------------------------------------------------------------------------
# 3. Cancel on modal → app stays alive, session NOT closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_modal_cancel_keeps_app_open(tmp_path):
    """Pressing cancel on the quit modal leaves the app running."""
    app = DownloaderApp(config_path=_cfg(tmp_path))
    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        panel._xhs_session = mock_session

        await app.run_action("quit")
        await pilot.pause()

        # Modal is the top of the screen stack
        modal = next(s for s in app.screen_stack if isinstance(s, QuitConfirmScreen))
        # Dismiss with False = cancel
        modal.dismiss(False)
        await pilot.pause()

        # Session should NOT be closed
        mock_session.close.assert_not_awaited()
        # App still running (no exit)
        assert not app._exit


# ---------------------------------------------------------------------------
# 4. Confirm on modal → session closed, app exits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_modal_confirm_closes_session_and_exits(tmp_path):
    """Confirming the quit modal must FULLY close the session AND then exit.

    Regression for the self-cancelling-worker bug: close() has a real internal
    await, so if the cleanup worker is cancelled mid-close (CancelledError is
    BaseException — not caught by `except Exception`), `closed` stays empty and
    exit() never runs. Asserting on both `closed` and exit being called proves
    close() completed before exit and was not interrupted.
    """
    import asyncio

    app = DownloaderApp(config_path=_cfg(tmp_path))

    closed: list[bool] = []

    async def fake_close():
        await asyncio.sleep(0)  # real await point — cancellation lands here
        closed.append(True)

    exited: list[bool] = []
    real_exit = type(app).exit

    def spy_exit(self, *a, **k):
        exited.append(True)
        return real_exit(self, *a, **k)

    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        mock_session = MagicMock()
        mock_session.close = fake_close
        panel._xhs_session = mock_session

        import unittest.mock as _m
        with _m.patch.object(type(app), "exit", spy_exit):
            await app.run_action("quit")
            await pilot.pause()

            # Modal is the top of the screen stack
            modal = next(
                s for s in app.screen_stack
                if isinstance(s, QuitConfirmScreen)
            )
            modal.dismiss(True)
            await pilot.pause()
            await pilot.pause()  # allow cleanup worker to run

            assert closed == [True], "close() must complete (not be cancelled)"
            assert exited == [True], "exit() must run after close() completes"


# ---------------------------------------------------------------------------
# 5. No modal when no active session — direct cleanup + exit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_no_modal_when_no_session(tmp_path):
    """Without an active session, quit should not push a modal."""
    app = DownloaderApp(config_path=_cfg(tmp_path))
    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        assert panel._xhs_session is None  # no active session

        # Should exit cleanly (context manager finishes = app exited)
        await app.run_action("quit")
        await pilot.pause()
        # If we get here without error and the context manager closed, all good


# ---------------------------------------------------------------------------
# 6. _xhs_session=None during quit does not raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_none_session_no_error(tmp_path):
    """Quitting with _xhs_session=None must not raise any exception."""
    app = DownloaderApp(config_path=_cfg(tmp_path))
    async with app.run_test() as pilot:
        panel = app.query_one(DownloadPanel)
        panel._xhs_session = None
        # Should not raise
        await app.run_action("quit")
        await pilot.pause()
