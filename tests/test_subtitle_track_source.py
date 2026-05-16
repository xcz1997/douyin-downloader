from pathlib import Path

from core.subtitle.track_source import TrackSource, parse_webvtt, find_caption_url


SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
第一句字幕

00:00:03.500 --> 00:00:06.000
第二句字幕
"""


def test_parse_webvtt_to_segments():
    segs = parse_webvtt(SAMPLE_VTT)
    assert len(segs) == 2
    assert segs[0].start == 1.0 and segs[0].end == 3.5
    assert segs[0].text == "第一句字幕"
    assert segs[1].start == 3.5 and segs[1].text == "第二句字幕"


def test_find_caption_url_from_known_field():
    raw = {
        "video": {
            "cla_info": {
                "caption_infos": [
                    {"url_list": ["https://example.com/cap.vtt"], "lang": "zh"}
                ]
            }
        }
    }
    assert find_caption_url(raw) == "https://example.com/cap.vtt"


def test_find_caption_url_none_when_absent():
    assert find_caption_url({"video": {}}) is None
    assert find_caption_url(None) is None


def test_extract_returns_none_without_track(tmp_path):
    src = TrackSource()
    assert src.extract(tmp_path / "v.mp4", raw={"video": {}}) is None


def test_extract_builds_doc(monkeypatch, tmp_path):
    src = TrackSource()
    monkeypatch.setattr(
        "core.subtitle.track_source._http_get_text",
        lambda url: SAMPLE_VTT,
    )
    raw = {"video": {"cla_info": {"caption_infos": [
        {"url_list": ["https://x/cap.vtt"], "lang": "zh"}]}}}
    doc = src.extract(tmp_path / "v.mp4", raw=raw)
    assert doc is not None
    assert doc.source == "track"
    assert len(doc.segments) == 2


def test_find_caption_url_fallback_caption_string():
    raw = {"video": {"caption": "https://example.com/cap.vtt"}}
    assert find_caption_url(raw) == "https://example.com/cap.vtt"


def test_parse_webvtt_no_hour_timestamps():
    vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\n短视频字幕\n"
    segs = parse_webvtt(vtt)
    assert len(segs) == 1
    assert segs[0].start == 1.0 and segs[0].end == 3.0
    assert segs[0].text == "短视频字幕"
