import logging
from pathlib import Path

from core.subtitle.base import SubtitleSource
from core.subtitle.runner import SubtitleRunner
from core.subtitle.schema import Segment, SubtitleDoc


class _Dummy:
    name = "ocr"

    def is_available(self) -> bool:
        return True

    def extract(self, video_path: Path, raw: dict | None) -> SubtitleDoc | None:
        return None


def test_dummy_satisfies_protocol():
    src: SubtitleSource = _Dummy()
    assert src.name == "ocr"
    assert src.is_available() is True
    assert src.extract(Path("x.mp4"), None) is None


class _Good:
    name = "ocr"

    def is_available(self):
        return True

    def extract(self, video_path, raw):
        return SubtitleDoc(
            source="ocr", video=Path(video_path).name, language="zh",
            duration=1.0, segments=[Segment(0, 0.0, 1.0, "ok")],
        )


class _Boom:
    name = "asr"

    def is_available(self):
        return True

    def extract(self, video_path, raw):
        raise RuntimeError("asr blew up")


class _Unavailable:
    name = "track"

    def is_available(self):
        return False

    def extract(self, video_path, raw):
        raise AssertionError("must not be called when unavailable")


def test_runner_writes_for_good_source(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good()], sources=["ocr"])
    written = runner.run(v, raw=None)
    assert (tmp_path / "v.ocr.json") in written
    assert (tmp_path / "v.ocr.txt").exists()


def test_runner_isolates_failing_source(tmp_path, caplog):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good(), _Boom()], sources=["ocr", "asr"])
    with caplog.at_level(logging.WARNING):
        written = runner.run(v, raw=None)
    assert (tmp_path / "v.ocr.json") in written
    assert not (tmp_path / "v.asr.json").exists()
    assert "asr" in caplog.text


def test_runner_skips_unselected_source(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good(), _Boom()], sources=["ocr"])
    written = runner.run(v, raw=None)
    assert (tmp_path / "v.ocr.json") in written
    assert not (tmp_path / "v.asr.json").exists()  # asr unselected, never run


def test_runner_skips_unavailable_source(tmp_path, caplog):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good(), _Unavailable()], sources=["ocr", "track"])
    with caplog.at_level(logging.WARNING):
        written = runner.run(v, raw=None)
    assert not (tmp_path / "v.track.json").exists()
    assert (tmp_path / "v.ocr.json") in written
    assert "track" in caplog.text  # the not-available warning actually fired


def test_runner_skips_source_returning_none(tmp_path):
    class _Nothing:
        name = "ocr"
        def is_available(self):
            return True
        def extract(self, video_path, raw):
            return None

    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Nothing()], sources=["ocr"])
    assert runner.run(v, raw=None) == []
    assert not (tmp_path / "v.ocr.json").exists()
