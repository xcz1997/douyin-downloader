import pytest
from pathlib import Path
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
