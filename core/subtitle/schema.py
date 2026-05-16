"""Subtitle data model + JSON/TXT serialization.

JSON is Whisper-style segments so it converts cleanly to SRT/VTT.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        self.start = round(float(self.start), 2)
        self.end = round(float(self.end), 2)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


@dataclass
class SubtitleDoc:
    source: str        # "track" | "ocr" | "asr"
    video: str         # video file name
    language: str      # e.g. "zh"
    duration: float
    segments: list[Segment]

    def to_json_dict(self) -> dict:
        return {
            "source": self.source,
            "video": self.video,
            "language": self.language,
            "duration": round(float(self.duration), 2),
            "segments": [s.to_dict() for s in self.segments],
        }

    def to_txt(self) -> str:
        return "\n".join(s.text for s in self.segments)

    def write(
        self, video_path: Path
    ) -> tuple[Path | None, Path | None]:
        """Write `<stem>.<source>.json` + `.txt` next to the video.

        Empty docs write nothing and return (None, None).
        """
        if not self.segments:
            return (None, None)
        video_path = Path(video_path)
        base = video_path.with_suffix("")
        json_path = base.with_name(f"{base.name}.{self.source}.json")
        txt_path = base.with_name(f"{base.name}.{self.source}.txt")
        json_path.write_text(
            json.dumps(self.to_json_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        txt_path.write_text(self.to_txt(), encoding="utf-8")
        return (json_path, txt_path)
