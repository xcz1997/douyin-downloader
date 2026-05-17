"""Shared TUI widgets: persistent log pane + status bar."""

from __future__ import annotations

from textual.widgets import RichLog, Static


class LogPane(RichLog):
    """Bottom persistent log/progress view. Lines appended via write()."""

    def __init__(self) -> None:
        super().__init__(highlight=False, markup=True, wrap=True,
                          id="logpane")


class StatusBar(Static):
    """One-line status: cookie/profile indicators + current status text."""

    def __init__(self) -> None:
        self._cookie = "?"
        self._profile = "?"
        self._status = ""
        # Pass initial content directly so the visual is ready before mounting.
        super().__init__(
            f"cookie:{self._cookie}  profile:{self._profile}  {self._status}",
            id="statusbar",
        )

    def _do_render(self) -> None:
        self.update(
            f"cookie:{self._cookie}  profile:{self._profile}  "
            f"{self._status}"
        )

    def set_cookie(self, ok: bool) -> None:
        self._cookie = "✓" if ok else "✗"
        self._do_render()

    def set_profile(self, ok: bool) -> None:
        self._profile = "✓" if ok else "✗"
        self._do_render()

    def set_status(self, text: str) -> None:
        self._status = text
        self._do_render()
