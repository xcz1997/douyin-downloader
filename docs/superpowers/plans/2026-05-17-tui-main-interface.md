# TUI 主界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Textual 建一个终端 TUI 主界面，整合抖音/小红书下载、字幕提取、登录、设置；TUI 包在现有 `core/` 上（注入 `TextualSink` 复用 `DownloadPipeline`/`SubtitleRunner`），CLI 脚本保留、core 业务逻辑零改动。

**Architecture:** 新增 `tui/` 包 + `tui.py` 入口 + `core/progress.py`（`ProgressSink` Protocol，pipeline 实际调用的 Dashboard 方法面）。rich `Dashboard` 与新 `TextualSink` 各实现该 Protocol；TUI 在 Textual worker 里跑 core 任务、经线程安全机制把进度推到常驻日志/进度 widget。左侧导航 + 内容区 + 底部日志布局，分 4 期。

**Tech Stack:** Python 3.13、Textual（新增依赖，交互式 TUI）、pytest + pytest-asyncio（`@pytest.mark.asyncio`，strict 模式无配置文件）、PyYAML。参考 spec：`docs/superpowers/specs/2026-05-17-tui-main-interface-design.md`

---

## File Structure

```
core/progress.py            # 新增：ProgressSink @runtime_checkable Protocol（仅类型契约，不改逻辑）
tui/
  __init__.py
  sink.py                   # TextualSink：实现 ProgressSink，把调用转 dict 事件推给注入的回调
  widgets.py                # LogPane(RichLog 包装) + StatusBar（cookie/profile 指示灯 + 进度文本）
  app.py                    # DownloaderApp(textual.App)：侧栏导航 + ContentSwitcher + 底部 LogPane/StatusBar
  config_io.py              # save_config_fields()：最小 yaml 回写（镜像 ConfigLoader.save_cookie 既有模式）
  panels/
    __init__.py
    settings.py             # SettingsPanel
    download.py             # DownloadPanel
    subtitle.py             # SubtitlePanel
    login.py                # LoginPanel
tui.py                      # 入口：DownloaderApp().run()
requirements.txt            # 改：加 textual
tests/
  test_tui_progress_protocol.py
  test_tui_sink.py
  test_tui_config_io.py
  test_tui_app_smoke.py
  test_tui_download_panel.py
  test_tui_subtitle_panel.py
  test_tui_login_panel.py
```

既有契约不可破坏：`core/pipeline.py` `DownloadPipeline(config, registry, engine, cookie_mgr, tracer, logger, dashboard)` —— `dashboard` 是注入点，pipeline 只 duck-type 调用其方法面。`core/dashboard.py` 的 rich `Dashboard` 不改。全部 CLI 脚本不动。既有 ~210 测试须保持绿。

pipeline 实际调用的 dashboard 方法（已 grep 确认）：`add_task(task)`、`update_task(task)`、`update_progress(task,done,total)`、`set_current_item(*,desc,author,index,total)`、`update_bytes_progress(bytes_done,bytes_total,name)`、`clear_current_item()`、`add_bytes(nbytes)`、`set_status(message)`、`clear_status()`、`log_done(...)`、`log_item_done(...)`、`record_api_call(success)`、`set_cookie_state(state)`、`refresh()`。`downloader.py` 另用 `start()`、`stop()`、`get_state()`。Dashboard 还有 `update_file_progress(task,done,total)`。Protocol 覆盖这全集。

---

## PHASE 1 — 骨架（Protocol / Sink / App 壳 / 设置区）

### Task 1: core/progress.py —— ProgressSink Protocol + 一致性测试

**Files:**
- Create: `core/progress.py`
- Test: `tests/test_tui_progress_protocol.py`

- [ ] **Step 1: 先核对真实方法集（必做，不照搬本计划）**

Run: `grep -oE "self\._dashboard\.[a-z_]+" core/pipeline.py | sort -u` and `grep -oE "\.(start|stop|get_state)\(" downloader.py`
Expected: 与上文「pipeline 实际调用的 dashboard 方法」列表一致。若有出入，以代码为准调整 Protocol 方法集（保持与 `core/dashboard.py` 中对应方法的签名一致——`grep -n "def <name>" core/dashboard.py` 逐一核对签名）。

- [ ] **Step 2: 写失败测试**

Create `tests/test_tui_progress_protocol.py`:

```python
from core.progress import ProgressSink
from core.dashboard import Dashboard


def test_rich_dashboard_satisfies_progress_sink():
    # The existing rich Dashboard is the reference implementation of the
    # seam. If this fails, the Protocol drifted from what pipeline needs.
    d = Dashboard(total_tasks=1, concurrency=1)
    assert isinstance(d, ProgressSink)


def test_progress_sink_is_runtime_checkable():
    class Missing:
        pass

    assert not isinstance(Missing(), ProgressSink)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_progress_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.progress'`

- [ ] **Step 4: 实现**

Create `core/progress.py`:

```python
"""ProgressSink: the method surface DownloadPipeline + downloader.py drive.

The rich `Dashboard` (core/dashboard.py) is the reference implementation;
the TUI provides `tui.sink.TextualSink` implementing the same surface so
TUI and CLI share one backend. This is a typing contract only — it does
not change any behavior.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.models import CookieState, DownloadTask


@runtime_checkable
class ProgressSink(Protocol):
    def add_task(self, task: DownloadTask) -> None: ...
    def update_task(self, task: DownloadTask) -> None: ...
    def update_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None: ...
    def update_file_progress(
        self, task: DownloadTask, done: int, total: int
    ) -> None: ...
    def set_current_item(
        self, *, desc: str = "", author: str = "",
        index: int = 0, total: int = 0,
    ) -> None: ...
    def update_bytes_progress(
        self, bytes_done: int, bytes_total: int, name: str
    ) -> None: ...
    def clear_current_item(self) -> None: ...
    def add_bytes(self, nbytes: int) -> None: ...
    def set_status(self, message: str) -> None: ...
    def clear_status(self) -> None: ...
    def log_done(self, *args: Any, **kwargs: Any) -> None: ...
    def log_item_done(self, *args: Any, **kwargs: Any) -> None: ...
    def record_api_call(self, success: bool) -> None: ...
    def set_cookie_state(self, state: CookieState) -> None: ...
    def get_state(self) -> dict[str, Any]: ...
    def refresh(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

Note on `log_done`/`log_item_done`: their concrete signatures in `core/dashboard.py` span multiple lines (positional + keyword). `runtime_checkable` Protocol only checks method *presence*, not signature, so `*args, **kwargs` here is safe and keeps the Protocol from over-constraining. Do NOT try to mirror exact signatures (verified: runtime_checkable ignores them).

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_tui_progress_protocol.py -v`
Expected: PASS (2 passed) — the existing `Dashboard` already has all these methods.

- [ ] **Step 6: Commit**

```bash
git add core/progress.py tests/test_tui_progress_protocol.py
git commit -m "feat(tui): ProgressSink Protocol（pipeline↔TUI 接缝，rich Dashboard 已满足）"
```

---

### Task 2: tui/sink.py —— TextualSink

`TextualSink` 实现 `ProgressSink`，每个方法转成一个 `(kind, payload)` 事件，调用注入的 `emit` 回调。不依赖运行中的 Textual app（app 会注入一个把事件 `post_message` 的 emit），便于纯单元测试。

**Files:**
- Create: `tui/__init__.py`, `tui/sink.py`
- Test: `tests/test_tui_sink.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tui_sink.py`:

```python
from core.models import DownloadTask, CookieState
from core.progress import ProgressSink
from tui.sink import TextualSink


def _task():
    return DownloadTask(task_id="t1", trace_id="x", url="u", content_type="video")


def test_sink_satisfies_protocol():
    assert isinstance(TextualSink(lambda e: None), ProgressSink)


def test_emits_events_for_calls():
    events = []
    s = TextualSink(events.append)

    s.set_status("正在获取列表…")
    s.log_done("作品1", True, "3 文件")
    s.add_task(_task())
    s.update_bytes_progress(50, 100, "v.mp4")
    s.set_cookie_state(CookieState(value="c", source="config",
                                   obtained_at=0.0, platform="douyin"))

    kinds = [e["kind"] for e in events]
    assert kinds == [
        "status", "log", "add_task", "bytes_progress", "cookie_state"
    ]
    assert events[0]["payload"]["message"] == "正在获取列表…"
    assert events[3]["payload"] == {
        "bytes_done": 50, "bytes_total": 100, "name": "v.mp4"
    }
    assert events[4]["payload"]["platform"] == "douyin"


def test_get_state_returns_local_aggregate():
    s = TextualSink(lambda e: None)
    s.add_task(_task())
    st = s.get_state()
    assert st["total"] >= 1 and "completed" in st and "failed" in st


def test_noop_methods_do_not_raise():
    s = TextualSink(lambda e: None)
    s.start(); s.stop(); s.refresh(); s.clear_status()
    s.clear_current_item(); s.add_bytes(10); s.record_api_call(True)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_sink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui'`

- [ ] **Step 3: 实现**

Create `tui/__init__.py`:

```python
"""Textual TUI front-end. Wraps core/ — does not change core logic."""
```

Create `tui/sink.py`:

```python
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
        if task.status == "completed":
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tui_sink.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tui/__init__.py tui/sink.py tests/test_tui_sink.py
git commit -m "feat(tui): TextualSink 把 pipeline 回调转结构化事件"
```

---

### Task 3: tui/config_io.py —— 最小 yaml 回写

`ConfigLoader` 只有 `save_cookie`（无通用字段回写）。新增一个独立薄函数 `save_config_fields(path, updates)`：读原 yaml → 浅合并指定键 → `yaml.dump` 回写。镜像 `save_cookie` 的既有 yaml.dump 行为（含丢注释——既有项目行为，不在此"修"）。放 `tui/`（不进 `core/`，不动 core 逻辑）。

**Files:**
- Create: `tui/config_io.py`
- Test: `tests/test_tui_config_io.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tui_config_io.py`:

```python
import yaml

from tui.config_io import save_config_fields


def test_save_merges_and_preserves_other_keys(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("links: [a]\nsave_path: ./old\nthread: 5\n",
                 encoding="utf-8")

    save_config_fields(str(f), {"save_path": "./new", "thread": 8})

    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert data["save_path"] == "./new"
    assert data["thread"] == 8
    assert data["links"] == ["a"]  # untouched key preserved


def test_save_nested_key(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("links: []\nsubtitle:\n  enabled: false\n",
                 encoding="utf-8")

    save_config_fields(str(f), {"subtitle": {"enabled": True,
                                              "sources": ["ocr"]}})

    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert data["subtitle"]["enabled"] is True
    assert data["subtitle"]["sources"] == ["ocr"]


def test_save_creates_file_if_absent(tmp_path):
    f = tmp_path / "new.yml"
    save_config_fields(str(f), {"save_path": "./x"})
    assert yaml.safe_load(f.read_text(encoding="utf-8")) == {
        "save_path": "./x"
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_config_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui.config_io'`

- [ ] **Step 3: 实现**

Create `tui/config_io.py`:

```python
"""Minimal config.yml field write-back for the Settings panel.

Mirrors ConfigLoader.save_cookie's existing yaml.dump approach (which
also drops comments — known pre-existing project behavior; not "fixed"
here). Top-level keys are replaced wholesale by `updates` (shallow
merge): a dict value under a key fully replaces that key's value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def save_config_fields(path: str, updates: dict[str, Any]) -> None:
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.update(updates)
    p.write_text(
        yaml.dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tui_config_io.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tui/config_io.py tests/test_tui_config_io.py
git commit -m "feat(tui): config.yml 字段回写（镜像 save_cookie 既有 yaml 行为）"
```

---

### Task 4: tui/widgets.py + tui/app.py + tui.py —— App 壳 + 设置区

新增依赖 textual。App 壳：左侧导航（下载/字幕/登录/设置）+ `ContentSwitcher` 内容区 + 底部 `LogPane` + `StatusBar`。本任务实现壳 + **设置区**（读 `ConfigLoader.load()`、存 `save_config_fields`），其余面板用占位 `Static`，后续 Phase 替换。

**Files:**
- Modify: `requirements.txt`
- Create: `tui/widgets.py`, `tui/app.py`, `tui/panels/__init__.py`, `tui/panels/settings.py`, `tui.py`
- Test: `tests/test_tui_app_smoke.py`

- [ ] **Step 1: 装依赖 + 加 requirements**

Run: `python -m pip install "textual>=0.80"` then `python -c "import textual; print(textual.__version__)"`
Add to `requirements.txt` after the `rich==13.7.0` line:

```
textual>=0.80           # 交互式终端 TUI（主界面）
```

Confirm the installed Textual version, then verify these API names exist in it (do NOT assume — `python -c` check each): `from textual.app import App, ComposeResult`, `from textual.widgets import Static, ListView, ListItem, Label, Input, Button, RichLog`, `from textual.containers import Horizontal, Vertical`, `from textual.widgets import ContentSwitcher`. If any import path differs in the installed version, adjust imports in Steps 3-4 to the installed API (this is a real verification step, not a placeholder — Textual reorganizes occasionally).

- [ ] **Step 2: 写失败测试**

Create `tests/test_tui_app_smoke.py`:

```python
import pytest

from tui.app import DownloaderApp


@pytest.mark.asyncio
async def test_app_boots_and_has_four_nav_sections():
    app = DownloaderApp(config_path="config.yml")
    async with app.run_test() as pilot:
        # nav list has the 4 sections
        labels = app.nav_labels()
        assert labels == ["下载", "字幕", "登录", "设置"]
        await pilot.pause()


@pytest.mark.asyncio
async def test_switch_to_settings_shows_config_fields(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./JIN/\nthread: 5\n",
                   encoding="utf-8")
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()
        assert app.current_section == "设置"
        # settings panel exposes the loaded save_path
        assert app.settings_value("save_path") == "./JIN/"
```

- [ ] **Step 3: 实现 widgets + settings 面板**

Create `tui/widgets.py`:

```python
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
        super().__init__(id="statusbar")
        self._cookie = "?"
        self._profile = "?"
        self._status = ""
        self._render()

    def _render(self) -> None:
        self.update(
            f"cookie:{self._cookie}  profile:{self._profile}  "
            f"{self._status}"
        )

    def set_cookie(self, ok: bool) -> None:
        self._cookie = "✓" if ok else "✗"
        self._render()

    def set_profile(self, ok: bool) -> None:
        self._profile = "✓" if ok else "✗"
        self._render()

    def set_status(self, text: str) -> None:
        self._status = text
        self._render()
```

Create `tui/panels/__init__.py`:

```python
"""TUI section panels."""
```

Create `tui/panels/settings.py`:

```python
"""Settings panel: load key config.yml fields, edit, save back."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Static

from core.config import ConfigLoader
from tui.config_io import save_config_fields

# (config.yml key, label, caster) — flat keys only for the MVP form.
_FIELDS: list[tuple[str, str, type]] = [
    ("save_path", "保存目录", str),
    ("thread", "并发数", int),
    ("retry_times", "重试次数", int),
]


class SettingsPanel(Static):
    def __init__(self, config_path: str) -> None:
        super().__init__()
        self._config_path = config_path
        self._values: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        cfg = ConfigLoader(self._config_path).load()
        # AppConfig attribute names match these config keys 1:1.
        self._values = {
            "save_path": str(cfg.save_path),
            "thread": str(cfg.thread),
            "retry_times": str(cfg.retry_times),
        }

    def value(self, key: str) -> str:
        inp = self.query_one(f"#set-{key}", Input)
        return inp.value

    def compose(self) -> ComposeResult:
        with Vertical():
            for key, label, _ in _FIELDS:
                yield Label(label)
                yield Input(value=self._values.get(key, ""),
                            id=f"set-{key}")
            yield Button("保存", id="settings-save", variant="primary")
            yield Label("", id="settings-msg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "settings-save":
            return
        msg = self.query_one("#settings-msg", Label)
        try:
            updates: dict = {}
            for key, _, caster in _FIELDS:
                updates[key] = caster(self.value(key))
            save_config_fields(self._config_path, updates)
            msg.update("已保存（注意：yaml 回写会丢注释，项目既有行为）")
        except Exception as exc:  # never crash the app
            msg.update(f"保存失败: {exc}")
```

- [ ] **Step 4: 实现 app 壳 + 入口**

Create `tui/app.py`:

```python
"""DownloaderApp: sidebar nav + content switcher + persistent log/status.

Wraps core/ — does not change core logic. Download/Subtitle/Login panels
land in later phases; here they are placeholders.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, Label, Static

from tui.panels.settings import SettingsPanel
from tui.widgets import LogPane, StatusBar

_SECTIONS = ["下载", "字幕", "登录", "设置"]


class DownloaderApp(App):
    CSS = """
    #sidebar { width: 16; border-right: solid $accent; }
    #content { width: 1fr; }
    #logpane { height: 10; border-top: solid $accent; }
    #statusbar { height: 1; background: $panel; }
    """
    BINDINGS = [("q", "quit", "退出")]

    def __init__(self, config_path: str = "config.yml") -> None:
        super().__init__()
        self._config_path = config_path
        self.current_section = "下载"

    def nav_labels(self) -> list[str]:
        return list(_SECTIONS)

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal():
                lv = ListView(
                    *[ListItem(Label(s), id=f"nav-{i}")
                      for i, s in enumerate(_SECTIONS)],
                    id="sidebar",
                )
                yield lv
                with Vertical(id="content"):
                    yield SettingsPanel(self._config_path)  # mounted; shown on demand
                    yield Static("下载（Phase 2）", id="panel-下载")
                    yield Static("字幕（Phase 3）", id="panel-字幕")
                    yield Static("登录（Phase 4）", id="panel-登录")
            yield LogPane()
            yield StatusBar()

    def on_mount(self) -> None:
        self.show_section("下载")

    def show_section(self, name: str) -> None:
        self.current_section = name
        # Toggle panel visibility (MVP: simple display swap).
        for s in _SECTIONS:
            try:
                w = self.query_one(f"#panel-{s}", Static)
                w.display = (s == name)
            except Exception:
                pass
        sp = self.query_one(SettingsPanel)
        sp.display = (name == "设置")

    def settings_value(self, key: str) -> str:
        return self.query_one(SettingsPanel).value(key)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = int(event.item.id.split("-")[1])
        self.show_section(_SECTIONS[idx])
```

Create `tui.py`:

```python
"""TUI 主界面入口：python tui.py [-c config.yml]"""

import argparse

from tui.app import DownloaderApp


def main() -> None:
    ap = argparse.ArgumentParser(description="抖音下载器 TUI 主界面")
    ap.add_argument("-c", "--config", default="config.yml")
    args = ap.parse_args()
    DownloaderApp(config_path=args.config).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_tui_app_smoke.py -v`
Expected: PASS (2 passed).
If `app.run_test()` / `pilot.pause()` API differs in the installed Textual, adjust to the installed version's testing API (Textual ships `App.run_test()` — confirm signature via `python -c "import textual,inspect;from textual.app import App;print(inspect.signature(App.run_test))"`). Keep assertions identical; only adapt the harness call.
Then: `python -m pytest tests/ -q` — full suite, no regression (~210 + new).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tui/widgets.py tui/app.py tui/panels/__init__.py tui/panels/settings.py tui.py tests/test_tui_app_smoke.py
git commit -m "feat(tui): App 壳（侧栏导航+日志+状态栏）+ 设置区"
```

---

## PHASE 2 — 下载区

### Task 5: tui/panels/download.py —— 接 DownloadPipeline

下载面板：链接来源（config.yml / 手动 URL）、并发、"同时提取字幕"开关、开始/停止。开始 → Textual worker 构造 `DownloadPipeline`（注入 `TextualSink`，其 emit 用 `app.call_from_thread` 推 LogPane）→ 跑。XHS 走 `config.xhs.profile_dir`；注入模式强制 `interactive=False`（TUI 不调 input()）。测试用 mock 的 pipeline，不真下载。

**Files:**
- Create: `tui/panels/download.py`
- Modify: `tui/app.py`（用 DownloadPanel 替换 `#panel-下载` 占位）
- Test: `tests/test_tui_download_panel.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tui_download_panel.py`:

```python
import pytest

from tui.panels.download import DownloadPanel, build_pipeline_args


def test_build_pipeline_args_manual_url():
    args = build_pipeline_args(
        source="manual", manual_url="https://v.douyin.com/X",
        config_path="config.yml",
    )
    assert args["links"] == ["https://v.douyin.com/X"]
    assert args["interactive"] is False  # TUI never blocks on input()


def test_build_pipeline_args_config_source(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("links:\n  - https://v.douyin.com/A\nsave_path: ./x\n",
                   encoding="utf-8")
    args = build_pipeline_args(
        source="config", manual_url="", config_path=str(cfg),
    )
    assert args["links"] == ["https://v.douyin.com/A"]
    assert args["interactive"] is False


@pytest.mark.asyncio
async def test_panel_start_invokes_runner(monkeypatch):
    calls = {}

    async def fake_run(self, links, sink, interactive):
        calls["links"] = links
        calls["interactive"] = interactive
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    panel = DownloadPanel(config_path="config.yml")
    await panel.start_download(source="manual",
                               manual_url="https://v.douyin.com/Z")
    assert calls["links"] == ["https://v.douyin.com/Z"]
    assert calls["interactive"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_download_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui.panels.download'`

- [ ] **Step 3: 实现**

Create `tui/panels/download.py`:

```python
"""Download panel: drives core DownloadPipeline in a Textual worker via
TextualSink. TUI never blocks on input() — XHS injection mode is forced
non-interactive; persistent profile is used when config.xhs.profile_dir
is set.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from core.config import ConfigLoader


def build_pipeline_args(
    source: str, manual_url: str, config_path: str
) -> dict[str, Any]:
    """Resolve the link list + fixed TUI flags. Pure → unit-testable."""
    if source == "manual":
        links = [manual_url.strip()] if manual_url.strip() else []
    else:
        links = list(ConfigLoader(config_path).load().links)
    return {
        "links": links,
        "config_path": config_path,
        "interactive": False,  # TUI never calls input()
    }


class DownloadPanel(Static):
    def __init__(self, config_path: str = "config.yml") -> None:
        super().__init__()
        self._config_path = config_path
        self._worker = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with RadioSet(id="dl-source"):
                yield RadioButton("config.yml", value=True, id="src-config")
                yield RadioButton("手动输入 URL", id="src-manual")
            yield Input(placeholder="https://v.douyin.com/...",
                        id="dl-url")
            yield Button("开始下载", id="dl-start", variant="primary")
            yield Button("停止", id="dl-stop")
            yield Label("", id="dl-msg")

    async def start_download(self, source: str, manual_url: str) -> None:
        args = build_pipeline_args(source, manual_url, self._config_path)
        msg = self.query_one("#dl-msg", Label)
        if not args["links"]:
            msg.update("无链接：请选 config.yml 或输入 URL")
            return
        msg.update("下载中…")
        await self._run_download(
            args["links"], self._make_sink(), args["interactive"]
        )

    def _make_sink(self):
        from tui.sink import TextualSink

        app = self.app

        def emit(event):
            # thread-safe: hop to UI thread to touch widgets
            app.call_from_thread(self._on_event, event)

        return TextualSink(emit)

    def _on_event(self, event) -> None:
        from tui.widgets import LogPane

        log = self.app.query_one(LogPane)
        kind = event["kind"]
        p = event["payload"]
        if kind == "status":
            log.write(f"[b]{p['message']}[/b]")
        elif kind == "log":
            log.write(" ".join(str(a) for a in p.get("args", [])))
        elif kind in ("add_task", "update_task"):
            log.write(f"{kind}: {p}")

    async def _run_download(self, links, sink, interactive) -> None:
        """Construct and run the real DownloadPipeline. Heavy wiring lives
        here so tests can monkeypatch this method wholesale.

        Mirrors downloader.py's cmd_download assembly: ConfigLoader →
        CookieManager → DouyinAPIClient → DownloadEngine → registry →
        DownloadPipeline(dashboard=sink). XHS session, when links contain
        an XHS URL, is built with interactive=interactive (False here)
        and profile_dir=config.xhs.profile_dir or None.
        """
        import asyncio

        from core.api_client import DouyinAPIClient
        from core.cookie import CookieManager
        from core.downloader_engine import DownloadEngine
        from core.logger import DualLogger
        from core.pipeline import DownloadPipeline
        from core.platform import PlatformRegistry
        from core.platforms.douyin import (DouyinPlatform,
                                           DouyinPlatformClient)
        from core.platforms.xhs import XHSPlatform, XHSPlatformClient
        from core.platforms.xhs_browser import XHSBrowserSession
        from core.tracer import Tracer

        cfg = ConfigLoader(self._config_path).load()
        cfg.links = links
        from pathlib import Path
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        dl = DualLogger(log_dir=log_dir, console_level="INFO")
        tracer = Tracer(log_dir=log_dir, session_id="tui")
        cookie_mgr = CookieManager(cfg, tracer=tracer,
                                   logger=dl.get("cookie"))
        api = DouyinAPIClient(cookie_state=None, tracer=tracer,
                              logger=dl.get("api"), rate_limit=2.0,
                              max_retries=cfg.retry_times)
        engine = DownloadEngine(
            save_path=cfg.save_path, tracer=tracer,
            logger=dl.get("engine"), concurrency=cfg.thread,
            download_music=cfg.download.music,
            download_cover=cfg.download.cover,
            download_json=cfg.download.json,
        )
        registry = PlatformRegistry()
        registry.register(DouyinPlatform(), DouyinPlatformClient(api))
        xhs_platform = XHSPlatform()
        xhs_session = None
        has_xhs = any(xhs_platform.match_url(u) is not None for u in links)
        if not has_xhs:
            registry.register(xhs_platform, XHSPlatformClient(None))
        else:
            try:
                st = await cookie_mgr.ensure_valid_cookie(platform="xhs")
                xhs_session = XHSBrowserSession(
                    st.value, interactive=interactive,
                    profile_dir=cfg.xhs.profile_dir or None,
                )
                await xhs_session.start()
                registry.register(xhs_platform,
                                  XHSPlatformClient(xhs_session))
            except Exception as exc:
                sink.set_status(f"XHS 跳过（{exc}），抖音不受影响")
                registry.register(xhs_platform, XHSPlatformClient(None))
        pipeline = DownloadPipeline(
            config=cfg, registry=registry, engine=engine,
            cookie_mgr=cookie_mgr, tracer=tracer,
            logger=dl.get("pipeline"), dashboard=sink,
        )
        try:
            st = await cookie_mgr.ensure_valid_cookie(platform="douyin")
            api.update_cookie(st)
        except Exception as exc:
            sink.set_status(f"抖音 Cookie 获取失败: {exc}")
        try:
            await pipeline.run()
        except Exception as exc:
            sink.set_status(f"下载失败: {exc}")
        finally:
            await api.close()
            await engine.close()
            if xhs_session is not None:
                await xhs_session.close()
            tracer.close()
            dl.close()
            await asyncio.sleep(0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dl-start":
            src = ("manual"
                   if self.query_one("#src-manual", RadioButton).value
                   else "config")
            url = self.query_one("#dl-url", Input).value
            self._worker = self.run_worker(
                self.start_download(src, url), exclusive=True
            )
        elif event.button.id == "dl-stop":
            if self._worker is not None:
                self._worker.cancel()
                self.query_one("#dl-msg", Label).update("已停止")
```

In `tui/app.py`, replace `yield Static("下载（Phase 2）", id="panel-下载")` with:

```python
                    from tui.panels.download import DownloadPanel
                    dlp = DownloadPanel(self._config_path)
                    dlp.id = "panel-下载"
                    yield dlp
```

(Keep the rest of `compose()` and `show_section()` unchanged — `show_section` already toggles `#panel-下载` by id, which now resolves to the DownloadPanel.)

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python -m pytest tests/test_tui_download_panel.py -v`
Expected: PASS (3 passed).
Run: `python -m pytest tests/ -q`
Expected: full suite green, no regression. If `id` cannot be set after construction in the installed Textual, instead pass `id="panel-下载"` to `DownloadPanel.__init__` via `super().__init__(id=...)` — adjust DownloadPanel to accept and forward an `id` kwarg. Verify which the installed Textual supports and use that; keep behavior identical.

- [ ] **Step 5: Commit**

```bash
git add tui/panels/download.py tui/app.py tests/test_tui_download_panel.py
git commit -m "feat(tui): 下载区接 DownloadPipeline（worker+TextualSink，XHS 非交互）"
```

---

## PHASE 3 — 字幕区

### Task 6: tui/panels/subtitle.py —— 接 SubtitleRunner

字幕面板：路径/目录输入、源多选（track/ocr/asr）、asr 模型、开始/停止。`SubtitleRunner` 是同步阻塞 → Textual thread worker 跑；每视频一条结果（粒度限制如实标注）。测试 mock runner。

**Files:**
- Create: `tui/panels/subtitle.py`
- Modify: `tui/app.py`（替换 `#panel-字幕` 占位，与 Task 5 同手法）
- Test: `tests/test_tui_subtitle_panel.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tui_subtitle_panel.py`:

```python
import pytest

from tui.panels.subtitle import SubtitlePanel, build_runner_spec


def test_build_runner_spec_collects_sources():
    spec = build_runner_spec(
        path="/v/a.mp4", sources=["ocr", "asr"], asr_model="1.7b",
    )
    assert spec["path"] == "/v/a.mp4"
    assert spec["sources"] == ["ocr", "asr"]
    assert spec["asr_model"] == "1.7b"


def test_build_runner_spec_rejects_empty_sources():
    spec = build_runner_spec(path="/v/a.mp4", sources=[], asr_model="0.6b")
    assert spec["error"] == "未选择任何字幕源"


@pytest.mark.asyncio
async def test_panel_start_invokes_runner(monkeypatch):
    seen = {}

    def fake_run(self, spec):
        seen["spec"] = spec

    monkeypatch.setattr(
        "tui.panels.subtitle.SubtitlePanel._run_subtitle", fake_run
    )
    panel = SubtitlePanel()
    await panel.start_subtitle(
        path="/v/a.mp4", sources=["track"], asr_model="0.6b"
    )
    assert seen["spec"]["sources"] == ["track"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_subtitle_panel.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现**

Create `tui/panels/subtitle.py`:

```python
"""Subtitle panel: drives core SubtitleRunner in a Textual thread worker.

SubtitleRunner.run() is synchronous/blocking; progress granularity is
one result line per video (the runner exposes no finer callback — a
documented limitation, core is not changed for this).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Checkbox, Input, Label, Static

_SOURCES = ["track", "ocr", "asr"]


def build_runner_spec(
    path: str, sources: list[str], asr_model: str
) -> dict[str, Any]:
    """Pure resolver → unit-testable."""
    if not sources:
        return {"error": "未选择任何字幕源"}
    return {"path": path, "sources": sources, "asr_model": asr_model}


class SubtitlePanel(Static):
    def __init__(self) -> None:
        super().__init__()
        self._worker = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="视频文件或目录路径", id="sub-path")
            for s in _SOURCES:
                yield Checkbox(s, value=(s == "ocr"), id=f"sub-src-{s}")
            yield Input(value="0.6b", id="sub-asr-model")
            yield Button("开始提取", id="sub-start", variant="primary")
            yield Button("停止", id="sub-stop")
            yield Label("", id="sub-msg")

    async def start_subtitle(
        self, path: str, sources: list[str], asr_model: str
    ) -> None:
        spec = build_runner_spec(path, sources, asr_model)
        msg = self.query_one("#sub-msg", Label)
        if "error" in spec:
            msg.update(spec["error"])
            return
        msg.update("提取中…")
        self._run_subtitle(spec)

    def _run_subtitle(self, spec: dict) -> None:
        """Run SubtitleRunner in a thread worker. Tests monkeypatch this
        method wholesale so no real OCR/ASR runs."""
        from tui.widgets import LogPane

        log = self.app.query_one(LogPane)

        def work() -> None:
            from core.subtitle.ocr_source import OCRSource
            from core.subtitle.runner import SubtitleRunner

            impls = []
            if "ocr" in spec["sources"]:
                impls.append(OCRSource())
            if "track" in spec["sources"]:
                from core.subtitle.track_source import TrackSource
                impls.append(TrackSource())
            if "asr" in spec["sources"]:
                from core.subtitle.asr_source import ASRSource
                impls.append(ASRSource(model=spec["asr_model"]))
            runner = SubtitleRunner(impls, sources=spec["sources"])
            p = Path(spec["path"])
            vids = [p] if p.is_file() else sorted(p.rglob("*.mp4"))
            for v in vids:
                try:
                    written = runner.run(v, None)
                    names = ", ".join(x.name for x in written) or "无产出"
                    self.app.call_from_thread(
                        log.write, f"{v.name} → {names}"
                    )
                except Exception as exc:
                    self.app.call_from_thread(
                        log.write, f"[red]{v.name} 失败: {exc}[/red]"
                    )

        self._worker = self.run_worker(work, thread=True, exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sub-start":
            path = self.query_one("#sub-path", Input).value
            sources = [s for s in _SOURCES
                       if self.query_one(f"#sub-src-{s}", Checkbox).value]
            model = self.query_one("#sub-asr-model", Input).value
            self._worker = self.run_worker(
                self.start_subtitle(path, sources, model), exclusive=True
            )
        elif event.button.id == "sub-stop":
            if self._worker is not None:
                self._worker.cancel()
                self.query_one("#sub-msg", Label).update("已停止")
```

In `tui/app.py`, replace `yield Static("字幕（Phase 3）", id="panel-字幕")` with the SubtitlePanel (same id-assignment approach validated in Task 5):

```python
                    from tui.panels.subtitle import SubtitlePanel
                    sp2 = SubtitlePanel()
                    sp2.id = "panel-字幕"
                    yield sp2
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python -m pytest tests/test_tui_subtitle_panel.py -v`
Expected: PASS (3 passed). Then `python -m pytest tests/ -q` — no regression.

- [ ] **Step 5: Commit**

```bash
git add tui/panels/subtitle.py tui/app.py tests/test_tui_subtitle_panel.py
git commit -m "feat(tui): 字幕区接 SubtitleRunner（thread worker）"
```

---

## PHASE 4 — 登录区 + 状态指示灯

### Task 7: tui/panels/login.py —— 抖音/XHS 登录 + 指示灯

登录面板：两个按钮——抖音 `cloak_douyin_login.main()`、XHS `xhs_login.main()`（持久 profile）。worker 跑、stdout 经 `redirect_stdout` 进 LogPane。完成后刷新顶部 StatusBar 的 cookie/profile 指示灯。测试 mock 两个 main。

**Files:**
- Create: `tui/panels/login.py`
- Modify: `tui/app.py`（替换 `#panel-登录` 占位）
- Test: `tests/test_tui_login_panel.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_tui_login_panel.py`:

```python
import sys
import types

import pytest

from tui.panels.login import LoginPanel, StdoutToLog


def test_stdout_to_log_forwards_writes():
    lines = []
    s = StdoutToLog(lines.append)
    s.write("hello\n")
    s.write("world")
    s.flush()
    assert "hello" in "".join(lines)


@pytest.mark.asyncio
async def test_douyin_login_invokes_main(monkeypatch):
    called = {}

    async def fake_main(*a, **k):
        called["douyin"] = True

    fake_mod = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "cloak_douyin_login", fake_mod)

    panel = LoginPanel()
    await panel.run_douyin_login()
    assert called["douyin"] is True


@pytest.mark.asyncio
async def test_xhs_login_invokes_main(monkeypatch):
    called = {}

    async def fake_main(*a, **k):
        called["xhs"] = True

    fake_mod = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "xhs_login", fake_mod)

    panel = LoginPanel()
    await panel.run_xhs_login()
    assert called["xhs"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tui_login_panel.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现**

Create `tui/panels/login.py`:

```python
"""Login panel: runs the standalone cloak/xhs login coroutines in a
worker, piping their stdout into the persistent LogPane, then refreshes
the StatusBar cookie/profile indicators. TUI never blocks on input().
"""

from __future__ import annotations

import contextlib
import importlib
from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Static


class StdoutToLog:
    """Minimal file-like that forwards complete lines to a sink callable."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._sink(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._sink(self._buf)
            self._buf = ""


class LoginPanel(Static):
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Button("抖音扫码登录 (CloakBrowser)",
                         id="login-douyin", variant="primary")
            yield Button("XHS 持久 profile 登录",
                         id="login-xhs", variant="primary")
            yield Label("", id="login-msg")

    def _log(self, line: str) -> None:
        from tui.widgets import LogPane
        try:
            self.app.call_from_thread(
                self.app.query_one(LogPane).write, line
            )
        except Exception:
            pass

    async def run_douyin_login(self) -> None:
        mod = importlib.import_module("cloak_douyin_login")
        with contextlib.redirect_stdout(StdoutToLog(self._log)):
            try:
                await mod.main()
            except SystemExit:
                pass
        self._refresh_indicators()

    async def run_xhs_login(self) -> None:
        mod = importlib.import_module("xhs_login")
        with contextlib.redirect_stdout(StdoutToLog(self._log)):
            try:
                await mod.main()
            except SystemExit:
                pass
        self._refresh_indicators()

    def _refresh_indicators(self) -> None:
        from tui.widgets import StatusBar
        try:
            bar = self.app.query_one(StatusBar)
            from core.config import ConfigLoader
            cfg = ConfigLoader(
                getattr(self.app, "_config_path", "config.yml")
            ).load()
            bar.set_cookie(bool(cfg.cookies.get("douyin")))
            bar.set_profile(bool(cfg.xhs.profile_dir))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-douyin":
            self.run_worker(self.run_douyin_login(), exclusive=False)
        elif event.button.id == "login-xhs":
            self.run_worker(self.run_xhs_login(), exclusive=False)
```

In `tui/app.py`, replace `yield Static("登录（Phase 4）", id="panel-登录")` with the LoginPanel (same id approach as Task 5):

```python
                    from tui.panels.login import LoginPanel
                    lp = LoginPanel()
                    lp.id = "panel-登录"
                    yield lp
```

Also confirm `cfg.cookies` exists on AppConfig: Run `grep -n "cookies" core/models.py` — `AppConfig` has `cookies: dict[str, str]`. If the attribute name differs, use the real one in `_refresh_indicators`.

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python -m pytest tests/test_tui_login_panel.py -v`
Expected: PASS (3 passed). Then `python -m pytest tests/ -q` — no regression.

- [ ] **Step 5: Commit**

```bash
git add tui/panels/login.py tui/app.py tests/test_tui_login_panel.py
git commit -m "feat(tui): 登录区（cloak/xhs 登录 + cookie/profile 指示灯）"
```

---

### Task 8: README + 全量回归收尾

**Files:**
- Modify: `README.md`
- 全量验证

- [ ] **Step 1: README 补充**

Read `README.md`; find the configuration/usage area (Chinese `###` headings, near the 字幕提取 / XHS 反检测 subsections added earlier). Add:

````markdown
### TUI 主界面（推荐入口）

整合下载（抖音/小红书）、字幕提取、登录、设置于一个终端界面：

```bash
pip install textual
python tui.py            # 或 python tui.py -c config.yml
```

左侧导航切换区块，底部常驻日志/进度。TUI 与命令行脚本共用同一 `core/`
后端；`downloader.py` / `extract_text.py` / `xhs_login.py` /
`cloak_douyin_login.py` 仍可单独脚本化使用，行为不变。
````

Do not restructure other README content.

- [ ] **Step 2: 全量回归（gating）**

Run: `python -m pytest tests/ -q`
Expected: 全绿（既有 ~210 + 新增 tui 测试）。报告精确通过数。If a regression appears, STOP and report BLOCKED — do not commit.

Run: `python -m py_compile tui.py tui/app.py tui/sink.py tui/widgets.py tui/config_io.py tui/panels/settings.py tui/panels/download.py tui/panels/subtitle.py tui/panels/login.py core/progress.py`
Expected: success, no output.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(tui): README 补充 TUI 主界面入口与用法"
```

---

## Self-Review 结论

- **Spec 覆盖**：ProgressSink 接缝 → Task 1；TextualSink → Task 2；config 回写 → Task 3；App 壳+侧栏布局+设置区 → Task 4；下载区(pipeline 注入 sink、XHS interactive=False、profile_dir) → Task 5；字幕区(SubtitleRunner thread worker、每视频粒度) → Task 6；登录区+指示灯(cloak/xhs main、stdout 重定向、不调 input) → Task 7；README → Task 8；分 4 期 → Phase 1-4；错误隔离(worker 内 except、cloakbrowser 缺失提示、配置保存内联报错) → 各面板 `except` + Task 5 XHS 降级；不改 core 逻辑/不动脚本 → 仅新增 `tui/` + `core/progress.py`(纯 Protocol) + `tui/config_io.py`(不进 core)。spec §9 未决项：pipeline 方法集(Task 1 Step 1 grep 核对)、stdout 重定向(Task 7 StdoutToLog)、worker thread vs async(Task 5 async / Task 6 thread=True 已分别指定)。
- **占位符**：无。每步含完整代码/命令/预期。涉及 Textual 版本 API 的 Step（Task 4 Step 1/5、Task 5 Step 4）是真实"以安装版本核对"验证步（同既往 plan 处理外部库的方式），非占位。
- **类型一致**：`ProgressSink`/`TextualSink(emit)`/`Event=dict{kind,payload}`/`save_config_fields(path,updates)`/`DownloaderApp(config_path)`/`SettingsPanel(config_path)`/`DownloadPanel(config_path)`/`build_pipeline_args(source,manual_url,config_path)`/`build_runner_spec(path,sources,asr_model)`/`SubtitlePanel()`/`LoginPanel()`/`StdoutToLog(sink)` 全计划一致。
- **已知实现期确认**：Textual 安装版本的 import 路径与 `App.run_test`/`run_worker(thread=)`/widget `id` 赋值方式——各 Task 内已标注先以 `python -c`/`inspect` 核对再用；`cfg.cookies`/`cfg.xhs.profile_dir`/AppConfig 属性名以 grep 核对。
