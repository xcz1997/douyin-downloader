from pathlib import Path

from core.pipeline import build_subtitle_runner, _collect_video_files


def test_no_runner_when_disabled():
    class Sub:
        enabled = False
        sources = ["ocr"]
        asr_model = "0.6b"
        ocr_interval = 0.5
        ocr_similarity = 0.7

    class Cfg:
        subtitle = Sub()

    assert build_subtitle_runner(Cfg()) is None


def test_runner_built_with_selected_sources_when_enabled():
    class Sub:
        enabled = True
        sources = ["ocr", "track"]
        asr_model = "0.6b"
        ocr_interval = 0.5
        ocr_similarity = 0.7

    class Cfg:
        subtitle = Sub()

    runner = build_subtitle_runner(Cfg())
    assert runner is not None
    names = {impl.name for impl in runner._impls}
    assert "ocr" in names and "track" in names
    assert runner._selected == {"ocr", "track"}


def test_collect_video_files_finds_mp4(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.mov").write_bytes(b"x")
    found = sorted(p.name for p in _collect_video_files([str(tmp_path)]))
    assert found == ["a.mp4", "c.mov"]
