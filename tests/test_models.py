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
