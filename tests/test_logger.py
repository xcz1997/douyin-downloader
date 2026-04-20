import json
from pathlib import Path
from core.logger import DualLogger


def test_create_logger(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="INFO", file_level="DEBUG")
    log = dl.get("test_module")
    assert log is not None
    dl.close()


def test_info_writes_to_file(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.info("hello world", extra_key="val")

    dl.close()
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

    dl.close()
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

    dl.close()
    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["trace_id"] == "t_abc"
    assert record["span_id"] == "s_123"


def test_warn_level(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.warn("retrying", attempt=2)

    dl.close()
    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["level"] == "WARN"
    assert record["attempt"] == 2


def test_error_level(tmp_path):
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR", file_level="DEBUG")
    log = dl.get("mymod")
    log.error("fatal", code=500)

    dl.close()
    files = list((tmp_path / "app").glob("*.jsonl"))
    with open(files[0]) as f:
        record = json.loads(f.readline())
    assert record["level"] == "ERROR"
