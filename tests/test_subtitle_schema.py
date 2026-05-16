import json
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc


def test_segment_rounds_times_to_2dp():
    s = Segment(id=0, start=1.2049, end=3.4561, text="你好")
    assert s.start == 1.20
    assert s.end == 3.46


def test_doc_to_json_dict_shape():
    doc = SubtitleDoc(
        source="asr", video="x.mp4", language="zh", duration=10.0,
        segments=[Segment(0, 1.0, 2.0, "甲"), Segment(1, 2.0, 3.0, "乙")],
    )
    d = doc.to_json_dict()
    assert d["source"] == "asr"
    assert d["video"] == "x.mp4"
    assert d["segments"][1] == {"id": 1, "start": 2.0, "end": 3.0, "text": "乙"}


def test_doc_to_txt_one_line_per_segment():
    doc = SubtitleDoc(
        source="ocr", video="x.mp4", language="zh", duration=5.0,
        segments=[Segment(0, 0.0, 1.0, "第一句"), Segment(1, 1.0, 2.0, "第二句")],
    )
    assert doc.to_txt() == "第一句\n第二句"


def test_doc_write_creates_json_and_txt(tmp_path: Path):
    doc = SubtitleDoc(
        source="ocr", video="v.mp4", language="zh", duration=2.0,
        segments=[Segment(0, 0.0, 1.0, "话")],
    )
    j, t = doc.write(tmp_path / "v.mp4")
    assert j == tmp_path / "v.ocr.json"
    assert t == tmp_path / "v.ocr.txt"
    loaded = json.loads(j.read_text(encoding="utf-8"))
    assert loaded["segments"][0]["text"] == "话"
    assert t.read_text(encoding="utf-8") == "话"


def test_empty_doc_writes_nothing_and_returns_none(tmp_path: Path):
    doc = SubtitleDoc(
        source="track", video="v.mp4", language="zh",
        duration=0.0, segments=[],
    )
    assert doc.write(tmp_path / "v.mp4") == (None, None)
    assert list(tmp_path.iterdir()) == []
