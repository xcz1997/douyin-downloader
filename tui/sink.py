"""TextualSink: a ProgressSink implementation that turns every pipeline
callback into a structured event handed to an injected `emit` callable.

The app injects an `emit` that posts the event as a Textual message
(thread-safe). Decoupling from a live app keeps this unit-testable.
"""

from __future__ import annotations

from typing import Any, Callable

from core.models import CookieState, DownloadTask

Event = dict[str, Any]


class TextualSink:
    def __init__(self, emit: Callable[[Event], None]) -> None:
        self._emit = emit
        self._total = 0
        self._completed = 0
        self._failed = 0

    def _e(self, kind: str, **payload: Any) -> None:
        self._emit({"kind": kind, "payload": payload})

    # --- task lifecycle ---
    def add_task(self, task: DownloadTask) -> None:
        self._total += 1
        self._e("add_task", task_id=task.task_id, url=task.url)

    def update_task(self, task: DownloadTask) -> None:
        # Status strings must match what core/pipeline.py actually assigns
        # (verified against pipeline): success="done", failure="failed".
        if task.status == "done":
            self._completed += 1
        elif task.status == "failed":
            self._failed += 1
        self._e("update_task", task_id=task.task_id, status=task.status)

    def update_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None:
        self._e("progress", task_id=task.task_id, done=done, total=total)

    def update_file_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None:
        self._e("file_progress", task_id=task.task_id,
                done=done, total=total)

    def set_current_item(
        self, *, desc: str = "", author: str = "",
        index: int = 0, total: int = 0,
    ) -> None:
        self._e("current_item", desc=desc, author=author,
                index=index, total=total)

    def update_bytes_progress(
        self, bytes_done: int, bytes_total: int, name: str
    ) -> None:
        self._e("bytes_progress", bytes_done=bytes_done,
                bytes_total=bytes_total, name=name)

    def clear_current_item(self) -> None:
        self._e("clear_current_item")

    def add_bytes(self, nbytes: int) -> None:
        self._e("add_bytes", nbytes=nbytes)

    def set_status(self, message: str) -> None:
        self._e("status", message=message)

    def clear_status(self) -> None:
        self._e("clear_status")

    # log_done (batch summary) and log_item_done (per-item) intentionally
    # both emit kind "log": the TUI is a scrolling log pane and the spec
    # does not require distinguishing them. Keep one kind (YAGNI).
    def log_done(self, *args: Any, **kwargs: Any) -> None:
        self._e("log", args=list(args), kwargs=kwargs)

    def log_item_done(self, *args: Any, **kwargs: Any) -> None:
        self._e("log", args=list(args), kwargs=kwargs)

    def record_api_call(self, success: bool) -> None:
        self._e("api_call", success=success)

    def set_cookie_state(self, state: CookieState) -> None:
        self._e("cookie_state", platform=state.platform,
                source=state.source, is_valid=state.is_valid)

    def get_state(self) -> dict[str, Any]:
        return {
            "total": self._total,
            "completed": self._completed,
            "failed": self._failed,
        }

    def refresh(self) -> None:
        self._e("refresh")

    def start(self) -> None:
        self._e("start")

    def stop(self) -> None:
        self._e("stop")
