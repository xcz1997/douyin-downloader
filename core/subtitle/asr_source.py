"""ASR subtitle source via mlx-qwen3-asr (Apple Silicon native).

Heavy dep imported lazily inside extract(). Tests monkeypatch
sys.modules['mlx_qwen3_asr'] so the real model never runs.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc

_MODEL_REPO = {
    "0.6b": "mlx-community/Qwen3-ASR-0.6B",
    "1.7b": "mlx-community/Qwen3-ASR-1.7B",
}


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _map_result(result: dict) -> tuple[str, list[Segment]]:
    lang = result.get("language", "zh")
    segs = [
        Segment(
            id=i,
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", 0.0)),
            text=str(s.get("text", "")).strip(),
        )
        for i, s in enumerate(result.get("segments", []))
        if str(s.get("text", "")).strip()
    ]
    return lang, segs


class ASRSource:
    name = "asr"

    def __init__(self, model: str = "0.6b"):
        self._model = model

    def is_available(self) -> bool:
        if not _is_apple_silicon():
            return False
        try:
            import mlx_qwen3_asr  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, video_path: Path, raw: dict | None) -> SubtitleDoc | None:
        import mlx_qwen3_asr  # lazy; tests inject a fake

        repo = _MODEL_REPO.get(self._model, _MODEL_REPO["0.6b"])
        result = mlx_qwen3_asr.transcribe(str(video_path), model=repo)
        lang, segs = _map_result(result)
        if not segs:
            return None
        duration = segs[-1].end
        return SubtitleDoc(
            source="asr", video=Path(video_path).name,
            language=lang, duration=duration, segments=segs,
        )
