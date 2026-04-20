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
    tracer.close()


def test_start_span_inherits_trace_id(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    child = tracer.start_span(root, "child_op", key="value")
    assert child.trace_id == root.trace_id
    assert child.parent_id == root.span_id
    assert child.attributes["key"] == "value"
    tracer.close()


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
    tracer.close()


def test_add_event(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    tracer.add_event(root, "cookie_checked", valid=True)
    assert len(root.events) == 1
    assert root.events[0]["event"] == "cookie_checked"
    assert root.events[0]["valid"] is True
    tracer.close()


def test_context_span_auto_close(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")

    with tracer.context_span(root, "child_op") as child:
        child.attributes["step"] = 1

    assert child.status == "ok"
    assert child.end_time is not None
    tracer.close()


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
    tracer.close()


def test_replay_builds_tree(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test_session")
    root = tracer.start_trace("root", url="test")
    child1 = tracer.start_span(root, "step_1")
    tracer.end_span(child1, status="ok")
    child2 = tracer.start_span(root, "step_2")
    tracer.end_span(child2, status="error", error="fail")
    tracer.end_span(root, status="ok")
    tracer.close()

    output = Tracer.replay(tmp_path, root.trace_id)
    assert "root" in output
    assert "step_1" in output
    assert "step_2" in output
    assert "error" in output
