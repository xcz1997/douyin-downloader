from pathlib import Path
from types import SimpleNamespace

from core.pipeline import DownloadPipeline


def _result(file_paths, media=1, success=True):
    task = SimpleNamespace(file_paths=file_paths)
    return SimpleNamespace(task=task, media_files_written=media,
                           success=success)


def test_run_transcribe_calls_per_dir(monkeypatch, tmp_path):
    calls = []
    fake = SimpleNamespace(
        transcribe_dir=lambda d: calls.append(Path(d)) or None)
    # 绕过 __init__ 直接构造一个空壳 pipeline
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = fake
    import asyncio
    asyncio.run(p._run_transcribe(_result([str(tmp_path)])))
    assert calls == [tmp_path]


def test_run_transcribe_noop_when_none():
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = None
    import asyncio
    asyncio.run(p._run_transcribe(_result(["/x"])))  # 不抛即可


def test_run_transcribe_swallows_exception():
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = SimpleNamespace(
        transcribe_dir=lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    p._log = SimpleNamespace(warn=lambda *a, **kw: None)
    import asyncio
    asyncio.run(p._run_transcribe(_result(["/x"])))  # 不抛即可


def test_run_transcribe_skips_when_no_media():
    calls = []
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = SimpleNamespace(
        transcribe_dir=lambda d: calls.append(d))
    import asyncio
    asyncio.run(p._run_transcribe(_result(["/x"], media=0)))
    assert calls == []
