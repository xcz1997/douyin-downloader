"""SubtitleSource interface. Each source turns a video (+ optional platform
raw dict) into a SubtitleDoc, or None when it has nothing to contribute."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from core.subtitle.schema import SubtitleDoc


@runtime_checkable
class SubtitleSource(Protocol):
    name: str  # "track" | "ocr" | "asr"

    def is_available(self) -> bool:
        """False when deps/platform unsupported. Source is then skipped."""
        ...

    def extract(
        self, video_path: Path, raw: dict | None
    ) -> SubtitleDoc | None:
        """Return a doc, or None if this source has no output for the video.

        Returning None is normal (e.g. no subtitle track). Raising is an
        error the runner isolates per-source.
        """
        ...
