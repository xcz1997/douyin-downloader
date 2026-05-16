from pathlib import Path

from core.subtitle.base import SubtitleSource
from core.subtitle.schema import SubtitleDoc


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
