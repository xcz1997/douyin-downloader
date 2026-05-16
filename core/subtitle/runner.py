"""SubtitleRunner: run the selected subtitle sources for one video,
isolating per-source failures so subtitle work never breaks downloads."""

from __future__ import annotations

import logging
from pathlib import Path

from core.subtitle.base import SubtitleSource

log = logging.getLogger("subtitle")


class SubtitleRunner:
    def __init__(
        self,
        impls: list[SubtitleSource],
        sources: list[str],
    ):
        """impls: available source implementations.
        sources: which source names to run (subset of impl names)."""
        self._impls = impls
        self._selected = set(sources)

    def run(self, video_path: Path, raw: dict | None) -> list[Path]:
        """Run each selected, available source. Returns written JSON paths.
        Never raises due to a source failure."""
        video_path = Path(video_path)
        written: list[Path] = []
        for src in self._impls:
            if src.name not in self._selected:
                continue
            try:
                if not src.is_available():
                    log.warning(
                        "字幕源 %s 不可用（依赖缺失或平台不支持），跳过", src.name
                    )
                    continue
                doc = src.extract(video_path, raw)
                if doc is None:
                    continue
                json_path, _ = doc.write(video_path)
                if json_path is not None:
                    written.append(json_path)
            except Exception as exc:  # isolate per-source
                log.warning(
                    "字幕源 %s 提取失败，跳过该源（不影响下载）: %s",
                    src.name, exc,
                )
        return written
