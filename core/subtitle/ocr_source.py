"""OCR subtitle source: per-frame OCR accumulated into timestamped segments.

Replaces extract_text.py's global-dedup collapse with a time-aware
accumulator so each on-screen subtitle becomes (start, end, text).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc

# --- noise/normalize: ported verbatim from extract_text.py (unchanged) ---
_NOISE_RE = re.compile(
    r'^[\s\d\W]{1,6}$'
    r'|^[a-zA-Z\d\s\.\-\_\×x]{1,10}$'
    r'|^[>＞<＜\+\-\*\d\s\×x]+$'
)


def is_noise(text: str) -> bool:
    return bool(_NOISE_RE.match(text.strip()))


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _similar(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


class _OpenSeg:
    __slots__ = ("text", "start", "end", "last_seen")

    def __init__(self, text: str, t: float):
        self.text = text
        self.start = t
        self.end = t
        self.last_seen = t


class SegmentAccumulator:
    """Feed (timestamp, [text blocks]) in increasing time order; finalize()
    returns merged, time-stamped, de-noised Segments."""

    def __init__(self, similarity: float = 0.7, gap: float = 1.0):
        self._similarity = similarity
        self._gap = gap
        self._open: list[_OpenSeg] = []
        self._closed: list[_OpenSeg] = []

    def feed(self, t: float, blocks: list[str]) -> None:
        # close stale open segments not seen within `gap`
        still_open: list[_OpenSeg] = []
        for seg in self._open:
            if t - seg.last_seen > self._gap:
                self._closed.append(seg)
            else:
                still_open.append(seg)
        self._open = still_open

        for raw in blocks:
            if not raw.strip() or is_noise(raw):
                continue
            text = normalize(raw)
            if not text:
                continue
            matched = None
            for seg in self._open:
                if _similar(seg.text, text, self._similarity):
                    matched = seg
                    break
            if matched is not None:
                matched.end = t
                matched.last_seen = t
            else:
                self._open.append(_OpenSeg(text, t))

    def finalize(self) -> list[Segment]:
        all_segs = self._closed + self._open
        all_segs.sort(key=lambda s: (s.start, s.end))
        return [
            Segment(id=i, start=s.start, end=s.end, text=s.text)
            for i, s in enumerate(all_segs)
        ]


def _process_video(video_path: str, interval: float, similarity: float):
    """Decode frames at `interval` seconds, OCR each, accumulate.

    Returns (segments, duration_seconds). Imports cv2/RapidOCR lazily so
    importing this module never requires the heavy OCR stack.
    """
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return ([], 0.0)

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    frame_step = max(1, int(fps * interval))

    acc = SegmentAccumulator(similarity=similarity, gap=max(interval * 3, 1.0))
    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % frame_step == 0:
                h, w = frame.shape[:2]
                if w > 720:
                    nh = int(h * 720 / w)
                    frame = cv2.resize(frame, (720, nh))
                result, _ = ocr(frame)
                blocks = [item[1] for item in result] if result else []
                acc.feed(frame_idx / fps, blocks)
            frame_idx += 1
    finally:
        cap.release()
    return (acc.finalize(), duration)


class OCRSource:
    name = "ocr"

    def __init__(self, interval: float = 0.5, similarity: float = 0.7):
        self._interval = interval
        self._similarity = similarity

    def is_available(self) -> bool:
        try:
            import cv2  # noqa: F401
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, video_path: Path, raw: dict | None) -> SubtitleDoc | None:
        segs, duration = _process_video(
            str(video_path), self._interval, self._similarity
        )
        if not segs:
            return None
        return SubtitleDoc(
            source="ocr", video=Path(video_path).name,
            language="zh", duration=duration, segments=segs,
        )
