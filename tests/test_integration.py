import yaml
import json
import pytest
from pathlib import Path
from core.config import ConfigLoader
from core.tracer import Tracer
from core.logger import DualLogger
from core.dashboard import Dashboard
from core.pipeline import DownloadPipeline
from core.models import AppConfig, DownloadOptions, DownloadTask, CookieState


def test_full_config_load_and_validate(tmp_path):
    """Config loading → AppConfig creation end-to-end"""
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


def test_old_config_migration_end_to_end(tmp_path):
    """Old format config → migrated AppConfig"""
    cfg_path = tmp_path / "config.yml"
    with open(cfg_path, "w") as f:
        yaml.dump({
            "link": ["https://www.douyin.com/video/123"],
            "path": str(tmp_path / "out"),
            "cookies": "ttwid=abc",
            "thread": 3,
            "number": {"post": 10},
            "increase": {"post": True},
            "start_time": "2026-01-01",
            "end_time": "2026-12-31",
        }, f)

    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://www.douyin.com/video/123"]
    assert config.thread == 3
    assert config.number == {"post": 10}
    assert config.start_time == "2026-01-01"
    assert config.end_time == "2026-12-31"


def test_tracer_logger_integration(tmp_path):
    """Tracer + Logger work together with trace binding"""
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

    # Verify trace replay
    output = Tracer.replay(tmp_path, root.trace_id)
    assert "test_flow" in output
    assert "step_1" in output

    # Verify log file contains trace_id
    log_files = list((tmp_path / "app").glob("*.jsonl"))
    assert len(log_files) == 1
    with open(log_files[0]) as f:
        lines = f.readlines()
    records = [json.loads(l) for l in lines]
    assert any(r.get("trace_id") == root.trace_id for r in records)

    tracer.close()
    dl.close()


def test_tracer_exception_chain(tmp_path):
    """Verify nested span exception propagation records errors correctly"""
    tracer = Tracer(log_dir=tmp_path, session_id="exc_test")
    root = tracer.start_trace("root", url="test")

    try:
        with tracer.context_span(root, "outer") as outer:
            with tracer.context_span(outer, "inner") as inner:
                raise ValueError("deep error")
    except ValueError:
        pass

    tracer.end_span(root, status="error")
    tracer.close()

    output = Tracer.replay(tmp_path, root.trace_id)
    assert "inner" in output
    assert "error" in output


def test_pipeline_url_parsing_comprehensive():
    """Pipeline static methods handle all URL patterns"""
    # Short URLs
    assert DownloadPipeline.is_short_url("https://v.douyin.com/cGYAzzSDbRQ/")
    assert not DownloadPipeline.is_short_url("https://www.douyin.com/video/123")

    # Content type detection
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/video/7562522534060772649") == "video"
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/note/7562522534060772649") == "image"
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/user/MS4wLjABAAAAtest") == "user"

    # ID extraction
    assert DownloadPipeline.extract_id("https://www.douyin.com/video/7562522534060772649", "video") == "7562522534060772649"
    assert DownloadPipeline.extract_id("https://www.douyin.com/note/7562522534060772649", "image") == "7562522534060772649"


def test_dashboard_full_lifecycle():
    """Dashboard state transitions through full task lifecycle"""
    db = Dashboard(total_tasks=3, concurrency=2)

    # Add tasks
    for i in range(3):
        task = DownloadTask(
            task_id=f"t{i}", trace_id=f"tr{i}",
            url=f"https://example.com/{i}", content_type="video",
        )
        db.add_task(task)

    # Set cookie
    cs = CookieState(value="ttwid=abc", source="config", obtained_at=0)
    db.set_cookie_state(cs)

    # Simulate API calls
    db.record_api_call(True)
    db.record_api_call(True)
    db.record_api_call(False)

    # Log completions
    db.log_done("video1", True, "3 files", trace_id="tr0")
    db.log_done("video2", False, "API error", trace_id="tr1")
    db.log_done("video3", True, "1 file", trace_id="tr2")

    state = db.get_state()
    assert state["total"] == 3
    assert state["completed"] == 2
    assert state["failed"] == 1
    assert state["api_calls"] == 3
    assert state["api_fails"] == 1
    assert state["cookie_source"] == "config"


def test_config_generate_and_reload(tmp_path):
    """Generate default config → reload → verify valid"""
    cfg_path = str(tmp_path / "config.yml")
    ConfigLoader.generate_default(cfg_path)

    loader = ConfigLoader(cfg_path)
    config = loader.load()
    assert isinstance(config, AppConfig)
    assert config.download.music is True
    assert config.thread > 0
