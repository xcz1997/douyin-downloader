import json
from pathlib import Path

from core.models import TranscribeConfig
from core.transcribe.runner import (
    ImageTranscriber, build_image_transcriber, find_images, find_data_json,
)


class _FakeClient:
    def __init__(self, text="### 图1\nHELLO"):
        self.text = text
        self.calls = 0

    def transcribe_images(self, paths, prompt):
        self.calls += 1
        self.seen = list(paths)
        return self.text


def _make_note(dir_: Path, nickname="作者A", imgs=3):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "note_data.json").write_text(json.dumps({
        "aweme_id": "111", "desc": "正文内容",
        "author": {"nickname": nickname, "unique_id": "uidA"},
        "create_time": 1700000000,
        "statistics": {"digg_count": 10, "collect_count": 5,
                       "comment_count": 2, "share_count": 1},
        "text_extra": [{"hashtag_name": "标签1"}],
        "images": [{}] * imgs,
    }, ensure_ascii=False), encoding="utf-8")
    for i in range(imgs):
        (dir_ / f"image_{i}.webp").write_bytes(b"x")
    return dir_


def test_find_images_sorted_by_index(tmp_path):
    d = _make_note(tmp_path, imgs=3)
    (d / "image_10.webp").write_bytes(b"x")
    names = [p.name for p in find_images(d)]
    assert names == ["image_0.webp", "image_1.webp",
                     "image_2.webp", "image_10.webp"]


def test_transcribe_dir_writes_doc(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=False))
    out = t.transcribe_dir(d)
    assert out is not None and out.exists()
    text = out.read_text(encoding="utf-8")
    assert "作者A" in text
    assert "正文内容" in text          # 正文 desc
    assert "#标签1" in text            # 话题
    assert "点赞 10" in text           # 互动
    assert "### 图1" in text and "HELLO" in text  # 图片转录
    assert client.calls == 1


def test_idempotent_skip_when_exists(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=False))
    t.transcribe_dir(d)
    assert client.calls == 1
    second = t.transcribe_dir(d)       # 第二次应跳过
    assert second is None
    assert client.calls == 1


def test_overwrite_reruns(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=True))
    t.transcribe_dir(d)
    t.transcribe_dir(d)
    assert client.calls == 2


def test_max_images_truncates(tmp_path):
    d = _make_note(tmp_path, imgs=5)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(max_images=2))
    t.transcribe_dir(d)
    assert len(client.seen) == 2


def test_build_factory_returns_none_when_disabled():
    assert build_image_transcriber(TranscribeConfig(enabled=False)) is None


def test_build_factory_returns_none_when_auto_off():
    # enabled 但没开 auto_after_download → 仍返回 None
    assert build_image_transcriber(
        TranscribeConfig(enabled=True, auto_after_download=False)) is None


def test_max_images_logs_warning(tmp_path, caplog):
    import logging
    d = _make_note(tmp_path, imgs=5)
    t = ImageTranscriber(_FakeClient(), TranscribeConfig(max_images=2))
    with caplog.at_level(logging.WARNING, logger="transcribe"):
        t.transcribe_dir(d)
    assert any("max_images" in r.message or "仅转录前" in r.message
               for r in caplog.records)
