from pathlib import Path

from transcribe_images import find_note_dirs


def _note(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "x_data.json").write_text("{}", encoding="utf-8")
    (d / "image_0.webp").write_bytes(b"x")


def test_find_note_dirs_self(tmp_path):
    _note(tmp_path)
    dirs = find_note_dirs(str(tmp_path))
    assert tmp_path in dirs


def test_find_note_dirs_recursive(tmp_path):
    _note(tmp_path / "作者" / "笔记1")
    _note(tmp_path / "作者" / "笔记2")
    dirs = find_note_dirs(str(tmp_path))
    assert len(dirs) == 2
