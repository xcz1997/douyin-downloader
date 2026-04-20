# Downloader 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 downloader.py 从 1500+ 行单文件重构为 10 个模块化组件，新增全链路 Trace、双轨日志、Cookie 引导和 Rich 仪表盘。

**Architecture:** 三层架构 — 基础层（models/errors/tracer/logger）→ 能力层（config/cookie/api_client/engine）→ 编排层（pipeline/dashboard/入口）。apiproxy/ 保持不动，core/ 模块通过其公开接口调用。

**Tech Stack:** Python 3.11+, aiohttp, Rich, PyYAML, dataclasses, JSON Lines, SQLite (via existing apiproxy/douyin/database.py)

**Spec:** `docs/specs/2026-04-20-downloader-refactor-design.md`

---

## Phase 1: 基础层

### Task 1: 项目骨架 + 错误体系（core/errors.py）

**Files:**
- Create: `core/__init__.py`
- Create: `core/errors.py`
- Create: `tests/__init__.py`
- Create: `tests/test_errors.py`

- [ ] **Step 1: 创建 core 包骨架**

```bash
mkdir -p core tests
```

```python
# core/__init__.py
"""抖音下载器核心模块"""
```

```python
# tests/__init__.py
```

- [ ] **Step 2: 写 errors 测试**

```python
# tests/test_errors.py
from core.errors import (
    DouyinError, RetryableError, RateLimitError, NetworkError,
    CookieExpiredError, ConfigError,
    SkippableError, ContentNotFoundError, DownloadFileError,
)


def test_retryable_is_douyin_error():
    assert issubclass(RetryableError, DouyinError)


def test_rate_limit_is_retryable():
    assert issubclass(RateLimitError, RetryableError)
    e = RateLimitError("429 too many requests")
    assert isinstance(e, RetryableError)
    assert isinstance(e, DouyinError)


def test_network_error_is_retryable():
    assert issubclass(NetworkError, RetryableError)


def test_cookie_expired_is_not_retryable():
    e = CookieExpiredError("expired")
    assert isinstance(e, DouyinError)
    assert not isinstance(e, RetryableError)


def test_skippable_hierarchy():
    assert issubclass(ContentNotFoundError, SkippableError)
    assert issubclass(DownloadFileError, SkippableError)
    assert issubclass(SkippableError, DouyinError)
    assert not issubclass(SkippableError, RetryableError)


def test_config_error():
    e = ConfigError("missing links")
    assert isinstance(e, DouyinError)
    assert not isinstance(e, RetryableError)
    assert not isinstance(e, SkippableError)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.errors'`

- [ ] **Step 4: 实现 errors.py**

```python
# core/errors.py


class DouyinError(Exception):
    """所有自定义异常的基类"""


class RetryableError(DouyinError):
    """可自动重试的错误"""


class RateLimitError(RetryableError):
    """API 限流 (429)"""


class NetworkError(RetryableError):
    """网络超时/连接中断"""


class CookieExpiredError(DouyinError):
    """Cookie 失效，需要用户重新获取"""


class ConfigError(DouyinError):
    """配置文件错误"""


class SkippableError(DouyinError):
    """可跳过的错误，不影响其他任务"""


class ContentNotFoundError(SkippableError):
    """作品已删除或不可见"""


class DownloadFileError(SkippableError):
    """文件下载失败（所有 URL 均不可用）"""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_errors.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add core/__init__.py core/errors.py tests/__init__.py tests/test_errors.py
git commit -m "feat(core): 添加错误分类体系"
```

---

### Task 2: 数据模型（core/models.py）

**Files:**
- Create: `core/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写 models 测试**

```python
# tests/test_models.py
import time
from pathlib import Path
from core.models import (
    AppConfig, DownloadOptions, CookieState, TraceSpan,
    DownloadTask, DownloadResult,
)


def test_app_config_defaults():
    opts = DownloadOptions()
    assert opts.music is True
    assert opts.cover is True
    assert opts.json is True


def test_app_config_creation():
    cfg = AppConfig(
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
    assert cfg.links == ["https://example.com"]
    assert cfg.download.music is True


def test_cookie_state():
    cs = CookieState(value="abc=123", source="config", obtained_at=time.time())
    assert cs.is_valid is True
    assert cs.last_checked == 0


def test_trace_span_defaults():
    span = TraceSpan(
        trace_id="t_001", span_id="s_001", parent_id=None,
        name="test", start_time=time.time(),
    )
    assert span.status == "running"
    assert span.end_time is None
    assert span.attributes == {}
    assert span.events == []


def test_download_task_defaults():
    task = DownloadTask(
        task_id="task_001", trace_id="t_001",
        url="https://example.com", content_type="video",
    )
    assert task.status == "pending"
    assert task.file_paths == []
    assert task.error is None


def test_download_result():
    task = DownloadTask(
        task_id="task_001", trace_id="t_001",
        url="https://example.com", content_type="video",
    )
    result = DownloadResult(task=task, success=True, files_written=3, elapsed=1.5)
    assert result.success is True
    assert result.error is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 models.py**

```python
# core/models.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadOptions:
    music: bool = True
    cover: bool = True
    json: bool = True


@dataclass
class AppConfig:
    links: list[str]
    save_path: Path
    cookies: str | dict | None
    cookie_mode: str
    mode: list[str]
    number: dict
    start_time: str | None
    end_time: str | None
    download: DownloadOptions
    thread: int
    database: bool
    increase: dict
    retry_times: int
    log_level: str


@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    is_valid: bool = True
    last_checked: float = 0


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_time: float
    end_time: float | None = None
    status: str = "running"
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


@dataclass
class DownloadTask:
    task_id: str
    trace_id: str
    url: str
    content_type: str
    resolved_url: str | None = None
    extracted_id: str | None = None
    status: str = "pending"
    error: str | None = None
    file_paths: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class DownloadResult:
    task: DownloadTask
    success: bool
    files_written: int
    elapsed: float
    error: str | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_models.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add core/models.py tests/test_models.py
git commit -m "feat(core): 添加数据模型"
```

---

### Task 3: Trace 引擎（core/tracer.py）

**Files:**
- Create: `core/tracer.py`
- Create: `tests/test_tracer.py`

- [ ] **Step 1: 写 tracer 测试**

```python
# tests/test_tracer.py
import json
import time
from pathlib import Path
from core.tracer import Tracer


def test_start_trace_creates_root_span(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    span = tracer.start_trace("download_user", url="https://example.com")
    assert span.trace_id.startswith("t_")
    assert span.span_id.startswith("s_")
    assert span.parent_id is None
    assert span.name == "download_user"
    assert span.attributes["url"] == "https://example.com"


def test_start_span_inherits_trace_id(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    child = tracer.start_span(root, "child_op", key="value")
    assert child.trace_id == root.trace_id
    assert child.parent_id == root.span_id
    assert child.attributes["key"] == "value"


def test_end_span_writes_jsonl(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    tracer.end_span(root, status="ok", count=42)

    files = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(files) == 1
    with open(files[0]) as f:
        line = json.loads(f.readline())
    assert line["trace_id"] == root.trace_id
    assert line["status"] == "ok"
    assert line["attributes"]["count"] == 42
    assert "duration_ms" in line


def test_add_event(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    tracer.add_event(root, "cookie_checked", valid=True)
    assert len(root.events) == 1
    assert root.events[0]["event"] == "cookie_checked"
    assert root.events[0]["valid"] is True


def test_context_span_auto_close(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")

    with tracer.context_span(root, "child_op") as child:
        child.attributes["step"] = 1

    assert child.status == "ok"
    assert child.end_time is not None


def test_context_span_captures_exception(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")

    try:
        with tracer.context_span(root, "failing_op") as child:
            raise ValueError("boom")
    except ValueError:
        pass

    assert child.status == "error"
    assert "boom" in child.attributes.get("error", "")


def test_replay_builds_tree(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    child1 = tracer.start_span(root, "step_1")
    tracer.end_span(child1, status="ok")
    child2 = tracer.start_span(root, "step_2")
    tracer.end_span(child2, status="error", error="fail")
    tracer.end_span(root, status="ok")

    output = Tracer.replay(tmp_path, root.trace_id)
    assert "root" in output
    assert "step_1" in output
    assert "step_2" in output
    assert "error" in output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_tracer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 tracer.py**

```python
# core/tracer.py
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.models import TraceSpan


class Tracer:
    def __init__(self, log_dir: Path, session_id: str):
        self._log_dir = log_dir / "traces"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._file_path = self._log_dir / f"{time.strftime('%Y-%m-%d')}_session_{session_id}.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")

    def _gen_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def start_trace(self, name: str, url: str) -> TraceSpan:
        trace_id = self._gen_id("t")
        span = TraceSpan(
            trace_id=trace_id,
            span_id=self._gen_id("s"),
            parent_id=None,
            name=name,
            start_time=time.time(),
            attributes={"url": url},
        )
        return span

    def start_span(self, parent: TraceSpan, name: str, **attrs) -> TraceSpan:
        span = TraceSpan(
            trace_id=parent.trace_id,
            span_id=self._gen_id("s"),
            parent_id=parent.span_id,
            name=name,
            start_time=time.time(),
            attributes=dict(attrs),
        )
        return span

    def end_span(self, span: TraceSpan, status: str = "ok", **attrs):
        span.end_time = time.time()
        span.status = status
        span.attributes.update(attrs)
        self._write_span(span)

    def add_event(self, span: TraceSpan, event: str, **data):
        entry = {"time": time.time(), "event": event, **data}
        span.events.append(entry)

    @contextmanager
    def context_span(self, parent: TraceSpan, name: str, **attrs):
        span = self.start_span(parent, name, **attrs)
        try:
            yield span
            self.end_span(span, status="ok")
        except Exception as e:
            self.end_span(span, status="error", error=str(e))
            raise

    def _write_span(self, span: TraceSpan):
        record = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_id": span.parent_id,
            "name": span.name,
            "start": span.start_time,
            "end": span.end_time,
            "status": span.status,
            "duration_ms": round((span.end_time - span.start_time) * 1000, 1)
            if span.end_time
            else None,
            "attributes": span.attributes,
            "events": span.events,
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()

    @staticmethod
    def replay(log_dir: Path, trace_id: str) -> str:
        traces_dir = log_dir / "traces"
        spans = []
        for f in traces_dir.glob("*.jsonl"):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    record = json.loads(line)
                    if record["trace_id"] == trace_id:
                        spans.append(record)

        if not spans:
            return f"Trace {trace_id} not found"

        by_id = {s["span_id"]: s for s in spans}
        root_spans = [s for s in spans if s["parent_id"] is None]
        children = {}
        for s in spans:
            pid = s["parent_id"]
            if pid:
                children.setdefault(pid, []).append(s)

        lines = []

        def render(span, prefix="", is_last=True):
            dur = f"{span['duration_ms'] / 1000:.1f}s" if span.get("duration_ms") else "?"
            status = span["status"]
            attrs = " ".join(f"{k}={v}" for k, v in span["attributes"].items()
                            if k not in ("url",))
            connector = "└─" if is_last else "├─"
            if span["parent_id"] is None:
                lines.append(
                    f"Trace {span['trace_id']} | {span['name']} | {dur} | {status}"
                )
            else:
                lines.append(f"{prefix}{connector} {span['name']:20s} {dur:>6s}  {status:5s}  {attrs}")

            kids = children.get(span["span_id"], [])
            kids.sort(key=lambda s: s["start"])
            child_prefix = prefix + ("   " if is_last else "│  ")
            for i, kid in enumerate(kids):
                render(kid, child_prefix, i == len(kids) - 1)

            for evt in span.get("events", []):
                evt_data = {k: v for k, v in evt.items() if k not in ("time", "event")}
                evt_line = f"{prefix}{'   ' if is_last else '│  '}   └─ event: {evt['event']} {evt_data}"
                lines.append(evt_line)

        for rs in root_spans:
            render(rs)

        return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_tracer.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add core/tracer.py tests/test_tracer.py
git commit -m "feat(core): 添加全链路 Trace 引擎"
```

---

### Task 4: 双轨日志（core/logger.py）

**Files:**
- Create: `core/logger.py`
- Create: `tests/test_logger.py`

- [ ] **Step 1: 写 logger 测试**

```python
# tests/test_logger.py
import json
from pathlib import Path
from core.logger import DualLogger


def test_create_logger(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="INFO", file_level="DEBUG")
    log = dl.get("test_module")
    assert log is not None


def test_info_writes_to_file(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.info("hello world", extra_key="val")

    files = list((tmp_path / "app").glob("*.jsonl"))
    assert len(files) == 1
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["msg"] == "hello world"
    assert record["module"] == "mymod"
    assert record["level"] == "INFO"
    assert record["extra_key"] == "val"


def test_debug_hidden_from_console_by_default(tmp_path, capsys):
    dl = DualLogger(log_dir=tmp_path, console_level="INFO", file_level="DEBUG")
    log = dl.get("mymod")
    log.debug("should not appear in console")

    captured = capsys.readouterr()
    assert "should not appear" not in captured.out

    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["msg"] == "should not appear in console"


def test_bind_trace(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    bound = log.bind_trace("t_abc", "s_123")
    bound.info("with trace")

    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["trace_id"] == "t_abc"
    assert record["span_id"] == "s_123"


def test_warn_level(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.warn("retrying", attempt=2)

    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["level"] == "WARN"
    assert record["attempt"] == 2


def test_error_level(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.error("fatal", code=500)

    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["level"] == "ERROR"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_logger.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 logger.py**

```python
# core/logger.py
import json
import sys
import time
from pathlib import Path

_LEVEL_ORDER = {"TRACE": 0, "DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


class BoundLogger:
    def __init__(self, module: str, file_writer, console_level: int,
                 file_level: int, trace_id: str | None = None,
                 span_id: str | None = None):
        self._module = module
        self._file_writer = file_writer
        self._console_level = console_level
        self._file_level = file_level
        self._trace_id = trace_id
        self._span_id = span_id

    def bind_trace(self, trace_id: str, span_id: str | None = None) -> "BoundLogger":
        return BoundLogger(
            self._module, self._file_writer, self._console_level,
            self._file_level, trace_id, span_id,
        )

    def trace(self, msg: str, **kwargs):
        self._log("TRACE", msg, kwargs)

    def debug(self, msg: str, **kwargs):
        self._log("DEBUG", msg, kwargs)

    def info(self, msg: str, **kwargs):
        self._log("INFO", msg, kwargs)

    def warn(self, msg: str, **kwargs):
        self._log("WARN", msg, kwargs)

    def error(self, msg: str, **kwargs):
        self._log("ERROR", msg, kwargs)

    def _log(self, level: str, msg: str, extra: dict):
        level_num = _LEVEL_ORDER.get(level, 20)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")

        if level_num >= self._file_level:
            record = {"ts": ts, "level": level, "module": self._module, "msg": msg}
            if self._trace_id:
                record["trace_id"] = self._trace_id
            if self._span_id:
                record["span_id"] = self._span_id
            record.update(extra)
            self._file_writer(record)

        if level_num >= self._console_level:
            extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
            line = f"{ts[11:]} {level:<5s} [{self._module}] {msg}"
            if extra_str:
                line += f" ({extra_str})"
            print(line, file=sys.stderr)


class DualLogger:
    def __init__(self, log_dir: Path, console_level: str = "INFO",
                 file_level: str = "DEBUG"):
        self._app_dir = log_dir / "app"
        self._app_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._app_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")
        self._console_level = _LEVEL_ORDER.get(console_level, 20)
        self._file_level = _LEVEL_ORDER.get(file_level, 10)

    def get(self, module: str) -> BoundLogger:
        return BoundLogger(
            module, self._write_record, self._console_level, self._file_level,
        )

    def _write_record(self, record: dict):
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_logger.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add core/logger.py tests/test_logger.py
git commit -m "feat(core): 添加双轨日志系统"
```

---

## Phase 2: 能力层

### Task 5: 配置系统（core/config.py）

**Files:**
- Create: `core/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写 config 测试**

```python
# tests/test_config.py
import yaml
from pathlib import Path
from core.config import ConfigLoader
from core.models import AppConfig


def _write_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def test_load_new_format(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "links": ["https://example.com/video/123"],
        "save_path": str(tmp_path / "out"),
        "cookie": "abc=123",
        "mode": ["post"],
        "limit": {"post": 10},
        "time_range": {"start": "", "end": ""},
        "download": {"music": True, "cover": False, "metadata": True},
        "incremental": {"post": True},
        "concurrency": 3,
        "retry": 5,
        "database": True,
        "log_level": "DEBUG",
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert isinstance(config, AppConfig)
    assert config.links == ["https://example.com/video/123"]
    assert config.thread == 3
    assert config.retry_times == 5
    assert config.download.cover is False
    assert config.download.json is True
    assert config.log_level == "DEBUG"


def test_migrate_old_format(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "link": ["https://example.com/video/123"],
        "path": str(tmp_path / "out"),
        "cookies": "abc=123",
        "mode": ["post"],
        "number": {"post": 10},
        "start_time": "2026-01-01",
        "end_time": "2026-12-31",
        "json": True,
        "music": True,
        "cover": True,
        "thread": 5,
        "retry_times": 3,
        "database": True,
        "increase": {"post": True},
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://example.com/video/123"]
    assert config.save_path == Path(tmp_path / "out")
    assert config.thread == 5
    assert config.number == {"post": 10}
    assert config.start_time == "2026-01-01"


def test_single_link_becomes_list(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "links": "https://example.com/video/123",
        "save_path": str(tmp_path / "out"),
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://example.com/video/123"]


def test_validate_missing_links(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {"save_path": str(tmp_path / "out")})
    loader = ConfigLoader(str(cfg_path))
    errors = loader.validate()
    assert any("links" in e.lower() for e in errors)


def test_generate_default(tmp_path):
    cfg_path = tmp_path / "config.yml"
    ConfigLoader.generate_default(str(cfg_path))
    assert cfg_path.exists()
    with open(cfg_path) as f:
        data = yaml.safe_load(f)
    assert "links" in data
    assert "save_path" in data
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 config.py**

```python
# core/config.py
import yaml
from pathlib import Path
from core.models import AppConfig, DownloadOptions

_FIELD_MIGRATION = {
    "link": "links",
    "path": "save_path",
    "output_dir": "save_path",
    "cookies": "cookie",
    "thread": "concurrency",
    "retry_times": "retry",
    "number": "limit",
    "increase": "incremental",
    "start_time": "time_range_start",
    "end_time": "time_range_end",
}

_DEFAULT_CONFIG = {
    "links": [],
    "save_path": "./downloads",
    "cookie": "",
    "mode": ["post"],
    "limit": {"post": 0, "like": 0, "mix": 0},
    "time_range": {"start": "", "end": ""},
    "download": {"music": True, "cover": True, "metadata": True},
    "incremental": {"post": True, "like": True, "mix": True},
    "concurrency": 5,
    "retry": 3,
    "database": True,
    "log_level": "INFO",
}


class ConfigLoader:
    def __init__(self, config_path: str):
        self._path = Path(config_path)
        self._raw = {}
        self._migrations = []

    def load(self) -> AppConfig:
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._raw = yaml.safe_load(f) or {}

        self._migrate()
        merged = {**_DEFAULT_CONFIG, **self._raw}
        return self._build_config(merged)

    def validate(self) -> list[str]:
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._raw = yaml.safe_load(f) or {}
        self._migrate()
        merged = {**_DEFAULT_CONFIG, **self._raw}
        errors = []
        links = merged.get("links", [])
        if not links:
            errors.append("links 不能为空")
        return errors

    def _migrate(self):
        for old_key, new_key in _FIELD_MIGRATION.items():
            if old_key in self._raw and new_key.replace("_start", "").replace("_end", "") not in self._raw:
                val = self._raw.pop(old_key)
                if new_key == "time_range_start":
                    self._raw.setdefault("time_range", {})["start"] = val
                elif new_key == "time_range_end":
                    self._raw.setdefault("time_range", {})["end"] = val
                else:
                    self._raw[new_key] = val
                self._migrations.append(f"'{old_key}' → '{new_key}'")

        if "json" in self._raw:
            self._raw.setdefault("download", {})["metadata"] = self._raw.pop("json")
        if "music" in self._raw and "download" not in self._raw:
            self._raw["download"] = {}
        for key in ("music", "cover"):
            if key in self._raw and key not in self._raw.get("download", {}):
                self._raw.setdefault("download", {})[key] = self._raw.pop(key)

    def _build_config(self, data: dict) -> AppConfig:
        links = data.get("links", [])
        if isinstance(links, str):
            links = [links]

        dl = data.get("download", {})
        download = DownloadOptions(
            music=dl.get("music", True),
            cover=dl.get("cover", True),
            json=dl.get("metadata", True),
        )

        time_range = data.get("time_range", {})
        cookie_raw = data.get("cookie", "")
        if isinstance(cookie_raw, dict):
            cookie_mode = "dict"
        elif cookie_raw == "auto":
            cookie_mode = "auto"
        elif cookie_raw:
            cookie_mode = "string"
        else:
            cookie_mode = "none"

        return AppConfig(
            links=links,
            save_path=Path(data.get("save_path", "./downloads")),
            cookies=cookie_raw or None,
            cookie_mode=cookie_mode,
            mode=data.get("mode", ["post"]),
            number=data.get("limit", {"post": 0}),
            start_time=time_range.get("start") or None,
            end_time=time_range.get("end") or None,
            download=download,
            thread=data.get("concurrency", 5),
            database=data.get("database", True),
            increase=data.get("incremental", {"post": True}),
            retry_times=data.get("retry", 3),
            log_level=data.get("log_level", "INFO"),
        )

    def save_cookie(self, cookie: str):
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        data["cookie"] = cookie
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    @staticmethod
    def generate_default(path: str):
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(_DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add core/config.py tests/test_config.py
git commit -m "feat(core): 添加配置系统（含旧格式迁移）"
```

---

### Task 6: Cookie 管理（core/cookie.py）

**Files:**
- Create: `core/cookie.py`
- Create: `tests/test_cookie.py`

- [ ] **Step 1: 写 cookie 测试**

```python
# tests/test_cookie.py
import time
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from core.cookie import CookieManager
from core.models import AppConfig, DownloadOptions, CookieState


def _make_config(**overrides):
    defaults = dict(
        links=["https://example.com"],
        save_path="./dl",
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


def test_validate_required_fields():
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cookie.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 cookie.py**

```python
# core/cookie.py
import time
from pathlib import Path
from core.models import AppConfig, CookieState
from core.errors import CookieExpiredError

_REQUIRED_FIELDS = ["ttwid"]
_IMPORTANT_FIELDS = ["sessionid", "sessionid_ss", "passport_csrf_token", "msToken"]


class CookieManager:
    def __init__(self, config: AppConfig, tracer, logger):
        self._config = config
        self._tracer = tracer
        self._log = logger
        self._state: CookieState | None = None

    @property
    def state(self) -> CookieState | None:
        return self._state

    async def ensure_valid_cookie(self) -> CookieState:
        steps = [
            ("配置文件", self._try_config),
            ("本地浏览器", self._try_browser),
            ("Playwright", self._try_playwright),
            ("手动输入", self._try_manual),
        ]
        for name, fn in steps:
            if self._log:
                self._log.info(f"Cookie 检测: {name}...")
            result = await fn()
            if result and result.is_valid:
                self._state = result
                if self._log:
                    self._log.info(f"Cookie 有效", source=result.source)
                return result
            if self._log:
                self._log.debug(f"Cookie 检测: {name} 未通过")

        raise CookieExpiredError("无法获取有效 Cookie，所有方式均失败")

    async def _try_config(self) -> CookieState | None:
        state = self._state_from_config()
        if not state:
            return None
        valid, reason = await self.validate(state.value)
        state.is_valid = valid
        state.last_checked = time.time()
        return state if valid else None

    async def _try_browser(self) -> CookieState | None:
        cookie_str = self.extract_from_browser()
        if not cookie_str:
            return None
        valid, reason = await self.validate(cookie_str)
        if valid:
            return CookieState(
                value=cookie_str, source="browser",
                obtained_at=time.time(), is_valid=True,
                last_checked=time.time(),
            )
        return None

    async def _try_playwright(self) -> CookieState | None:
        try:
            from apiproxy.douyin.auth.cookie_manager import AutoCookieManager
            mgr = AutoCookieManager()
            cookies = mgr.get_cookies()
            if cookies:
                cookie_str = cookies if isinstance(cookies, str) else "; ".join(
                    f"{k}={v}" for k, v in cookies.items()
                )
                return CookieState(
                    value=cookie_str, source="playwright",
                    obtained_at=time.time(), is_valid=True,
                    last_checked=time.time(),
                )
        except ImportError:
            pass
        except Exception:
            pass
        return None

    async def _try_manual(self) -> CookieState | None:
        print("\n" + "=" * 50)
        print("请手动提供 Cookie:")
        print("  1. 打开浏览器访问 douyin.com 并登录")
        print("  2. 按 F12 → Network → 刷新页面")
        print("  3. 点击任意请求 → Headers → 复制 Cookie 值")
        print("=" * 50)
        try:
            cookie_str = input("\n粘贴 Cookie: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not cookie_str:
            return None
        parsed = self.parse_cookie_string(cookie_str)
        missing, warnings = self.check_cookie_fields(parsed)
        if missing:
            print(f"缺少必需字段: {', '.join(missing)}")
            return None
        for w in warnings:
            print(f"  ⚠️ 缺少推荐字段: {w}")
        return CookieState(
            value=cookie_str, source="manual",
            obtained_at=time.time(), is_valid=True,
            last_checked=time.time(),
        )

    async def validate(self, cookie_str: str) -> tuple[bool, str]:
        import aiohttp
        try:
            url = "https://www.douyin.com/aweme/v1/web/im/resources/?device_platform=webapp&aid=6383"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
                "Cookie": cookie_str,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 403:
                        return (False, "blocked")
                    data = await resp.json(content_type=None)
                    if data.get("status_code") == 0:
                        return (True, "ok")
                    return (False, f"status_code={data.get('status_code')}")
        except Exception as e:
            return (False, str(e))

    def _state_from_config(self) -> CookieState | None:
        raw = self._config.cookies
        if not raw:
            return None
        if isinstance(raw, dict):
            cookie_str = "; ".join(f"{k}={v}" for k, v in raw.items())
        else:
            cookie_str = str(raw).strip()
        if not cookie_str or cookie_str == "auto":
            return None
        return CookieState(
            value=cookie_str, source="config",
            obtained_at=time.time(),
        )

    def extract_from_browser(self) -> str | None:
        try:
            import browser_cookie3
            for browser_fn in [browser_cookie3.chrome, browser_cookie3.edge]:
                try:
                    cj = browser_fn(domain_name=".douyin.com")
                    cookies = {c.name: c.value for c in cj}
                    if cookies.get("ttwid"):
                        return "; ".join(f"{k}={v}" for k, v in cookies.items())
                except Exception:
                    continue
        except ImportError:
            pass
        return None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict:
        result = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, value = part.partition("=")
                result[key.strip()] = value.strip().strip('"')
        return result

    @staticmethod
    def check_cookie_fields(parsed: dict) -> tuple[list[str], list[str]]:
        missing = [f for f in _REQUIRED_FIELDS if f not in parsed]
        warnings = [f for f in _IMPORTANT_FIELDS if f not in parsed]
        return missing, warnings

    def save_to_config(self, state: CookieState):
        from core.config import ConfigLoader
        loader = ConfigLoader(str(self._config.save_path.parent / "config.yml"))
        loader.save_cookie(state.value)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cookie.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add core/cookie.py tests/test_cookie.py
git commit -m "feat(core): 添加 Cookie 交互式引导管理"
```

---

### Task 7: API 客户端（core/api_client.py）

**Files:**
- Create: `core/api_client.py`
- Create: `tests/test_api_client.py`

- [ ] **Step 1: 写 api_client 测试**

```python
# tests/test_api_client.py
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.api_client import DouyinAPIClient, RateLimiter
from core.models import CookieState, TraceSpan
from core.errors import RateLimitError, NetworkError, CookieExpiredError
from core.tracer import Tracer


def _make_cookie():
    return CookieState(value="ttwid=abc", source="test", obtained_at=0)


def _make_parent_span():
    return TraceSpan(
        trace_id="t_test", span_id="s_test", parent_id=None,
        name="test", start_time=0,
    )


@pytest.mark.asyncio
async def test_rate_limiter_spacing():
    limiter = RateLimiter(max_per_second=10.0)
    import time
    t0 = time.time()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.time() - t0
    assert elapsed >= 0.09


@pytest.mark.asyncio
async def test_client_creation(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test")
    from core.logger import DualLogger
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR")
    log = dl.get("test")
    client = DouyinAPIClient(
        cookie_state=_make_cookie(),
        tracer=tracer,
        logger=log,
    )
    assert client is not None
    await client.close()
    tracer.close()
    dl.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 api_client.py**

```python
# core/api_client.py
import asyncio
import json
import time
import copy
from pathlib import Path

import aiohttp

from core.models import CookieState, TraceSpan
from core.errors import (
    CookieExpiredError, RateLimitError, NetworkError, ContentNotFoundError,
)
from core.tracer import Tracer
from core.logger import BoundLogger

import apiproxy
from apiproxy.douyin.urls import Urls
from apiproxy.douyin.result import Result
from apiproxy.common.utils import Utils


class RateLimiter:
    def __init__(self, max_per_second: float = 2.0):
        self._interval = 1.0 / max_per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.time()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.time()


class DouyinAPIClient:
    def __init__(self, cookie_state: CookieState, tracer: Tracer,
                 logger: BoundLogger, rate_limit: float = 2.0,
                 max_retries: int = 3):
        self._cookie = cookie_state
        self._tracer = tracer
        self._log = logger
        self._rate_limiter = RateLimiter(rate_limit)
        self._max_retries = max_retries
        self._retry_delays = [1, 2, 5]
        self._session: aiohttp.ClientSession | None = None
        self._urls = Urls()
        self._result = Result()
        self._utils = Utils()

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )

    def _headers(self) -> dict:
        return {
            "User-Agent": apiproxy.ua,
            "Referer": "https://www.douyin.com/",
            "accept-encoding": "gzip, deflate",
            "Cookie": self._cookie.value,
        }

    async def _request(self, parent_span: TraceSpan, name: str,
                       url: str, parse_fn=None, **attrs) -> dict:
        await self._ensure_session()
        with self._tracer.context_span(parent_span, name, **attrs) as span:
            await self._rate_limiter.acquire()
            self._tracer.add_event(span, "rate_limit_passed")

            last_error = None
            for attempt in range(self._max_retries + 1):
                try:
                    span.attributes["attempt"] = attempt + 1
                    async with self._session.get(url, headers=self._headers()) as resp:
                        span.attributes["status_code"] = resp.status

                        if resp.status == 403:
                            self._tracer.add_event(span, "blocked", status=403)
                            raise CookieExpiredError("403 Forbidden")

                        if resp.status == 429:
                            self._tracer.add_event(span, "rate_limited", status=429)
                            raise RateLimitError("429 Too Many Requests")

                        if resp.status != 200:
                            raise NetworkError(f"HTTP {resp.status}")

                        text = await resp.text()
                        data = json.loads(text)

                        if parse_fn:
                            return parse_fn(data)
                        return data

                except (CookieExpiredError, RateLimitError):
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                    last_error = e
                    self._tracer.add_event(span, "retry", attempt=attempt + 1, error=str(e))
                    self._log.warn("请求重试", attempt=attempt + 1, error=str(e))
                    if attempt < self._max_retries:
                        delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                        await asyncio.sleep(delay)
                    else:
                        raise NetworkError(f"重试{self._max_retries}次后失败: {last_error}")

    async def get_video_info(self, aweme_id: str, parent_span: TraceSpan) -> dict:
        params = f"aweme_id={aweme_id}&device_platform=webapp&aid=6383"
        url = self._urls.POST_DETAIL + self._utils.getXbogus(params)

        def parse(data):
            detail = data.get("aweme_detail")
            if not detail:
                raise ContentNotFoundError(f"aweme_id={aweme_id} 未找到")
            self._result.clearDict(self._result.awemeDict)
            aweme_type = 1 if detail.get("images") else 0
            self._result.dataConvert(aweme_type, self._result.awemeDict, detail)
            return copy.deepcopy(self._result.awemeDict)

        return await self._request(parent_span, "api_get_video", url,
                                   parse_fn=parse, aweme_id=aweme_id)

    async def get_user_posts(self, sec_uid: str, cursor: int,
                             parent_span: TraceSpan) -> dict:
        params = (
            f"sec_user_id={sec_uid}&count=35&max_cursor={cursor}"
            f"&device_platform=webapp&aid=6383&channel=channel_pc_web"
            f"&pc_client_type=1&version_code=170400&version_name=17.4.0"
            f"&cookie_enabled=true&screen_width=1920&screen_height=1080"
            f"&browser_language=zh-CN&browser_platform=MacIntel"
            f"&browser_name=Chrome&browser_version=122.0.0.0"
        )
        url = self._urls.USER_POST + self._utils.getXbogus(params)
        return await self._request(parent_span, "api_user_posts", url,
                                   sec_uid=sec_uid, cursor=cursor)

    async def get_user_likes(self, sec_uid: str, cursor: int,
                             parent_span: TraceSpan) -> dict:
        params = (
            f"sec_user_id={sec_uid}&count=35&max_cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.USER_FAVORITE_A + self._utils.getXbogus(params)
        return await self._request(parent_span, "api_user_likes", url,
                                   sec_uid=sec_uid, cursor=cursor)

    async def get_mix_list(self, sec_uid: str, parent_span: TraceSpan) -> list[dict]:
        params = f"sec_user_id={sec_uid}&device_platform=webapp&aid=6383"
        url = self._urls.USER_MIX_LIST + self._utils.getXbogus(params)
        data = await self._request(parent_span, "api_mix_list", url, sec_uid=sec_uid)
        return data.get("mix_infos", [])

    async def get_mix_items(self, mix_id: str, cursor: int,
                            parent_span: TraceSpan) -> dict:
        params = (
            f"mix_id={mix_id}&count=35&cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.USER_MIX + self._utils.getXbogus(params)
        return await self._request(parent_span, "api_mix_items", url,
                                   mix_id=mix_id, cursor=cursor)

    async def get_music_items(self, music_id: str, cursor: int,
                              parent_span: TraceSpan) -> dict:
        params = (
            f"music_id={music_id}&count=35&cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.MUSIC + self._utils.getXbogus(params)
        return await self._request(parent_span, "api_music_items", url,
                                   music_id=music_id, cursor=cursor)

    def update_cookie(self, cookie_state: CookieState):
        self._cookie = cookie_state

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api_client.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add core/api_client.py tests/test_api_client.py
git commit -m "feat(core): 添加抖音 API 异步客户端"
```

---

### Task 8: 下载引擎（core/downloader_engine.py）

**Files:**
- Create: `core/downloader_engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: 写 engine 测试**

```python
# tests/test_engine.py
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from core.downloader_engine import DownloadEngine
from core.tracer import Tracer
from core.logger import DualLogger
from core.models import TraceSpan


@pytest.fixture
def engine_deps(tmp_path):
    tracer = Tracer(log_dir=tmp_path / "logs", session_id="test")
    dl = DualLogger(log_dir=tmp_path / "logs", console_level="ERROR")
    log = dl.get("test")
    save_path = tmp_path / "downloads"
    save_path.mkdir()
    return save_path, tracer, log, dl


def _make_parent_span():
    return TraceSpan(
        trace_id="t_test", span_id="s_test", parent_id=None,
        name="test", start_time=0,
    )


@pytest.mark.asyncio
async def test_download_file_skip_existing(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    target = save_path / "existing.jpg"
    target.write_text("fake image")
    parent = _make_parent_span()

    result = await engine.download_file(
        url="https://example.com/img.jpg",
        path=target,
        parent_span=parent,
    )
    assert result is True
    tracer.close()
    dl.close()


@pytest.mark.asyncio
async def test_build_save_dir(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    aweme = {
        "author": {"nickname": "testuser"},
        "desc": "test video",
        "create_time": 1700000000,
    }
    dir_path = engine._build_save_dir(aweme)
    assert "testuser" in str(dir_path)
    assert dir_path.exists()
    tracer.close()
    dl.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 downloader_engine.py**

```python
# core/downloader_engine.py
import asyncio
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from core.models import TraceSpan, DownloadResult, DownloadTask
from core.errors import DownloadFileError
from core.tracer import Tracer
from core.logger import BoundLogger


class DownloadEngine:
    def __init__(self, save_path: Path, tracer: Tracer, logger: BoundLogger,
                 concurrency: int = 5, download_music: bool = True,
                 download_cover: bool = True, download_json: bool = True):
        self._save_path = save_path
        self._tracer = tracer
        self._log = logger
        self._semaphore = asyncio.Semaphore(concurrency)
        self._session: aiohttp.ClientSession | None = None
        self._download_music = download_music
        self._download_cover = download_cover
        self._download_json = download_json

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
            )

    def _build_save_dir(self, aweme: dict) -> Path:
        author = aweme.get("author", {}).get("nickname", "unknown")
        desc = (aweme.get("desc") or "")[:50].replace("/", "_").replace("\\", "_")
        raw_time = aweme.get("create_time")
        if isinstance(raw_time, (int, float)):
            dt = datetime.fromtimestamp(raw_time)
        else:
            dt = datetime.now()
        ts = dt.strftime("%Y-%m-%d_%H-%M-%S")
        folder = f"{ts}_{desc}" if desc else ts
        path = self._save_path / author / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def download_media(self, aweme: dict, parent_span: TraceSpan) -> DownloadResult:
        task = DownloadTask(
            task_id=aweme.get("aweme_id", "unknown"),
            trace_id=parent_span.trace_id,
            url="",
            content_type="image" if aweme.get("images") else "video",
        )
        t0 = time.time()
        save_dir = self._build_save_dir(aweme)
        files_written = 0
        success = True
        folder_name = save_dir.name

        if aweme.get("images"):
            images = aweme.get("images", [])
            for i, img in enumerate(images):
                url_list = img.get("url_list", [])
                if url_list:
                    path = save_dir / f"image_{i+1}.jpg"
                    ok = await self.download_file(url_list[0], path, parent_span,
                                                  fallback_urls=url_list[1:])
                    if ok:
                        files_written += 1
                    else:
                        success = False
                    await asyncio.sleep(0.3)
        else:
            video_url = self._get_video_url(aweme)
            if video_url:
                fallbacks = self._get_video_fallbacks(aweme)
                path = save_dir / f"{folder_name}.mp4"
                ok = await self.download_file(video_url, path, parent_span,
                                              fallback_urls=fallbacks)
                if ok:
                    files_written += 1
                else:
                    success = False

            if self._download_music:
                music_url = self._get_music_url(aweme)
                if music_url:
                    path = save_dir / f"{folder_name}_music.mp3"
                    if await self.download_file(music_url, path, parent_span):
                        files_written += 1

        if self._download_cover:
            cover_url = self._get_cover_url(aweme)
            if cover_url:
                path = save_dir / f"{folder_name}_cover.jpg"
                if await self.download_file(cover_url, path, parent_span):
                    files_written += 1

        if self._download_json:
            import json
            json_path = save_dir / f"{folder_name}_data.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(aweme, f, ensure_ascii=False, indent=2)
            files_written += 1

        task.file_paths = [str(save_dir)]
        return DownloadResult(
            task=task, success=success,
            files_written=files_written, elapsed=time.time() - t0,
        )

    async def download_file(self, url: str, path: Path,
                            parent_span: TraceSpan,
                            fallback_urls: list[str] | None = None) -> bool:
        if path.exists() and path.stat().st_size > 0:
            self._tracer.add_event(parent_span, "file_skip", path=str(path.name))
            return True

        await self._ensure_session()
        async with self._semaphore:
            all_urls = [url] + (fallback_urls or [])
            for i, u in enumerate(all_urls):
                try:
                    u = u.replace("playwm", "play")
                    async with self._session.get(u) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                            self._log.debug("文件已下载", file=path.name,
                                            size_kb=len(data) // 1024)
                            return True
                        elif resp.status == 403 and i < len(all_urls) - 1:
                            continue
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if i < len(all_urls) - 1:
                        continue

        self._log.warn("文件下载失败", file=path.name, urls_tried=len(all_urls))
        return False

    def _get_video_url(self, aweme: dict) -> str | None:
        for key in ("play_addr_h264", "play_addr"):
            addr = aweme.get("video", {}).get(key)
            if addr and addr.get("url_list"):
                return addr["url_list"][0].replace("playwm", "play")
        return None

    def _get_video_fallbacks(self, aweme: dict) -> list[str]:
        urls = []
        for key in ("play_addr", "play_addr_h264", "download_addr"):
            addr = aweme.get("video", {}).get(key)
            if addr and addr.get("url_list"):
                urls.extend(addr["url_list"])
        return urls

    def _get_music_url(self, aweme: dict) -> str | None:
        music = aweme.get("music", {})
        play_url = music.get("play_url", {})
        if isinstance(play_url, dict):
            url_list = play_url.get("url_list", [])
            return url_list[0] if url_list else None
        return play_url if isinstance(play_url, str) else None

    def _get_cover_url(self, aweme: dict) -> str | None:
        cover = aweme.get("video", {}).get("cover", {})
        url_list = cover.get("url_list", [])
        return url_list[0] if url_list else None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_engine.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add core/downloader_engine.py tests/test_engine.py
git commit -m "feat(core): 添加异步下载引擎"
```

---

## Phase 3: 编排层

### Task 9: 仪表盘（core/dashboard.py）

**Files:**
- Create: `core/dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: 写 dashboard 测试**

```python
# tests/test_dashboard.py
from core.dashboard import Dashboard
from core.models import DownloadTask, CookieState
import time


def test_dashboard_creation():
    db = Dashboard(total_tasks=5, concurrency=3)
    assert db is not None


def test_add_and_update_task():
    db = Dashboard(total_tasks=2, concurrency=2)
    task = DownloadTask(
        task_id="t1", trace_id="tr1",
        url="https://example.com", content_type="user",
    )
    db.add_task(task)
    task.status = "running"
    db.update_task(task)
    state = db.get_state()
    assert state["active_count"] >= 0


def test_log_done():
    db = Dashboard(total_tasks=1, concurrency=1)
    db.log_done("test video", True, "3 files", trace_id="t_001")
    state = db.get_state()
    assert state["completed"] == 1


def test_log_done_failure():
    db = Dashboard(total_tasks=1, concurrency=1)
    db.log_done("bad video", False, "API error", trace_id="t_002")
    state = db.get_state()
    assert state["failed"] == 1


def test_set_cookie_state():
    db = Dashboard(total_tasks=1, concurrency=1)
    cs = CookieState(value="abc", source="config", obtained_at=time.time())
    db.set_cookie_state(cs)
    state = db.get_state()
    assert state["cookie_source"] == "config"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 dashboard.py**

```python
# core/dashboard.py
import os
import time

from core.models import DownloadTask, CookieState


class Dashboard:
    def __init__(self, total_tasks: int, concurrency: int):
        self._total = total_tasks
        self._concurrency = concurrency
        self._tasks: dict[str, DownloadTask] = {}
        self._active: dict[str, dict] = {}
        self._done_log: list[str] = []
        self._completed = 0
        self._failed = 0
        self._api_calls = 0
        self._api_fails = 0
        self._cookie_state: CookieState | None = None
        self._start_time = time.time()
        self._live = None

    def set_cookie_state(self, state: CookieState):
        self._cookie_state = state

    def add_task(self, task: DownloadTask):
        self._tasks[task.task_id] = task

    def update_task(self, task: DownloadTask):
        self._tasks[task.task_id] = task
        if task.status == "running":
            self._active[task.task_id] = {
                "task": task, "current": 0, "total": 0,
            }
        elif task.status in ("done", "failed", "skipped"):
            self._active.pop(task.task_id, None)

    def update_progress(self, task: DownloadTask, current: int, total: int):
        if task.task_id in self._active:
            self._active[task.task_id]["current"] = current
            self._active[task.task_id]["total"] = total

    def update_file_progress(self, aweme_desc: str, file_type: str,
                             current: int, total: int, speed: float = 0):
        pass

    def record_api_call(self, success: bool):
        self._api_calls += 1
        if not success:
            self._api_fails += 1

    def log_done(self, desc: str, success: bool, detail: str, trace_id: str = None):
        if success:
            self._completed += 1
            entry = f"[green]  ✓ {desc} ({detail})[/green]"
        else:
            self._failed += 1
            entry = f"[red]  ✗ {desc} ({detail})"
            if trace_id:
                entry += f" trace={trace_id}"
            entry += "[/red]"
        self._done_log.append(entry)
        if len(self._done_log) > 8:
            self._done_log = self._done_log[-8:]

    def get_state(self) -> dict:
        return {
            "total": self._total,
            "completed": self._completed,
            "failed": self._failed,
            "active_count": len(self._active),
            "api_calls": self._api_calls,
            "api_fails": self._api_fails,
            "cookie_source": self._cookie_state.source if self._cookie_state else None,
            "elapsed": time.time() - self._start_time,
        }

    def start(self):
        try:
            import psutil
            from rich.live import Live
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text
            from rich.console import Group
            self._has_rich = True
        except ImportError:
            self._has_rich = False
            return

        self._live = Live(self._build_display(), refresh_per_second=4)
        self._live.start()

    def refresh(self):
        if self._live:
            self._live.update(self._build_display())

    def stop(self):
        if self._live:
            self._live.stop()
            self._live = None

    def _build_display(self):
        import psutil
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        from rich.console import Group

        elapsed = time.time() - self._start_time
        mem = psutil.virtual_memory()
        mem_gb = mem.used / (1024 ** 3)
        mem_total = mem.total / (1024 ** 3)
        cpu = psutil.cpu_percent(interval=0)

        status = Text()
        status.append(f"⏱ {elapsed:.0f}s", style="cyan")
        cookie_info = self._cookie_state.source if self._cookie_state else "未配置"
        cookie_style = "green" if self._cookie_state and self._cookie_state.is_valid else "red"
        status.append(f"  │  🧠 {mem_gb:.1f}/{mem_total:.0f}GB ({mem.percent}%)",
                      style="yellow" if mem.percent > 70 else "green")
        status.append(f"  │  💻 CPU {cpu:.0f}%",
                      style="yellow" if cpu > 80 else "green")
        status.append(f"  │  🍪 {cookie_info}", style=cookie_style)
        status.append(f"  │  📡 API {self._api_calls}次/{self._api_fails}失败", style="white")
        status.append(f"  │  📊 {self._completed}✓ {self._failed}✗ / {self._total}总", style="white")

        task_table = Table(show_header=True, header_style="bold cyan", expand=True, padding=(0, 1))
        task_table.add_column("#", width=4, justify="center")
        task_table.add_column("类型", width=6, justify="center")
        task_table.add_column("目标", ratio=3)
        task_table.add_column("进度", width=20)
        task_table.add_column("状态", width=8)

        for tid, info in list(self._active.items())[:self._concurrency]:
            task = info["task"]
            current, total = info["current"], info["total"]
            if total > 0:
                pct = current / total * 100
                bar_len = 12
                filled = int(bar_len * pct / 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                progress = f"{bar} {current}/{total}"
            else:
                progress = "准备中..."
            desc = task.url[:40]
            task_table.add_row(
                task.task_id[:4], task.content_type.upper(),
                desc, progress, task.status,
            )

        log_text = "\n".join(self._done_log[-6:]) if self._done_log else "[dim]等待中...[/dim]"

        return Group(
            status, "",
            Panel(task_table, title=f"任务队列 ({len(self._active)}/{self._concurrency})", border_style="blue"),
            Panel(log_text, title=f"已完成 ({self._completed + self._failed}/{self._total})", border_style="green"),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add core/dashboard.py tests/test_dashboard.py
git commit -m "feat(core): 添加 Rich 实时仪表盘"
```

---

### Task 10: 管线调度器（core/pipeline.py）

**Files:**
- Create: `core/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: 写 pipeline 测试**

```python
# tests/test_pipeline.py
import pytest
import re
from core.pipeline import DownloadPipeline


def test_resolve_short_url_pattern():
    assert DownloadPipeline.is_short_url("https://v.douyin.com/abc123/")
    assert not DownloadPipeline.is_short_url("https://www.douyin.com/video/123")


def test_detect_content_type():
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/video/123") == "video"
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/note/123") == "image"
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/user/MS4wLjAB") == "user"
    assert DownloadPipeline.detect_content_type("https://www.iesdouyin.com/share/user/MS4wLjAB") == "user"


def test_extract_video_id():
    assert DownloadPipeline.extract_id("https://www.douyin.com/video/7123456789", "video") == "7123456789"


def test_extract_user_id():
    uid = DownloadPipeline.extract_id(
        "https://www.iesdouyin.com/share/user/MS4wLjABAAAAtest?foo=bar", "user"
    )
    assert uid == "MS4wLjABAAAAtest"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 pipeline.py**

```python
# core/pipeline.py
import asyncio
import re
import time
import traceback

import aiohttp

from core.models import AppConfig, DownloadTask, TraceSpan
from core.errors import CookieExpiredError, SkippableError, RetryableError
from core.tracer import Tracer
from core.logger import BoundLogger
from core.api_client import DouyinAPIClient
from core.downloader_engine import DownloadEngine
from core.cookie import CookieManager
from core.dashboard import Dashboard

_SHORT_URL_RE = re.compile(r"https?://v\.douyin\.com/\w+")
_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")
_NOTE_RE = re.compile(r"douyin\.com/note/(\d+)")
_USER_RE = re.compile(r"(?:sec_uid=|/user/)(MS4wLjAB[\w\-]+)")
_MIX_RE = re.compile(r"mix_id=(\d+)|/collection/(\d+)")
_MUSIC_RE = re.compile(r"music/(\d+)")


class DownloadPipeline:
    def __init__(self, config: AppConfig, api: DouyinAPIClient,
                 engine: DownloadEngine, cookie_mgr: CookieManager,
                 tracer: Tracer, logger: BoundLogger, dashboard: Dashboard):
        self._config = config
        self._api = api
        self._engine = engine
        self._cookie_mgr = cookie_mgr
        self._tracer = tracer
        self._log = logger
        self._dashboard = dashboard

    async def run(self):
        session_span = self._tracer.start_trace("session", url="batch")

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            cookie_state = await self._cookie_mgr.ensure_valid_cookie()
            cs.attributes["source"] = cookie_state.source
            self._api.update_cookie(cookie_state)
            self._dashboard.set_cookie_state(cookie_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")

        for task in tasks:
            self._dashboard.add_task(task)

        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

    async def _prepare_tasks(self, parent_span: TraceSpan) -> list[DownloadTask]:
        tasks = []
        for i, url in enumerate(self._config.links):
            with self._tracer.context_span(parent_span, "prepare_url", url=url) as span:
                resolved = url
                if self.is_short_url(url):
                    resolved = await self._resolve_short_url(url)
                    span.attributes["resolved"] = resolved

                content_type = self.detect_content_type(resolved)
                extracted_id = self.extract_id(resolved, content_type)
                span.attributes["type"] = content_type
                span.attributes["id"] = extracted_id

                task = DownloadTask(
                    task_id=f"task_{i:03d}",
                    trace_id=parent_span.trace_id,
                    url=url,
                    content_type=content_type,
                    resolved_url=resolved,
                    extracted_id=extracted_id,
                )
                tasks.append(task)
        return tasks

    async def _execute_task(self, task: DownloadTask):
        root = self._tracer.start_trace(f"download_{task.content_type}", url=task.url)
        task.trace_id = root.trace_id
        task.status = "running"
        self._dashboard.update_task(task)

        try:
            match task.content_type:
                case "video" | "image":
                    await self._handle_single(task, root)
                case "user":
                    await self._handle_user(task, root)
                case "mix":
                    await self._handle_mix(task, root)
                case "music":
                    await self._handle_music(task, root)
            task.status = "done"
            self._dashboard.log_done(
                task.url[:50], True,
                f"{task.stats.get('downloaded', 0)} 个作品",
                root.trace_id,
            )

        except CookieExpiredError:
            self._tracer.add_event(root, "cookie_expired")
            self._log.warn("Cookie 失效，重新获取...")
            cookie = await self._cookie_mgr.ensure_valid_cookie()
            self._api.update_cookie(cookie)
            self._dashboard.set_cookie_state(cookie)
            await self._execute_task(task)
            return

        except SkippableError as e:
            task.status = "failed"
            task.error = str(e)
            self._dashboard.log_done(task.url[:50], False, str(e), root.trace_id)

        except RetryableError as e:
            task.status = "failed"
            task.error = str(e)
            self._dashboard.log_done(task.url[:50], False, f"重试耗尽: {e}", root.trace_id)

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            self._log.error("未预期错误", error=str(e), tb=traceback.format_exc())
            self._dashboard.log_done(task.url[:50], False, str(e), root.trace_id)

        finally:
            self._tracer.end_span(root, status=task.status)
            self._dashboard.update_task(task)

    async def _handle_single(self, task: DownloadTask, root: TraceSpan):
        with self._tracer.context_span(root, "fetch_info", aweme_id=task.extracted_id) as span:
            info = await self._api.get_video_info(task.extracted_id, span)
            self._dashboard.record_api_call(True)
        with self._tracer.context_span(root, "download_media") as span:
            result = await self._engine.download_media(info, span)
            task.stats["downloaded"] = 1 if result.success else 0

    async def _handle_user(self, task: DownloadTask, root: TraceSpan):
        downloaded = 0
        cursor = 0
        all_posts = []

        with self._tracer.context_span(root, "fetch_all_posts") as fetch_span:
            while True:
                if "post" in self._config.mode:
                    page = await self._api.get_user_posts(task.extracted_id, cursor, fetch_span)
                else:
                    page = await self._api.get_user_likes(task.extracted_id, cursor, fetch_span)
                self._dashboard.record_api_call(True)

                aweme_list = page.get("aweme_list", [])
                if not aweme_list:
                    break
                all_posts.extend(aweme_list)
                fetch_span.attributes["fetched"] = len(all_posts)

                if not page.get("has_more"):
                    break
                cursor = page.get("max_cursor", 0)

        total = len(all_posts)
        with self._tracer.context_span(root, "download_posts", total=total) as dl_span:
            for i, post in enumerate(all_posts):
                if self._config.number.get("post", 0) > 0 and downloaded >= self._config.number["post"]:
                    break
                with self._tracer.context_span(dl_span, "download_media",
                        index=i+1, aweme_id=post.get("aweme_id")) as media_span:
                    result = await self._engine.download_media(post, media_span)
                    if result.success:
                        downloaded += 1
                self._dashboard.update_progress(task, i + 1, total)

        task.stats["downloaded"] = downloaded
        task.stats["total"] = total

    async def _handle_mix(self, task: DownloadTask, root: TraceSpan):
        downloaded = 0
        cursor = 0

        while True:
            page = await self._api.get_mix_items(task.extracted_id, cursor, root)
            self._dashboard.record_api_call(True)
            aweme_list = page.get("aweme_list", [])
            if not aweme_list:
                break
            for post in aweme_list:
                with self._tracer.context_span(root, "download_media") as span:
                    result = await self._engine.download_media(post, span)
                    if result.success:
                        downloaded += 1
            if not page.get("has_more"):
                break
            cursor = page.get("cursor", 0)

        task.stats["downloaded"] = downloaded

    async def _handle_music(self, task: DownloadTask, root: TraceSpan):
        downloaded = 0
        cursor = 0

        while True:
            page = await self._api.get_music_items(task.extracted_id, cursor, root)
            self._dashboard.record_api_call(True)
            aweme_list = page.get("aweme_list", [])
            if not aweme_list:
                break
            for post in aweme_list:
                with self._tracer.context_span(root, "download_media") as span:
                    result = await self._engine.download_media(post, span)
                    if result.success:
                        downloaded += 1
            if not page.get("has_more"):
                break
            cursor = page.get("cursor", 0)

        task.stats["downloaded"] = downloaded

    async def _resolve_short_url(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, allow_redirects=False,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (301, 302):
                        return str(resp.headers.get("Location", url))
        except Exception:
            pass
        return url

    @staticmethod
    def is_short_url(url: str) -> bool:
        return bool(_SHORT_URL_RE.match(url))

    @staticmethod
    def detect_content_type(url: str) -> str:
        if _NOTE_RE.search(url):
            return "image"
        if _VIDEO_RE.search(url):
            return "video"
        if _USER_RE.search(url):
            return "user"
        if _MIX_RE.search(url):
            return "mix"
        if _MUSIC_RE.search(url):
            return "music"
        return "video"

    @staticmethod
    def extract_id(url: str, content_type: str) -> str | None:
        match content_type:
            case "video":
                m = _VIDEO_RE.search(url)
                return m.group(1) if m else None
            case "image":
                m = _NOTE_RE.search(url)
                return m.group(1) if m else None
            case "user":
                m = _USER_RE.search(url)
                return m.group(1) if m else None
            case "mix":
                m = _MIX_RE.search(url)
                return (m.group(1) or m.group(2)) if m else None
            case "music":
                m = _MUSIC_RE.search(url)
                return m.group(1) if m else None
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat(core): 添加下载管线调度器"
```

---

### Task 11: 新入口（downloader.py 重写）

**Files:**
- Rename: `downloader.py` → `downloader_legacy.py`
- Create: `downloader.py` (新入口)

- [ ] **Step 1: 备份旧文件**

```bash
cp downloader.py downloader_legacy.py
```

- [ ] **Step 2: 写新入口**

```python
# downloader.py
"""
抖音下载器 v4.0 — 模块化重构版

用法:
    python downloader.py -c config.yml
    python downloader.py https://v.douyin.com/xxx
    python downloader.py --replay <trace_id>
    python downloader.py --validate-cookie
    python downloader.py --generate-config
"""

import argparse
import asyncio
import sys
import uuid
import time
from pathlib import Path

from core.config import ConfigLoader
from core.models import AppConfig
from core.tracer import Tracer
from core.logger import DualLogger
from core.cookie import CookieManager
from core.api_client import DouyinAPIClient
from core.downloader_engine import DownloadEngine
from core.pipeline import DownloadPipeline
from core.dashboard import Dashboard


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抖音下载器 v4.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="直接指定下载链接")
    parser.add_argument("-c", "--config", default="config.yml", help="配置文件路径")
    parser.add_argument("--save-path", help="覆盖保存目录")
    parser.add_argument("--concurrency", type=int, help="覆盖并发数")
    parser.add_argument("--verbose", action="store_true", help="控制台显示 DEBUG 日志")
    parser.add_argument("--no-dashboard", action="store_true", help="关闭仪表盘")
    parser.add_argument("--replay", metavar="TRACE_ID", help="回放指定 trace")
    parser.add_argument("--validate-cookie", action="store_true", help="仅检测 Cookie")
    parser.add_argument("--generate-config", action="store_true", help="生成默认配置")
    return parser.parse_args()


def cmd_replay(trace_id: str):
    log_dir = Path("logs")
    output = Tracer.replay(log_dir, trace_id)
    print(output)


def cmd_generate_config(path: str):
    ConfigLoader.generate_default(path)
    print(f"已生成默认配置: {path}")
    print("请编辑后重新运行。")


async def cmd_validate_cookie(config: AppConfig):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    dl = DualLogger(log_dir=log_dir, console_level="INFO")
    log = dl.get("cookie")
    mgr = CookieManager(config, tracer=None, logger=log)
    try:
        state = await mgr.ensure_valid_cookie()
        print(f"\n✅ Cookie 有效 (来源: {state.source})")
    except Exception as e:
        print(f"\n❌ Cookie 无效: {e}")
    dl.close()


async def cmd_download(config: AppConfig, args: argparse.Namespace):
    session_id = uuid.uuid4().hex[:8]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    console_level = "DEBUG" if args.verbose else "INFO"
    dual_logger = DualLogger(log_dir=log_dir, console_level=console_level)
    log = dual_logger.get("main")
    tracer = Tracer(log_dir=log_dir, session_id=session_id)

    log.info("抖音下载器 v4.0 启动", session_id=session_id)

    cookie_mgr = CookieManager(config, tracer=tracer, logger=dual_logger.get("cookie"))
    api = DouyinAPIClient(
        cookie_state=None,
        tracer=tracer,
        logger=dual_logger.get("api"),
        rate_limit=2.0,
        max_retries=config.retry_times,
    )
    engine = DownloadEngine(
        save_path=config.save_path,
        tracer=tracer,
        logger=dual_logger.get("engine"),
        concurrency=config.thread,
        download_music=config.download.music,
        download_cover=config.download.cover,
        download_json=config.download.json,
    )
    dashboard = Dashboard(
        total_tasks=len(config.links),
        concurrency=config.thread,
    )

    pipeline = DownloadPipeline(
        config=config, api=api, engine=engine,
        cookie_mgr=cookie_mgr, tracer=tracer,
        logger=dual_logger.get("pipeline"), dashboard=dashboard,
    )

    if not args.no_dashboard:
        dashboard.start()

    try:
        await pipeline.run()
    except KeyboardInterrupt:
        log.warn("用户中断")
    finally:
        dashboard.stop()
        await api.close()
        await engine.close()
        tracer.close()
        dual_logger.close()

    state = dashboard.get_state()
    print(f"\n{'=' * 60}")
    print(f"完成: {state['completed']}✓ {state['failed']}✗ / {state['total']}总 | 耗时 {state['elapsed']:.1f}s")
    print(f"Session: {session_id} | 日志: logs/")


def main():
    args = parse_args()

    if args.replay:
        cmd_replay(args.replay)
        return

    if args.generate_config:
        cmd_generate_config(args.config)
        return

    loader = ConfigLoader(args.config)

    if not Path(args.config).exists() and not args.urls:
        cmd_generate_config(args.config)
        return

    config = loader.load()

    if args.urls:
        config.links = args.urls
    if args.save_path:
        config.save_path = Path(args.save_path)
    if args.concurrency:
        config.thread = args.concurrency

    if not config.links:
        print("错误: 未指定下载链接。使用 -c config.yml 或直接传入 URL。")
        sys.exit(1)

    if args.validate_cookie:
        asyncio.run(cmd_validate_cookie(config))
        return

    config.save_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(cmd_download(config, args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 冒烟测试**

```bash
python downloader.py --generate-config /tmp/test_config.yml
python downloader.py --help
```

Expected: 生成配置文件成功，help 输出正常。

- [ ] **Step 4: 提交**

```bash
git add downloader_legacy.py downloader.py
git commit -m "feat: 重写 downloader.py 入口，原文件备份为 downloader_legacy.py"
```

---

### Task 12: 端到端集成验证

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/test_integration.py
import yaml
import pytest
from pathlib import Path
from core.config import ConfigLoader
from core.tracer import Tracer
from core.logger import DualLogger
from core.dashboard import Dashboard
from core.pipeline import DownloadPipeline


def test_full_config_load_and_validate(tmp_path):
    cfg_path = tmp_path / "config.yml"
    with open(cfg_path, "w") as f:
        yaml.dump({
            "links": ["https://www.douyin.com/video/7123456789"],
            "save_path": str(tmp_path / "out"),
            "cookie": "ttwid=abc; sessionid=xyz",
            "mode": ["post"],
            "concurrency": 2,
        }, f)

    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert len(config.links) == 1
    assert config.thread == 2
    assert config.cookie_mode == "string"


def test_old_config_migration(tmp_path):
    cfg_path = tmp_path / "config.yml"
    with open(cfg_path, "w") as f:
        yaml.dump({
            "link": ["https://www.douyin.com/video/123"],
            "path": str(tmp_path / "out"),
            "cookies": "ttwid=abc",
            "thread": 3,
            "number": {"post": 10},
            "increase": {"post": True},
        }, f)

    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://www.douyin.com/video/123"]
    assert config.thread == 3
    assert config.number == {"post": 10}


def test_tracer_and_logger_integration(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="integ_test")
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("test")

    root = tracer.start_trace("test_flow", url="https://example.com")
    bound_log = log.bind_trace(root.trace_id, root.span_id)
    bound_log.info("started trace")

    with tracer.context_span(root, "step_1") as child:
        bound_log.debug("in step 1")
        child.attributes["result"] = "ok"

    tracer.end_span(root, status="ok")

    output = Tracer.replay(tmp_path, root.trace_id)
    assert "test_flow" in output
    assert "step_1" in output

    tracer.close()
    dl.close()


def test_pipeline_url_parsing():
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/video/123") == "video"
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/note/123") == "image"
    assert DownloadPipeline.extract_id("https://www.douyin.com/video/7654321", "video") == "7654321"


def test_dashboard_lifecycle():
    db = Dashboard(total_tasks=3, concurrency=2)
    db.log_done("video1", True, "5 files", trace_id="t_001")
    db.log_done("video2", False, "API error", trace_id="t_002")
    state = db.get_state()
    assert state["completed"] == 1
    assert state["failed"] == 1
    assert state["total"] == 3
```

- [ ] **Step 2: 运行全部测试**

Run: `python -m pytest tests/ -v`
Expected: ALL PASSED

- [ ] **Step 3: 提交**

```bash
git add tests/test_integration.py
git commit -m "test: 添加端到端集成测试"
```

---

## 检查清单

完成所有 Task 后确认：

- [ ] `python downloader.py --help` 正常输出
- [ ] `python downloader.py --generate-config` 生成配置
- [ ] `python downloader.py --validate-cookie` Cookie 检测流程正常
- [ ] `python downloader.py -c config.yml` 使用已有 Cookie 正常下载
- [ ] `python downloader.py --replay <trace_id>` 回放 trace 链路
- [ ] `python downloader.py --no-dashboard -c config.yml` 纯文本模式正常
- [ ] `python -m pytest tests/ -v` 全部通过
- [ ] `downloader_legacy.py` 保留为备份可随时回滚
