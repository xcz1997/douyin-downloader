"""Track subtitle source: pull a platform-provided caption file (WebVTT)
from the post's raw dict, parse to segments. No track → None."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc

_TS_RE = re.compile(
    r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})"
)


def _ts(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_webvtt(text: str) -> list[Segment]:
    segs: list[Segment] = []
    lines = text.splitlines()
    i = 0
    sid = 0
    while i < len(lines):
        m = _TS_RE.search(lines[i])
        if not m:
            i += 1
            continue
        start = _ts(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        buf: list[str] = []
        while i < len(lines) and lines[i].strip():
            buf.append(lines[i].strip())
            i += 1
        if buf:
            segs.append(Segment(sid, start, end, " ".join(buf)))
            sid += 1
    return segs


# Ordered candidate paths into the raw dict where a caption URL may live.
# Add more as real抖音/XHS samples reveal new shapes (spec §11).
def find_caption_url(raw: dict | None) -> str | None:
    if not raw:
        return None
    video = raw.get("video") or {}
    cla = video.get("cla_info") or {}
    for info in cla.get("caption_infos") or []:
        urls = info.get("url_list") or []
        if urls and urls[0]:
            return urls[0]
    cap = video.get("caption")
    if isinstance(cap, str) and cap.startswith("http"):
        return cap
    return None


def _http_get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


class TrackSource:
    name = "track"

    def is_available(self) -> bool:
        return True

    def extract(self, video_path: Path, raw: dict | None) -> SubtitleDoc | None:
        url = find_caption_url(raw)
        if not url:
            return None
        try:
            text = _http_get_text(url)
        except Exception:
            return None
        segs = parse_webvtt(text)
        if not segs:
            return None
        return SubtitleDoc(
            source="track", video=Path(video_path).name,
            language="zh", duration=segs[-1].end, segments=segs,
        )
