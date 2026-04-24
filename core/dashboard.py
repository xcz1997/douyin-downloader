"""Rich Live dashboard with state tracking for the download pipeline.

Manages runtime state (active tasks, counters, cookie info, API metrics,
current download detail) and renders an interactive Rich Live display.
Degrades gracefully when Rich or psutil are unavailable.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.models import CookieState, DownloadTask

try:
    from rich.console import Console, Group
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


class _DoneEntry:
    __slots__ = ("label", "success", "detail", "trace_id", "ts")

    def __init__(
        self, label: str, success: bool, detail: str,
        trace_id: str, ts: float,
    ) -> None:
        self.label = label
        self.success = success
        self.detail = detail
        self.trace_id = trace_id
        self.ts = ts


class Dashboard:
    def __init__(
        self, total_tasks: int, concurrency: int,
        refresh_per_second: float = 4.0, done_log_size: int = 50,
    ) -> None:
        self._total: int = total_tasks
        self._concurrency: int = concurrency
        self._refresh_per_second: float = refresh_per_second
        self._done_log_size: int = done_log_size

        self._tasks: dict[str, DownloadTask] = {}
        self._task_progress: dict[str, tuple[int, int]] = {}

        self._completed: int = 0
        self._failed: int = 0
        self._api_calls: int = 0
        self._api_fails: int = 0

        self._done_log: list[_DoneEntry] = []
        self._cookie_state: CookieState | None = None
        self._start_time: float = time.monotonic()
        self._lock: threading.Lock = threading.Lock()

        self._live: Any | None = None
        self._console: Any | None = None

        self._current_item: dict | None = None
        self._total_bytes: int = 0
        self._status_message: str | None = None

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def add_task(self, task: DownloadTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def update_task(self, task: DownloadTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def update_progress(self, task: DownloadTask, done: int, total: int) -> None:
        with self._lock:
            self._task_progress[task.task_id] = (done, total)

    def update_file_progress(self, task: DownloadTask, done: int, total: int) -> None:
        self.update_progress(task, done, total)

    # ------------------------------------------------------------------
    # Current item tracking
    # ------------------------------------------------------------------

    def set_current_item(self, *, desc: str = "", author: str = "",
                         index: int = 0, total: int = 0) -> None:
        with self._lock:
            self._current_item = {
                "desc": desc, "author": author,
                "index": index, "total": total,
                "file_name": "", "bytes_done": 0, "bytes_total": 0,
                "file_start": time.monotonic(),
            }

    def update_bytes_progress(self, bytes_done: int, bytes_total: int,
                              file_name: str = "") -> None:
        with self._lock:
            if self._current_item is None:
                return
            if file_name and file_name != self._current_item.get("file_name"):
                self._current_item["file_start"] = time.monotonic()
            self._current_item["bytes_done"] = bytes_done
            self._current_item["bytes_total"] = bytes_total
            if file_name:
                self._current_item["file_name"] = file_name

    def clear_current_item(self) -> None:
        with self._lock:
            self._current_item = None

    def add_bytes(self, nbytes: int) -> None:
        with self._lock:
            self._total_bytes += nbytes

    def set_status(self, message: str) -> None:
        with self._lock:
            self._status_message = message

    def clear_status(self) -> None:
        with self._lock:
            self._status_message = None

    # ------------------------------------------------------------------
    # Completion logging
    # ------------------------------------------------------------------

    def log_done(
        self, label: str, success: bool, detail: str, *,
        trace_id: str = "",
    ) -> None:
        entry = _DoneEntry(
            label=label, success=success, detail=detail,
            trace_id=trace_id, ts=time.time(),
        )
        with self._lock:
            if success:
                self._completed += 1
            else:
                self._failed += 1
            self._done_log.append(entry)
            if len(self._done_log) > self._done_log_size:
                self._done_log = self._done_log[-self._done_log_size:]

    def log_item_done(
        self, label: str, success: bool, detail: str, *,
        trace_id: str = "",
    ) -> None:
        entry = _DoneEntry(
            label=label, success=success, detail=detail,
            trace_id=trace_id, ts=time.time(),
        )
        with self._lock:
            self._done_log.append(entry)
            if len(self._done_log) > self._done_log_size:
                self._done_log = self._done_log[-self._done_log_size:]

    # ------------------------------------------------------------------
    # API metrics
    # ------------------------------------------------------------------

    def record_api_call(self, success: bool) -> None:
        with self._lock:
            self._api_calls += 1
            if not success:
                self._api_fails += 1

    # ------------------------------------------------------------------
    # Cookie state
    # ------------------------------------------------------------------

    def set_cookie_state(self, state: CookieState) -> None:
        with self._lock:
            self._cookie_state = state

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            active = sum(1 for t in self._tasks.values() if t.status == "running")
            cookie_source = (
                self._cookie_state.source if self._cookie_state else None
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
        if not _RICH_AVAILABLE:
            return
        self._console = Console()
        self._live = Live(
            self._build_display(),
            console=self._console,
            refresh_per_second=self._refresh_per_second,
            auto_refresh=True,
        )
        self._live.start()

    def refresh(self) -> None:
        if self._live is None:
            return
        self._live.update(self._build_display(), refresh=False)

    def stop(self) -> None:
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    def __enter__(self) -> "Dashboard":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps <= 0:
            return "-"
        if bps < 1024:
            return f"{bps:.0f} B/s"
        if bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        return f"{bps / (1024 * 1024):.1f} MB/s"

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            m, s = divmod(int(seconds), 60)
            return f"{m}m{s:02d}s"
        h, rem = divmod(int(seconds), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h{m:02d}m"

    def _make_bar(self, frac: float, width: int = 30) -> Any:
        frac = max(0.0, min(1.0, frac))
        filled = int(width * frac)
        t = Text()
        if filled >= width:
            t.append("━" * width, style="bold green")
        elif filled > 0:
            t.append("━" * filled, style="bold cyan")
            t.append("╸", style="cyan")
            rest = width - filled - 1
            if rest > 0:
                t.append("─" * rest, style="dim")
        else:
            t.append("─" * width, style="dim")
        return t

    # ------------------------------------------------------------------
    # Rich rendering
    # ------------------------------------------------------------------

    def _build_display(self) -> Any:  # pragma: no cover
        state = self.get_state()
        parts: list[Any] = []

        # ---- Header: summary stats ----
        hdr = Text()
        hdr.append(f" 任务 {state['completed']}/{state['total']}", style="bold green")
        if state["failed"] > 0:
            hdr.append(f"  失败 {state['failed']}", style="bold red")
        hdr.append(
            f"  |  耗时 {self._fmt_duration(state['elapsed'])}", style="dim",
        )
        with self._lock:
            tb = self._total_bytes
        if tb > 0:
            avg_spd = tb / state["elapsed"] if state["elapsed"] > 0 else 0
            hdr.append(f"  |  ↓ {self._fmt_bytes(tb)}", style="cyan")
            if avg_spd > 0:
                hdr.append(f" ({self._fmt_speed(avg_spd)})", style="dim cyan")
        if state["cookie_source"]:
            hdr.append(
                f"  |  cookie: {state['cookie_source']}", style="dim yellow",
            )
        parts.append(hdr)

        # ---- Batch progress bar ----
        with self._lock:
            b_done, b_total = 0, 0
            for tid, task in self._tasks.items():
                if task.status == "running":
                    prog = self._task_progress.get(tid)
                    if prog:
                        b_done, b_total = prog
                    break

        if b_total > 0:
            pct = b_done / b_total
            bar = self._make_bar(pct, 40)
            ln = Text(" ")
            ln.append_text(bar)
            ln.append(f"  {b_done}/{b_total}", style="bold")
            ln.append(f" ({pct * 100:.1f}%)", style="dim")
            if 0 < pct < 1.0:
                eta = state["elapsed"] / pct - state["elapsed"]
                ln.append(f"  ~{self._fmt_duration(eta)}", style="dim")
            parts.append(ln)

        parts.append(Text(""))

        # ---- Current item detail ----
        with self._lock:
            item = dict(self._current_item) if self._current_item else None
            status_msg = self._status_message

        if item:
            ih = Text()
            ih.append(" ▶ ", style="bold cyan")
            if item["author"]:
                ih.append(f"@{item['author']}", style="bold")
                ih.append("  ", style="dim")
            if item["total"] > 0:
                ih.append(
                    f"作品 {item['index']}/{item['total']}", style="yellow",
                )
            parts.append(ih)

            if item["desc"]:
                parts.append(Text(f"   {item['desc']}", style="italic"))

            if item["bytes_total"] > 0:
                fpct = min(item["bytes_done"] / item["bytes_total"], 1.0)
                ftime = time.monotonic() - item["file_start"]
                spd = item["bytes_done"] / ftime if ftime > 0.1 else 0

                fl = Text("   ")
                if item["file_name"]:
                    fl.append(f"{item['file_name']}  ", style="dim")
                fl.append_text(self._make_bar(fpct, 20))
                fl.append(f" {fpct * 100:.0f}%", style="bold")
                fl.append(
                    f"  {self._fmt_bytes(item['bytes_done'])}"
                    f"/{self._fmt_bytes(item['bytes_total'])}",
                    style="dim",
                )
                if spd > 0:
                    fl.append(f"  {self._fmt_speed(spd)}", style="cyan")
                parts.append(fl)
            elif item["bytes_done"] > 0:
                fl = Text("   ")
                if item["file_name"]:
                    fl.append(f"{item['file_name']}  ", style="dim")
                fl.append(
                    f"↓ {self._fmt_bytes(item['bytes_done'])}", style="cyan",
                )
                parts.append(fl)
        elif status_msg:
            parts.append(Text(f" ⋯ {status_msg}", style="dim cyan"))

        parts.append(Text(""))

        # ---- Done log ----
        with self._lock:
            recent = list(self._done_log[-8:])

        if recent:
            parts.append(Text(" 最近完成", style="dim"))
            dtbl = Table(
                box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1),
            )
            dtbl.add_column("", width=4)
            dtbl.add_column("", overflow="fold")
            dtbl.add_column("", justify="right")
            for e in recent:
                icon = (
                    Text("  ✓ ", style="green")
                    if e.success
                    else Text("  ✗ ", style="red")
                )
                dtbl.add_row(icon, e.label, e.detail)
            parts.append(dtbl)

        return Panel(
            Group(*parts),
            subtitle="[dim]douyin-downloader[/dim]",
            expand=True,
        )
