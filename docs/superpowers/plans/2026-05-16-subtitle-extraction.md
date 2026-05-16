# 字幕提取系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给下载的视频生成三种来源（track / ocr / asr）带时间戳的 Whisper 风格 segments JSON + 人类可读 TXT，三源各出一份，可选接进下载流程（配置开关默认关）。

**Architecture:** 新增 `core/subtitle/` 包：`schema` 定义数据结构与写出，`base` 定义 `SubtitleSource` 接口，`ocr_source`/`track_source`/`asr_source` 三个独立实现，`runner` 按配置跑选定源并隔离各源失败。`extract_text.py` 退化为调 runner 的薄 CLI 壳。pipeline 在视频下载成功后经 `SubtitleRunner` 调用，默认关闭零开销。

**Tech Stack:** Python 3.13、pytest、opencv-python、rapidocr-onnxruntime（已有 OCR）、mlx-qwen3-asr（新增 ASR，仅 Apple Silicon）、PyYAML（已有配置）。

参考 spec：`docs/superpowers/specs/2026-05-16-subtitle-extraction-design.md`

---

## File Structure

```
core/subtitle/
  __init__.py       # 包导出
  schema.py         # Segment / SubtitleDoc + JSON/TXT 序列化与写盘
  base.py           # SubtitleSource Protocol
  ocr_source.py     # OCR 源：逐帧识别 → 带时间戳 segments（核心改造）
  track_source.py   # 字幕轨源：从平台 raw 找字幕文件并解析
  asr_source.py     # ASR 源：mlx-qwen3-asr 转写
  runner.py         # SubtitleRunner：按配置跑选定源，隔离失败
extract_text.py     # 改：退化为薄 CLI 壳
core/models.py      # 改：加 SubtitleConfig，AppConfig 增 subtitle 字段
core/config.py      # 改：_DEFAULTS 加 subtitle 块 + 解析
core/pipeline.py    # 改：视频下载成功后调 SubtitleRunner
tests/
  test_subtitle_schema.py
  test_subtitle_ocr_source.py
  test_subtitle_track_source.py
  test_subtitle_asr_source.py
  test_subtitle_runner.py
  test_subtitle_config.py
  test_pipeline_subtitle.py
```

---

## PHASE 1 — 基础（schema / 接口 / OCR 改造 / runner / CLI 壳）

### Task 1: schema —— Segment / SubtitleDoc + 序列化

**Files:**
- Create: `core/subtitle/__init__.py`
- Create: `core/subtitle/schema.py`
- Test: `tests/test_subtitle_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_schema.py`:

```python
import json
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc


def test_segment_rounds_times_to_2dp():
    s = Segment(id=0, start=1.2049, end=3.4561, text="你好")
    assert s.start == 1.20
    assert s.end == 3.46


def test_doc_to_json_dict_shape():
    doc = SubtitleDoc(
        source="asr", video="x.mp4", language="zh", duration=10.0,
        segments=[Segment(0, 1.0, 2.0, "甲"), Segment(1, 2.0, 3.0, "乙")],
    )
    d = doc.to_json_dict()
    assert d["source"] == "asr"
    assert d["video"] == "x.mp4"
    assert d["segments"][1] == {"id": 1, "start": 2.0, "end": 3.0, "text": "乙"}


def test_doc_to_txt_one_line_per_segment():
    doc = SubtitleDoc(
        source="ocr", video="x.mp4", language="zh", duration=5.0,
        segments=[Segment(0, 0.0, 1.0, "第一句"), Segment(1, 1.0, 2.0, "第二句")],
    )
    assert doc.to_txt() == "第一句\n第二句"


def test_doc_write_creates_json_and_txt(tmp_path: Path):
    doc = SubtitleDoc(
        source="ocr", video="v.mp4", language="zh", duration=2.0,
        segments=[Segment(0, 0.0, 1.0, "话")],
    )
    j, t = doc.write(tmp_path / "v.mp4")
    assert j == tmp_path / "v.ocr.json"
    assert t == tmp_path / "v.ocr.txt"
    loaded = json.loads(j.read_text(encoding="utf-8"))
    assert loaded["segments"][0]["text"] == "话"
    assert t.read_text(encoding="utf-8") == "话"


def test_empty_doc_writes_nothing_and_returns_none(tmp_path: Path):
    doc = SubtitleDoc(
        source="track", video="v.mp4", language="zh",
        duration=0.0, segments=[],
    )
    assert doc.write(tmp_path / "v.mp4") == (None, None)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/__init__.py`:

```python
"""Subtitle extraction package: track / ocr / asr sources → timestamped segments."""
```

Create `core/subtitle/schema.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_schema.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/__init__.py core/subtitle/schema.py tests/test_subtitle_schema.py
git commit -m "feat(subtitle): segment schema + JSON/TXT 写出"
```

---

### Task 2: base —— SubtitleSource 接口

**Files:**
- Create: `core/subtitle/base.py`
- Test: `tests/test_subtitle_runner.py`（先建文件，本任务只放接口测试）

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_runner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle.base'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/base.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_runner.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/base.py tests/test_subtitle_runner.py
git commit -m "feat(subtitle): SubtitleSource 接口"
```

---

### Task 3: ocr_source —— 带时间戳的 OCR 段累积（核心改造）

替换现有「全局去重塌一坨」逻辑：逐帧 OCR，按时间维护「打开中的段」。每个处理帧时间 `t = frame_idx / fps`。对当前帧识别出的每个文本块（经 `normalize` 归一化、`is_noise` 过滤后）：与打开中的段比对，相似（`SequenceMatcher` ≥ similarity）则把该段 `end` 延到 `t`；否则新开一段 `start=end=t`。任何段若超过 `gap` 秒没再出现就关闭。视频结束关闭所有段。

**Files:**
- Create: `core/subtitle/ocr_source.py`
- Test: `tests/test_subtitle_ocr_source.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_ocr_source.py`:

```python
from core.subtitle.ocr_source import SegmentAccumulator


def test_same_text_across_frames_merges_into_one_segment():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["你好世界"])
    acc.feed(0.5, ["你好世界"])
    acc.feed(1.0, ["你好世界"])
    segs = acc.finalize()
    assert len(segs) == 1
    assert segs[0].text == "你好世界"
    assert segs[0].start == 0.0
    assert segs[0].end == 1.0


def test_different_text_makes_separate_segments():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["第一句台词"])
    acc.feed(0.5, ["第一句台词"])
    acc.feed(2.0, ["完全不同的第二句"])
    segs = acc.finalize()
    assert [s.text for s in segs] == ["第一句台词", "完全不同的第二句"]
    assert segs[0].start == 0.0 and segs[0].end == 0.5
    assert segs[1].start == 2.0 and segs[1].end == 2.0


def test_reappearing_after_gap_closes_then_reopens():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["重复台词"])
    acc.feed(0.5, ["重复台词"])
    acc.feed(5.0, ["重复台词"])  # gap > 1.0 → 新段
    segs = acc.finalize()
    assert len(segs) == 2
    assert segs[0].end == 0.5
    assert segs[1].start == 5.0


def test_noise_blocks_filtered_out():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["15s", ">>>", "正常字幕内容"])
    segs = acc.finalize()
    assert [s.text for s in segs] == ["正常字幕内容"]


def test_ids_are_sequential():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["甲甲甲甲"])
    acc.feed(2.0, ["乙乙乙乙"])
    acc.feed(4.0, ["丙丙丙丙"])
    segs = acc.finalize()
    assert [s.id for s in segs] == [0, 1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_ocr_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle.ocr_source'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/ocr_source.py`. The noise/normalize helpers are ported verbatim from `extract_text.py:48-83` (keep behavior identical):

```python
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
            text = normalize(raw)
            if not text or is_noise(text):
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
    duration = total_frames / fps if fps else 0.0
    frame_step = max(1, int(fps * interval))

    acc = SegmentAccumulator(similarity=similarity, gap=max(interval * 3, 1.0))
    frame_idx = 0
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_ocr_source.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/ocr_source.py tests/test_subtitle_ocr_source.py
git commit -m "feat(subtitle): OCR 源带时间戳段累积"
```

---

### Task 4: runner —— 按配置跑选定源，隔离失败

**Files:**
- Create: `core/subtitle/runner.py`
- Test: `tests/test_subtitle_runner.py`（追加用例）

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subtitle_runner.py`:

```python
import logging
from pathlib import Path

from core.subtitle.runner import SubtitleRunner
from core.subtitle.schema import Segment, SubtitleDoc


class _Good:
    name = "ocr"

    def is_available(self):
        return True

    def extract(self, video_path, raw):
        return SubtitleDoc(
            source="ocr", video=Path(video_path).name, language="zh",
            duration=1.0, segments=[Segment(0, 0.0, 1.0, "ok")],
        )


class _Boom:
    name = "asr"

    def is_available(self):
        return True

    def extract(self, video_path, raw):
        raise RuntimeError("asr blew up")


class _Unavailable:
    name = "track"

    def is_available(self):
        return False

    def extract(self, video_path, raw):
        raise AssertionError("must not be called when unavailable")


def test_runner_writes_for_good_source(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good()], sources=["ocr"])
    written = runner.run(v, raw=None)
    assert (tmp_path / "v.ocr.json") in written
    assert (tmp_path / "v.ocr.txt").exists()


def test_runner_isolates_failing_source(tmp_path, caplog):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner([_Good(), _Boom()], sources=["ocr", "asr"])
    with caplog.at_level(logging.WARNING):
        written = runner.run(v, raw=None)
    assert (tmp_path / "v.ocr.json") in written
    assert not (tmp_path / "v.asr.json").exists()
    assert "asr" in caplog.text


def test_runner_skips_unavailable_and_unselected(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    runner = SubtitleRunner(
        [_Good(), _Unavailable()], sources=["ocr"]  # track not selected anyway
    )
    written = runner.run(v, raw=None)
    assert not (tmp_path / "v.track.json").exists()
    assert (tmp_path / "v.ocr.json") in written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle.runner'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/runner.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_runner.py -v`
Expected: PASS (4 passed — protocol test + 3 runner tests)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/runner.py tests/test_subtitle_runner.py
git commit -m "feat(subtitle): runner 按配置跑选定源 + 失败隔离"
```

---

### Task 5: extract_text.py 退化为薄 CLI 壳

保留所有现有命令行参数，行为默认仍只跑 OCR；新增 `--sources`。CLI 不再自己做 OCR，改调 `core.subtitle`。多进程/仪表盘逻辑移除（runner 单进程逐视频；批量由 CLI 循环文件列表实现），保持输出 json+txt。

**Files:**
- Modify: `extract_text.py`（整体重写为 CLI 壳）
- Test: `tests/test_subtitle_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_cli.py`:

```python
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_cli_help_lists_sources_flag():
    out = subprocess.run(
        [sys.executable, "extract_text.py", "--help"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "--sources" in out.stdout


def test_cli_errors_on_missing_path():
    out = subprocess.run(
        [sys.executable, "extract_text.py", "/no/such/path.mp4"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.returncode != 0
    assert "未找到视频" in (out.stdout + out.stderr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_cli.py -v`
Expected: FAIL on `test_cli_help_lists_sources_flag` (no `--sources` yet)

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `extract_text.py` with:

```python
"""视频字幕提取 CLI（薄壳）。

实际逻辑在 core.subtitle。默认只跑 OCR 源，保持历史行为；
可用 --sources 选择 track/ocr/asr。

用法:
    python extract_text.py video.mp4
    python extract_text.py ./downloads/ --collect
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_cli.py -v`
Expected: PASS (2 passed)

Then run the full suite to confirm no regression in the old extract-text tests (there are none referencing internals — confirm):

Run: `python -m pytest tests/ -q -k "subtitle or extract"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add extract_text.py tests/test_subtitle_cli.py
git commit -m "refactor(subtitle): extract_text.py 退化为薄 CLI 壳"
```

---

## PHASE 2 — 扩源（track / asr）

### Task 6: track_source —— 解析平台自带字幕轨

抖音 `aweme_detail` 字幕字段确切结构需用真实 raw 确认（见 spec §11）。本任务实现「给定 raw 提取字幕文件 URL → 下载 → 解析 WebVTT → segments」，URL 探测做成可扩展的候选键列表，无轨则返回 None。

**Files:**
- Create: `core/subtitle/track_source.py`
- Test: `tests/test_subtitle_track_source.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_track_source.py`:

```python
from pathlib import Path

from core.subtitle.track_source import TrackSource, parse_webvtt, find_caption_url


SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
第一句字幕

00:00:03.500 --> 00:00:06.000
第二句字幕
"""


def test_parse_webvtt_to_segments():
    segs = parse_webvtt(SAMPLE_VTT)
    assert len(segs) == 2
    assert segs[0].start == 1.0 and segs[0].end == 3.5
    assert segs[0].text == "第一句字幕"
    assert segs[1].start == 3.5 and segs[1].text == "第二句字幕"


def test_find_caption_url_from_known_field():
    raw = {
        "video": {
            "cla_info": {
                "caption_infos": [
                    {"url_list": ["https://example.com/cap.vtt"], "lang": "zh"}
                ]
            }
        }
    }
    assert find_caption_url(raw) == "https://example.com/cap.vtt"


def test_find_caption_url_none_when_absent():
    assert find_caption_url({"video": {}}) is None
    assert find_caption_url(None) is None


def test_extract_returns_none_without_track(tmp_path):
    src = TrackSource()
    assert src.extract(tmp_path / "v.mp4", raw={"video": {}}) is None


def test_extract_builds_doc(monkeypatch, tmp_path):
    src = TrackSource()
    monkeypatch.setattr(
        "core.subtitle.track_source._http_get_text",
        lambda url: SAMPLE_VTT,
    )
    raw = {"video": {"cla_info": {"caption_infos": [
        {"url_list": ["https://x/cap.vtt"], "lang": "zh"}]}}}
    doc = src.extract(tmp_path / "v.mp4", raw=raw)
    assert doc is not None
    assert doc.source == "track"
    assert len(doc.segments) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_track_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle.track_source'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/track_source.py`:

```python
"""Track subtitle source: pull a platform-provided caption file (WebVTT)
from the post's raw dict, parse to segments. No track → None."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from core.subtitle.schema import Segment, SubtitleDoc

_TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def _ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


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
        if urls:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_track_source.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/track_source.py tests/test_subtitle_track_source.py
git commit -m "feat(subtitle): track 源解析平台自带 WebVTT 字幕"
```

---

### Task 7: asr_source —— mlx-qwen3-asr 转写

仅 Apple Silicon 可用，重依赖懒加载，测试 mock 掉 `transcribe` 不跑真模型。

**Files:**
- Create: `core/subtitle/asr_source.py`
- Test: `tests/test_subtitle_asr_source.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_asr_source.py`:

```python
import sys
import types
from pathlib import Path

from core.subtitle.asr_source import ASRSource, _map_result


def test_map_result_to_segments():
    raw_result = {
        "language": "zh",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "你好"},
            {"start": 2.0, "end": 4.5, "text": "再见"},
        ],
    }
    lang, segs = _map_result(raw_result)
    assert lang == "zh"
    assert [s.text for s in segs] == ["你好", "再见"]
    assert segs[1].start == 2.0 and segs[1].end == 4.5
    assert [s.id for s in segs] == [0, 1]


def test_is_available_false_off_apple_silicon(monkeypatch):
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: False)
    assert ASRSource().is_available() is False


def test_extract_uses_transcribe(monkeypatch, tmp_path):
    fake_mod = types.SimpleNamespace(
        transcribe=lambda p, **kw: {
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.0, "text": "话"}],
        }
    )
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", fake_mod)
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: True)
    src = ASRSource(model="0.6b")
    doc = src.extract(tmp_path / "v.mp4", raw=None)
    assert doc is not None
    assert doc.source == "asr"
    assert doc.segments[0].text == "话"


def test_extract_returns_none_when_no_segments(monkeypatch, tmp_path):
    fake_mod = types.SimpleNamespace(
        transcribe=lambda p, **kw: {"language": "zh", "segments": []}
    )
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", fake_mod)
    monkeypatch.setattr("core.subtitle.asr_source._is_apple_silicon",
                        lambda: True)
    assert ASRSource().extract(tmp_path / "v.mp4", raw=None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_asr_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.subtitle.asr_source'`

- [ ] **Step 3: Write minimal implementation**

Create `core/subtitle/asr_source.py`:

```python
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
        duration = segs[-1].end if segs else 0.0
        return SubtitleDoc(
            source="asr", video=Path(video_path).name,
            language=lang, duration=duration, segments=segs,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_asr_source.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/subtitle/asr_source.py tests/test_subtitle_asr_source.py
git commit -m "feat(subtitle): ASR 源 mlx-qwen3-asr 转写"
```

---

## PHASE 3 — 集成（配置 + pipeline）

### Task 8: 配置 —— SubtitleConfig + AppConfig + config.py 默认/解析

**Files:**
- Modify: `core/models.py`（加 `SubtitleConfig`，`AppConfig` 加字段）
- Modify: `core/config.py:24-37`（`_DEFAULTS` 加 subtitle）+ AppConfig 构造处
- Test: `tests/test_subtitle_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_subtitle_config.py`:

```python
from core.config import load_config


def test_subtitle_defaults_off(tmp_path):
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\n", encoding="utf-8"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.subtitle.enabled is False
    assert cfg.subtitle.sources == ["track", "ocr", "asr"]
    assert cfg.subtitle.asr_model == "0.6b"


def test_subtitle_parsed_from_yaml(tmp_path):
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\n"
        "subtitle:\n"
        "  enabled: true\n"
        "  sources: [ocr]\n"
        "  asr:\n    model: '1.7b'\n"
        "  ocr:\n    interval: 0.3\n    similarity: 0.8\n",
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    assert cfg.subtitle.enabled is True
    assert cfg.subtitle.sources == ["ocr"]
    assert cfg.subtitle.asr_model == "1.7b"
    assert cfg.subtitle.ocr_interval == 0.3
    assert cfg.subtitle.ocr_similarity == 0.8
```

Confirm the loader entrypoint name first:

Run: `grep -n "^def load_config\|^def load\b" core/config.py`
If the public function is not `load_config`, use the actual name in the test and Task 9.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subtitle_config.py -v`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'subtitle'`

- [ ] **Step 3: Write minimal implementation**

In `core/models.py`, add after `DownloadOptions`:

```python
@dataclass
class SubtitleConfig:
    enabled: bool = False
    sources: list[str] = field(default_factory=lambda: ["track", "ocr", "asr"])
    asr_model: str = "0.6b"
    ocr_interval: float = 0.5
    ocr_similarity: float = 0.7
```

(`field` is already imported in `core/models.py`.)

In `core/models.py`, add to `AppConfig` (after `log_level`):

```python
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
```

In `core/config.py`, add to `_DEFAULTS` dict (after `"log_level": "INFO",`):

```python
    "subtitle": {
        "enabled": False,
        "sources": ["track", "ocr", "asr"],
        "asr": {"model": "0.6b"},
        "ocr": {"interval": 0.5, "similarity": 0.7},
    },
```

Find where `AppConfig(...)` is constructed in `core/config.py`:

Run: `grep -n "AppConfig(" core/config.py`

Add a `SubtitleConfig` import at top of `core/config.py` (alongside the existing `from core.models import AppConfig, DownloadOptions`):

```python
from core.models import AppConfig, DownloadOptions, SubtitleConfig
```

At the `AppConfig(...)` construction site, build and pass `subtitle`. Insert before the `AppConfig(` call:

```python
    _sub = merged.get("subtitle", {}) or {}
    subtitle = SubtitleConfig(
        enabled=bool(_sub.get("enabled", False)),
        sources=list(_sub.get("sources", ["track", "ocr", "asr"])),
        asr_model=str((_sub.get("asr", {}) or {}).get("model", "0.6b")),
        ocr_interval=float((_sub.get("ocr", {}) or {}).get("interval", 0.5)),
        ocr_similarity=float((_sub.get("ocr", {}) or {}).get("similarity", 0.7)),
    )
```

Then add `subtitle=subtitle,` as a keyword argument inside the `AppConfig(...)` call.

(Note: `merged` is the post-defaults-merge dict. If the local variable holding the merged config has a different name at that site, use that name instead — inspect the surrounding 5 lines.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subtitle_config.py tests/test_config.py -v`
Expected: PASS (new tests pass, existing `test_config.py` still green)

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/config.py tests/test_subtitle_config.py
git commit -m "feat(subtitle): config 加 subtitle 块（默认关）"
```

---

### Task 9: pipeline 集成 —— 视频下载成功后调 SubtitleRunner

在 `_handle_single` 和 `_handle_list` 的 `download_media` 成功后，对产出目录里的视频文件调 runner，经 `asyncio.to_thread` 不阻塞事件循环。`enabled=False` 时完全不构造 runner。

**Files:**
- Modify: `core/pipeline.py`（`DownloadPipeline.__init__` 建 runner；两处下载成功后调用）
- Test: `tests/test_pipeline_subtitle.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_subtitle.py`:

```python
from pathlib import Path

from core.pipeline import build_subtitle_runner


def test_no_runner_when_disabled():
    class Cfg:
        class subtitle:
            enabled = False
            sources = ["ocr"]
            asr_model = "0.6b"
            ocr_interval = 0.5
            ocr_similarity = 0.7
    assert build_subtitle_runner(Cfg()) is None


def test_runner_built_with_selected_sources_when_enabled():
    class Sub:
        enabled = True
        sources = ["ocr", "track"]
        asr_model = "0.6b"
        ocr_interval = 0.5
        ocr_similarity = 0.7

    class Cfg:
        subtitle = Sub()

    runner = build_subtitle_runner(Cfg())
    assert runner is not None
    names = {impl.name for impl in runner._impls}
    assert "ocr" in names and "track" in names
    assert runner._selected == {"ocr", "track"}


def test_collect_video_files_finds_mp4(tmp_path):
    from core.pipeline import _collect_video_files
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.mov").write_bytes(b"x")
    found = sorted(p.name for p in _collect_video_files([str(tmp_path)]))
    assert found == ["a.mp4", "c.mov"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_subtitle.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_subtitle_runner' from 'core.pipeline'`

- [ ] **Step 3: Write minimal implementation**

At the top of `core/pipeline.py` add imports (with the other `core.*` imports):

```python
import asyncio
from pathlib import Path

from core.subtitle.runner import SubtitleRunner
from core.subtitle.ocr_source import OCRSource
from core.subtitle.track_source import TrackSource
from core.subtitle.asr_source import ASRSource
```

(If `asyncio` / `Path` are already imported there, do not duplicate.)

Add module-level helpers in `core/pipeline.py`:

```python
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"}


def _collect_video_files(roots: list[str]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        p = Path(r)
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
            out.append(p)
        elif p.is_dir():
            for ext in _VIDEO_EXTS:
                out.extend(p.rglob(f"*{ext}"))
    return out


def build_subtitle_runner(config) -> SubtitleRunner | None:
    sub = config.subtitle
    if not sub.enabled:
        return None
    impls = [
        OCRSource(interval=sub.ocr_interval, similarity=sub.ocr_similarity),
        TrackSource(),
        ASRSource(model=sub.asr_model),
    ]
    return SubtitleRunner(impls, sources=list(sub.sources))
```

In `DownloadPipeline.__init__` (find it: `grep -n "class DownloadPipeline\|def __init__" core/pipeline.py`), after existing assignments add:

```python
        self._subtitle_runner = build_subtitle_runner(config)
```

Add a helper method on `DownloadPipeline`:

```python
    async def _run_subtitles(self, result) -> None:
        if self._subtitle_runner is None or not result.success:
            return
        videos = _collect_video_files(result.task.file_paths)
        for v in videos:
            await asyncio.to_thread(
                self._subtitle_runner.run, v, None
            )
```

In `_handle_single`, immediately after the `download_media` block (after `self._dashboard.clear_current_item()` on the single path), add:

```python
        await self._run_subtitles(result)
```

In `_handle_list`, inside the `for i, item in enumerate(all_items):` loop, after `self._dashboard.clear_current_item()` and before the limit-counter block, add:

```python
                await self._run_subtitles(result)
```

(`result` is the `download_media` return already in scope at both sites — confirm variable name via the lines shown in the spec context. Pass `None` as raw for now; wiring `item.raw` through is a follow-up — track_source simply returns None without it, which is the documented no-track behavior.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline_subtitle.py -v`
Expected: PASS (3 passed)

Then full regression:

Run: `python -m pytest tests/ -q`
Expected: all pass (no existing pipeline test broken — subtitle disabled by default so `_run_subtitles` early-returns)

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_pipeline_subtitle.py
git commit -m "feat(subtitle): pipeline 下载成功后调 SubtitleRunner（默认关）"
```

---

## PHASE 4 — 收尾

### Task 10: 文档与依赖说明

**Files:**
- Modify: `README.md`（如有「配置」节，加 subtitle 块说明）
- Create/Modify: `requirements.txt` 或安装说明（加可选依赖注释）

- [ ] **Step 1: 在 README 配置段补充 subtitle 用法**

在 README 配置说明处加入：

````markdown
### 字幕提取（可选，默认关）

```yaml
subtitle:
  enabled: true
  sources: [track, ocr, asr]   # 可只填子集
  asr:
    model: "0.6b"              # 或 1.7b
  ocr:
    interval: 0.5
    similarity: 0.7
```

依赖（按需安装）：
- ocr：`pip install opencv-python rapidocr-onnxruntime`
- asr：`pip install mlx-qwen3-asr`（仅 Apple Silicon）
- track：无额外依赖

也可独立使用：`python extract_text.py <视频或目录> --sources ocr,asr`
````

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs(subtitle): README 补充字幕提取配置与依赖"
```

---

## Self-Review 结论

- **Spec 覆盖**：track/ocr/asr 三源 → Task 6/3/7；Whisper segments JSON+TXT → Task 1；OCR 时间戳改造 → Task 3；runner 失败隔离 → Task 4；CLI 壳保留 → Task 5；config 默认关 → Task 8；pipeline 集成 + 非视频跳过（无视频文件则 `_collect_video_files` 为空，自然跳过）→ Task 9；分阶段 → Phase 1/2/3 对应。spec §11 未决项在 Task 6/7 注明用真实样例/文档确认。
- **占位符**：无。所有步骤均含完整可用代码与命令，无 TBD/TODO/「类似上文」。
- **类型一致**：`Segment(id,start,end,text)`、`SubtitleDoc(source,video,language,duration,segments)`、`SubtitleSource.{name,is_available,extract}`、`SubtitleRunner(impls, sources=[...])`、`build_subtitle_runner(config)`、`_collect_video_files(roots)`、`SubtitleConfig` 字段名 全程一致。
- **已知实现期风险**：抖音字幕轨真实字段（Task 6）、mlx-qwen3-asr 真实 API 形态（Task 7）——均已在任务内标注先确认；config.py 的 `AppConfig(...)` 构造点与 merged 变量名需执行者按现场确认（Task 8 已注明）。
