"""ImageTranscriber：遍历笔记目录图片→VLM→组装文字稿→写盘，含幂等。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from core.models import TranscribeConfig
from core.transcribe.client import VLMClient, VLMError
from core.transcribe.prompt import build_prompt

log = logging.getLogger("transcribe")

_IMG_EXTS = (".webp", ".jpg", ".jpeg", ".png")
_IMG_RE = re.compile(r"image_(\d+)\.")


def find_images(note_dir: Path) -> list[Path]:
    """目录内 image_N.* 按数字序号排序。"""
    imgs = [
        (m, p)
        for p in note_dir.iterdir()
        if p.suffix.lower() in _IMG_EXTS and (m := _IMG_RE.search(p.name))
    ]
    return [p for _, p in sorted(imgs, key=lambda x: int(x[0].group(1)))]


def find_data_json(note_dir: Path) -> Path | None:
    matches = sorted(note_dir.glob("*_data.json"))
    return matches[0] if matches else None


def _fmt_doc(meta: dict, img_text: str) -> str:
    a = meta.get("author") or {}
    st = meta.get("statistics") or {}
    nickname = a.get("nickname") or "unknown"
    ct = meta.get("create_time")
    ts = datetime.fromtimestamp(ct).strftime("%Y-%m-%d") if ct else "?"
    tags = [t.get("hashtag_name") for t in (meta.get("text_extra") or [])
            if t.get("hashtag_name")]
    lines = [
        f"# {nickname} · 图文笔记文字稿\n",
        "## 关键信息",
        f"- 作者：{nickname}（抖音号 {a.get('unique_id') or '—'}）",
        f"- 发布日期：{ts}",
        f"- aweme_id：{meta.get('aweme_id')}",
        f"- 图片数：{len(meta.get('images') or [])}",
        f"- 互动：点赞 {st.get('digg_count')} / 收藏 {st.get('collect_count')}"
        f" / 评论 {st.get('comment_count')} / 分享 {st.get('share_count')}",
        f"- 话题：{' '.join('#' + t for t in tags) if tags else '—'}\n",
        "## 作者正文文案",
        (meta.get("desc") or "").strip() + "\n",
        "## 图片内容（识别转录）",
        img_text.strip(),
    ]
    return "\n".join(lines)


def doc_path(note_dir: Path, meta: dict) -> Path:
    nickname = (meta.get("author") or {}).get("nickname") or "unknown"
    return note_dir / f"文字稿_{nickname}.md"


class ImageTranscriber:
    """对单个笔记目录做转录。失败抛出，由调用方决定是否吞掉。"""

    def __init__(self, client, config: TranscribeConfig) -> None:
        self._client = client
        self._cfg = config

    def transcribe_dir(self, note_dir: Path) -> Path | None:
        """转录一个笔记目录。返回写出的文字稿路径；跳过时返回 None。"""
        note_dir = Path(note_dir)
        dj = find_data_json(note_dir)
        if dj is None:
            log.warning("无 data.json，跳过: %s", note_dir)
            return None
        meta = json.loads(dj.read_text(encoding="utf-8"))
        out = doc_path(note_dir, meta)
        if out.exists() and not self._cfg.overwrite:
            log.info("已转录，跳过: %s", out.name)
            return None
        imgs = find_images(note_dir)
        if not imgs:
            log.warning("无图片，跳过: %s", note_dir)
            return None
        if self._cfg.max_images and len(imgs) > self._cfg.max_images:
            log.warning("图片 %d 张超过 max_images=%d，仅转录前 %d 张",
                        len(imgs), self._cfg.max_images, self._cfg.max_images)
            imgs = imgs[: self._cfg.max_images]
        prompt = build_prompt(len(imgs))
        img_text = self._client.transcribe_images(imgs, prompt)
        out.write_text(_fmt_doc(meta, img_text), encoding="utf-8")
        return out


def build_transcribe_spec(path: str) -> dict:
    """纯函数：校验 CLI/面板输入。空路径→error。"""
    if not path or not path.strip():
        return {"error": "未提供路径"}
    return {"path": path.strip()}


def find_note_dirs(path: str) -> list[Path]:
    """找出含 *_data.json 的笔记目录。给定目录自身命中则只返回自身，
    否则递归找所有含 data.json 的子目录。"""
    p = Path(path)
    if not p.is_dir():
        return []
    if find_data_json(p) is not None:
        return [p]
    seen: list[Path] = []
    for dj in sorted(p.rglob("*_data.json")):
        if dj.parent not in seen:
            seen.append(dj.parent)
    return seen


def build_image_transcriber(config: TranscribeConfig):
    """工厂：未启用返回 None；否则按配置建好 client 的 ImageTranscriber。

    供 pipeline 自动触发用——只在 enabled and auto_after_download 时生效。
    独立 CLI / TUI 面板不经过这里（它们即使 enabled=False 也能手动跑）。
    """
    if not config.enabled or not config.auto_after_download:
        return None
    api_key = os.environ.get(config.api_key_env, "")
    try:
        client = VLMClient(
            base_url=config.base_url, model=config.model, api_key=api_key,
            timeout=config.timeout, retry=config.retry,
        )
    except VLMError as exc:
        log.warning(
            "自动转录已启用但客户端初始化失败（下载不受影响）: %s", exc
        )
        return None
    return ImageTranscriber(client, config)
