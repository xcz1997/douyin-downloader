"""Rich Live dashboard with state tracking for the download pipeline.

The Dashboard class manages all runtime state (active tasks, counters,
cookie info, API metrics) and, when Rich is available, renders an
interactive live display.  It degrades gracefully when Rich or psutil
are not installed — all state-management methods work regardless.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.models import CookieState, DownloadTask

# ---------------------------------------------------------------------------
# Optional heavy dependencies
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

try:
    import psutil as _psutil  # noqa: F401

    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSUTIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Log entry for completed / failed items
# ---------------------------------------------------------------------------
class _DoneEntry:
    """Single record stored in the done-log ring buffer."""

    __slots__ = ("label", "success", "detail", "trace_id", "ts")

    def __init__(
        self,
        label: str,
        success: bool,
        detail: str,
        trace_id: str,
        ts: float,
    ) -> None:
        self.label = label
        self.success = success
        self.detail = detail
        self.trace_id = trace_id
        self.ts = ts


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class Dashboard:
    """Runtime state tracker and optional Rich Live display.

    Args:
        total_tasks: Total number of download tasks scheduled for this run.
        concurrency: Maximum number of concurrent downloads.
        refresh_per_second: Rich Live refresh rate (ignored when Rich is
            unavailable).
        done_log_size: Maximum entries kept in the done-log ring buffer.
    """

    def __init__(
        self,
        total_tasks: int,
        concurrency: int,
        refresh_per_second: float = 4.0,
        done_log_size: int = 50,
    ) -> None:
        self._total: int = total_tasks
        self._concurrency: int = concurrency
        self._refresh_per_second: float = refresh_per_second
        self._done_log_size: int = done_log_size

        # Task registry
        self._tasks: dict[str, DownloadTask] = {}
        # Per-task file-level progress: task_id -> (done_files, total_files)
        self._task_progress: dict[str, tuple[int, int]] = {}

        # Counters
        self._completed: int = 0
        self._failed: int = 0
        self._api_calls: int = 0
        self._api_fails: int = 0

        # Done-log ring buffer (most-recent last)
        self._done_log: list[_DoneEntry] = []

        # Cookie state
        self._cookie_state: CookieState | None = None

        # Timing
        self._start_time: float = time.monotonic()

        # Thread safety
        self._lock: threading.Lock = threading.Lock()

        # Rich Live handle (None when Rich is unavailable or not started)
        self._live: Any | None = None
        self._console: Any | None = None

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def add_task(self, task: DownloadTask) -> None:
        """Register a new task in the dashboard.

        Args:
            task: The DownloadTask to register.
        """
        with self._lock:
            self._tasks[task.task_id] = task

    def update_task(self, task: DownloadTask) -> None:
        """Refresh the stored snapshot of a task.

        Args:
            task: The DownloadTask with updated fields.
        """
        with self._lock:
            self._tasks[task.task_id] = task

    def update_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None:
        """Record file-level download progress for a task.

        Args:
            task: The task whose progress is being updated.
            done: Number of files completed so far.
            total: Total number of files expected.
        """
        with self._lock:
            self._task_progress[task.task_id] = (done, total)

    def update_file_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None:
        """Alias for :meth:`update_progress` (byte-level or file-level).

        Args:
            task: The task whose progress is being updated.
            done: Amount completed (bytes or files).
            total: Total amount expected.
        """
        self.update_progress(task, done, total)

    # ------------------------------------------------------------------
    # Completion logging
    # ------------------------------------------------------------------

    def log_done(
        self,
        label: str,
        success: bool,
        detail: str,
        *,
        trace_id: str = "",
    ) -> None:
        """Record that a task has finished (succeeded or failed).

        Args:
            label: Human-readable name / URL of the task.
            success: ``True`` if the task succeeded, ``False`` otherwise.
            detail: Short description (e.g. file count or error message).
            trace_id: Optional trace identifier for correlation.
        """
        entry = _DoneEntry(
            label=label,
            success=success,
            detail=detail,
            trace_id=trace_id,
            ts=time.time(),
        )
        with self._lock:
            if success:
                self._completed += 1
            else:
                self._failed += 1

            self._done_log.append(entry)
            # Keep ring buffer bounded
            if len(self._done_log) > self._done_log_size:
                self._done_log = self._done_log[-self._done_log_size :]

    # ------------------------------------------------------------------
    # API metrics
    # ------------------------------------------------------------------

    def record_api_call(self, success: bool) -> None:
        """Track an outbound API call result.

        Args:
            success: ``True`` if the API call succeeded, ``False`` otherwise.
        """
        with self._lock:
            self._api_calls += 1
            if not success:
                self._api_fails += 1

    # ------------------------------------------------------------------
    # Cookie state
    # ------------------------------------------------------------------

    def set_cookie_state(self, state: CookieState) -> None:
        """Update the tracked cookie state.

        Args:
            state: Current :class:`~core.models.CookieState`.
        """
        with self._lock:
            self._cookie_state = state

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return a thread-safe snapshot of the current dashboard state.

        Returns:
            Dictionary with keys:
            ``total``, ``completed``, ``failed``, ``active_count``,
            ``api_calls``, ``api_fails``, ``cookie_source``, ``elapsed``.
        """
        with self._lock:
            active = sum(
                1
                for t in self._tasks.values()
                if t.status == "running"
            )
            cookie_source = (
                self._cookie_state.source
                if self._cookie_state is not None
                else None
            )
            elapsed = time.monotonic() - self._start_time
            return {
                "total": self._total,
                "completed": self._completed,
                "failed": self._failed,
                "active_count": active,
                "api_calls": self._api_calls,
                "api_fails": self._api_fails,
                "cookie_source": cookie_source,
                "elapsed": elapsed,
            }

    # ------------------------------------------------------------------
    # Rich Live lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the Rich Live display (no-op when Rich is unavailable)."""
        if not _RICH_AVAILABLE:
            return
        self._console = Console()
        self._live = Live(
            self._build_display(),
            console=self._console,
            refresh_per_second=self._refresh_per_second,
        )
        self._live.start()

    def refresh(self) -> None:
        """Push a fresh render frame to the Live display.

        No-op when Rich is unavailable or :meth:`start` has not been called.
        """
        if self._live is None:
            return
        self._live.update(self._build_display())

    def stop(self) -> None:
        """Stop the Rich Live display (no-op when Rich is unavailable)."""
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    # Context-manager support
    def __enter__(self) -> "Dashboard":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Rich rendering (only called when Rich is available)
    # ------------------------------------------------------------------

    def _build_display(self) -> Any:  # pragma: no cover
        """Construct the Rich renderable for the current state.

        Returns:
            A Rich renderable (Panel containing a Table).
        """
        state = self.get_state()

        # Header summary
        header = Text()
        header.append(f"Tasks: {state['completed']}/{state['total']} done", style="bold green")
        header.append(f"  |  failed: {state['failed']}", style="bold red")
        header.append(f"  |  active: {state['active_count']}", style="bold cyan")
        elapsed = state["elapsed"]
        header.append(f"  |  elapsed: {elapsed:.1f}s", style="dim")
        if state["cookie_source"]:
            header.append(f"  |  cookie: {state['cookie_source']}", style="yellow")
        header.append(
            f"  |  API: {state['api_calls']} calls / {state['api_fails']} fail",
            style="dim",
        )

        # Active tasks table
        table = Table(box=box.SIMPLE, show_header=True, expand=True)
        table.add_column("Task ID", style="cyan", no_wrap=True)
        table.add_column("Status", style="bold")
        table.add_column("Progress")
        table.add_column("URL", overflow="fold")

        with self._lock:
            for tid, task in self._tasks.items():
                if task.status not in ("running", "pending"):
                    continue
                prog = self._task_progress.get(tid)
                prog_str = f"{prog[0]}/{prog[1]}" if prog else "-"
                status_style = "green" if task.status == "running" else "dim"
                table.add_row(
                    tid,
                    Text(task.status, style=status_style),
                    prog_str,
                    task.url,
                )

        # Recent done-log
        done_table = Table(box=box.SIMPLE, show_header=True, expand=True)
        done_table.add_column("", width=2)
        done_table.add_column("Label", overflow="fold")
        done_table.add_column("Detail")
        done_table.add_column("Trace ID", style="dim")

        with self._lock:
            recent = self._done_log[-10:]

        for entry in reversed(recent):
            icon = Text("[OK]", style="green") if entry.success else Text("[FAIL]", style="red")
            done_table.add_row(icon, entry.label, entry.detail, entry.trace_id)

        from rich.console import Group  # local import to avoid top-level dep

        return Panel(
            Group(header, "", table, "", done_table),
            subtitle="[dim]douyin-downloader[/dim]",
            expand=True,
        )
