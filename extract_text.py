"""视频字幕提取 CLI（薄壳）。

实际逻辑在 core.subtitle。默认只跑 OCR 源，保持历史行为；
可用 --sources 选择 track/ocr/asr。

用法:
    python extract_text.py video.mp4
    python extract_text.py ./downloads/
    python extract_text.py video.mp4 --sources ocr,asr
"""

import argparse
import sys
from pathlib import Path

from core.subtitle.ocr_source import OCRSource
from core.subtitle.runner import SubtitleRunner

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"}


def find_videos(path: str) -> list[str]:
    p = Path(path)
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        return [str(p)]
    if p.is_dir():
        vids: list[str] = []
        for ext in _VIDEO_EXTS:
            vids.extend(str(f) for f in p.rglob(f"*{ext}"))
        vids.sort()
        return vids
    return []


def _build_impls(args):
    impls = []
    if "ocr" in args.sources:
        impls.append(OCRSource(interval=args.interval, similarity=args.similarity))
    if "track" in args.sources:
        from core.subtitle.track_source import TrackSource
        impls.append(TrackSource())
    if "asr" in args.sources:
        from core.subtitle.asr_source import ASRSource
        impls.append(ASRSource(model=args.asr_model))
    return impls


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从视频提取字幕（track/ocr/asr → 带时间戳 JSON + TXT）",
    )
    parser.add_argument("path", help="视频文件或目录路径")
    parser.add_argument(
        "--sources", default="ocr",
        help="逗号分隔，子集 of track,ocr,asr（默认 ocr）",
    )
    parser.add_argument("--interval", type=float, default=0.5,
                        help="OCR 采帧间隔(秒)，默认0.5")
    parser.add_argument("--similarity", type=float, default=0.7,
                        help="OCR 去重相似度阈值(0-1)，默认0.7")
    parser.add_argument("--asr-model", default="0.6b",
                        help="ASR 模型 0.6b 或 1.7b，默认0.6b")
    args = parser.parse_args()
    args.sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    videos = find_videos(args.path)
    if not videos:
        print(f"未找到视频文件: {args.path}", file=sys.stderr)
        sys.exit(1)

    impls = _build_impls(args)
    runner = SubtitleRunner(impls, sources=args.sources)

    print(f"找到 {len(videos)} 个视频 | 源: {','.join(args.sources)}")
    for i, v in enumerate(videos, 1):
        written = runner.run(Path(v), raw=None)
        names = ", ".join(p.name for p in written) or "无产出"
        print(f"  [{i}/{len(videos)}] {Path(v).name} → {names}")


if __name__ == "__main__":
    main()
