import sys
import types
from pathlib import Path

from core.subtitle.asr_source import ASRSource, _map_result


def test_map_result_to_segments():
    raw_result = {
        "language": "zh",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "你好"},
            {"start": 2.0, "end": 4.5, "text": "再见"},
        ],
    }
    lang, segs = _map_result(raw_result)
    assert lang == "zh"
    assert [s.text for s in segs] == ["你好", "再见"]
    assert segs[1].start == 2.0 and segs[1].end == 4.5
    assert [s.id for s in segs] == [0, 1]


def test_is_available_false_off_apple_silicon(monkeypatch):
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: False)
    assert ASRSource().is_available() is False


def test_extract_uses_transcribe(monkeypatch, tmp_path):
    received = {}

    def fake_transcribe(p, **kw):
        received["path"] = p
        received.update(kw)
        return {"language": "zh", "segments": [{"start": 0.0, "end": 1.0, "text": "话"}]}

    fake_mod = types.SimpleNamespace(transcribe=fake_transcribe)
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", fake_mod)
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: True)
    src = ASRSource(model="0.6b")
    doc = src.extract(tmp_path / "v.mp4", raw=None)
    assert doc is not None
    assert doc.source == "asr"
    assert doc.segments[0].text == "话"
    assert received["model"] == "mlx-community/Qwen3-ASR-0.6B"
    assert received["path"].endswith("v.mp4")


def test_extract_returns_none_when_no_segments(monkeypatch, tmp_path):
    fake_mod = types.SimpleNamespace(
        transcribe=lambda p, **kw: {"language": "zh", "segments": []}
    )
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", fake_mod)
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: True)
    assert ASRSource().extract(tmp_path / "v.mp4", raw=None) is None
