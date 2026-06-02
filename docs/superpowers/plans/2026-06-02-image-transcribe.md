# 图文笔记图片转录 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给抖音图文笔记加一个用多模态大模型（VLM）把图片转成结构化文字稿的能力，TUI 面板 / 独立 CLI / 下载后自动触发三种入口，幂等、可配置、可选。

**Architecture:** 三层同构现有字幕功能——核心逻辑在 `core/transcribe/`，TUI 面板与 CLI 是薄壳。模型走通用 OpenAI-compatible vision 协议（base_url + model + 环境变量 key）。pipeline 集成照搬现有 `_subtitle_runner` / `_run_subtitles` 对称模式。

**Tech Stack:** Python 3、`requests`（项目已有，不引入新依赖）、dataclass 配置、Textual TUI、pytest + unittest.mock。

**Spec:** `docs/superpowers/specs/2026-06-02-image-transcribe-design.md`

**分支:** 已在 `feat/image-transcribe`（已含 note 短链修复）。

---

## 文件结构

| 文件 | 创建/修改 | 职责 |
|------|-----------|------|
| `core/models.py` | 修改 | 加 `TranscribeConfig` dataclass + `AppConfig.transcribe` 字段 |
| `core/config.py` | 修改 | DEFAULTS / generate_default / `_build_config` 解析 transcribe 段 |
| `core/transcribe/__init__.py` | 创建 | 包初始化，导出 `ImageTranscriber` / `VLMClient` / `build_image_transcriber` |
| `core/transcribe/prompt.py` | 创建 | 转录提示词常量 + 取用函数 |
| `core/transcribe/client.py` | 创建 | `VLMClient`：OpenAI-compatible vision 调用 |
| `core/transcribe/runner.py` | 创建 | `ImageTranscriber`：遍历图片→调 client→组装文字稿→写盘，含幂等；`build_transcribe_spec` 纯函数；`build_image_transcriber` 工厂 |
| `transcribe_images.py` | 创建 | 独立 CLI 薄壳 |
| `tui/panels/transcribe.py` | 创建 | `TranscribePanel`，照 `SubtitlePanel` |
| `tui/app.py` | 修改 | `_SECTIONS`/`_NAV_ICONS`/`_SECTION_ID` 加「转录」，compose 挂面板 |
| `core/pipeline.py` | 修改 | 加 `_transcriber` + `_run_transcribe(result)`，照 `_run_subtitles` |
| `config.yml` 注释 / README | 修改 | 配置说明 + 成本提示 |

---

## Task 1: 配置模型 TranscribeConfig

**Files:**
- Modify: `core/models.py`（在 `SubtitleConfig` 之后、`AppConfig` 之前加 dataclass；`AppConfig` 加字段）
- Modify: `core/config.py`（`_DEFAULTS`、`generate_default`、`_build_config`）
- Test: `tests/test_config_transcribe.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config_transcribe.py
import yaml
from core.config import ConfigLoader


def _write(tmp_path, data):
    p = tmp_path / "config.yml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_transcribe_defaults_when_absent(tmp_path):
    cfg = ConfigLoader(_write(tmp_path, {"links": ["x"]})).load()
    assert cfg.transcribe.enabled is False
    assert cfg.transcribe.auto_after_download is False
    assert cfg.transcribe.model  # 非空默认
    assert cfg.transcribe.api_key_env  # 非空默认
    assert cfg.transcribe.overwrite is False


def test_transcribe_parsed_from_yaml(tmp_path):
    data = {
        "links": ["x"],
        "transcribe": {
            "enabled": True,
            "auto_after_download": True,
            "base_url": "http://local/v1",
            "model": "my-vl",
            "api_key_env": "MY_KEY",
            "max_images": 5,
            "overwrite": True,
            "timeout": 30,
            "retry": 1,
        },
    }
    cfg = ConfigLoader(_write(tmp_path, data)).load()
    t = cfg.transcribe
    assert t.enabled and t.auto_after_download
    assert t.base_url == "http://local/v1"
    assert t.model == "my-vl"
    assert t.api_key_env == "MY_KEY"
    assert t.max_images == 5
    assert t.overwrite is True
    assert t.timeout == 30
    assert t.retry == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_config_transcribe.py -v`
Expected: FAIL（`AttributeError: 'AppConfig' object has no attribute 'transcribe'`）

- [ ] **Step 3: 加 dataclass 与字段**

`core/models.py`，在 `SubtitleConfig` 定义之后加：

```python
@dataclass
class TranscribeConfig:
    enabled: bool = False
    auto_after_download: bool = False
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-vl-max"
    api_key_env: str = "DASHSCOPE_API_KEY"
    max_images: int = 0          # 0 = 不限
    overwrite: bool = False      # 幂等：False=已存在跳过
    timeout: int = 60
    retry: int = 2
```

`core/models.py` 的 `AppConfig`，在 `subtitle: SubtitleConfig = ...` 之后加一行：

```python
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
```

- [ ] **Step 4: config.py 加默认与解析**

`core/config.py` 第 14 行 import 追加 `TranscribeConfig`：

```python
from core.models import AppConfig, DownloadOptions, SubtitleConfig, TranscribeConfig, XHSConfig
```

`_DEFAULTS` 字典里 `"xhs": {"profile_dir": ""},` 之前加：

```python
    "transcribe": {
        "enabled": False,
        "auto_after_download": False,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
        "api_key_env": "DASHSCOPE_API_KEY",
        "max_images": 0,
        "overwrite": False,
        "timeout": 60,
        "retry": 2,
    },
```

`generate_default` 的 `default_content` 里 `"xhs": {"profile_dir": ""},` 之前加同样的块，并在其上方加注释：

```python
            # 图片转录（默认关闭；改 enabled: true 并设置环境变量
            #   <api_key_env> 指定的 key 后生效）。走 OpenAI-compatible
            #   vision 协议，base_url/model 可换任意兼容服务。
            #   auto_after_download: true 时下载图文笔记后自动转录。
            "transcribe": {
                "enabled": False,
                "auto_after_download": False,
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-vl-max",
                "api_key_env": "DASHSCOPE_API_KEY",
                "max_images": 0,
                "overwrite": False,
                "timeout": 60,
                "retry": 2,
            },
```

`_build_config` 里 `subtitle = SubtitleConfig(...)` 块之后、`return AppConfig(...)` 之前加：

```python
        _tr = data.get("transcribe", {}) or {}
        transcribe = TranscribeConfig(
            enabled=bool(_tr.get("enabled", False)),
            auto_after_download=bool(_tr.get("auto_after_download", False)),
            base_url=str(_tr.get("base_url", TranscribeConfig.base_url)),
            model=str(_tr.get("model", TranscribeConfig.model)),
            api_key_env=str(_tr.get("api_key_env", TranscribeConfig.api_key_env)),
            max_images=int(_tr.get("max_images", 0)),
            overwrite=bool(_tr.get("overwrite", False)),
            timeout=int(_tr.get("timeout", 60)),
            retry=int(_tr.get("retry", 2)),
        )
```

并在 `return AppConfig(` 的参数里 `subtitle=subtitle,` 之后加 `transcribe=transcribe,`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_config_transcribe.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add core/models.py core/config.py tests/test_config_transcribe.py
git commit -m "feat(transcribe): 加 TranscribeConfig 配置模型与解析"
```

---

## Task 2: 转录提示词 prompt.py

**Files:**
- Create: `core/transcribe/__init__.py`（空文件占位，Task 4 再填导出）
- Create: `core/transcribe/prompt.py`
- Test: `tests/test_transcribe_prompt.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_transcribe_prompt.py
from core.transcribe.prompt import build_prompt


def test_prompt_has_core_constraints():
    p = build_prompt(image_count=7)
    assert "7" in p                 # 告知张数
    assert "原文" in p              # 保留原文
    assert "风景" in p              # 风景图标注约定
    assert "### 图" in p            # 分隔约定
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_transcribe_prompt.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'core.transcribe'`）

- [ ] **Step 3: 创建包与 prompt**

`core/transcribe/__init__.py`：空文件（一行注释即可）

```python
"""图文笔记图片转录：VLM 把图片转成结构化文字稿。"""
```

`core/transcribe/prompt.py`：

```python
"""图片转录提示词。复刻经验证有效的逐图转录约束。"""

from __future__ import annotations


def build_prompt(image_count: int) -> str:
    """构造转录提示词。

    Args:
        image_count: 本组图片张数，用于让模型知道要覆盖多少张。
    """
    return (
        f"请把下面 {image_count} 张图片（抖音图文笔记的配图）上"
        "所有可见的文字完整、逐字转录出来。\n"
        "要求：\n"
        "1. 保持原文用词、标点、emoji，不要改写、不要翻译、不要总结\n"
        "2. 每张图用「### 图N」分隔（N 从 1 开始）\n"
        "3. 若某张图没有文字（纯风景照），写「（无文字/风景图）」"
        "并用一句话简述画面内容\n"
        "4. 文字按图上从上到下、从左到右的阅读顺序排列\n"
        "5. 只输出转录正文，不要任何解释或寒暄"
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcribe_prompt.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/transcribe/__init__.py core/transcribe/prompt.py tests/test_transcribe_prompt.py
git commit -m "feat(transcribe): 加转录提示词模块"
```

---

## Task 3: VLM 客户端 client.py

**Files:**
- Create: `core/transcribe/client.py`
- Test: `tests/test_transcribe_client.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_transcribe_client.py
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.transcribe.client import VLMClient, VLMError


def _png(tmp_path) -> Path:
    p = tmp_path / "img0.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")  # 内容无所谓，client 只 base64
    return p


def test_builds_openai_vision_request_and_returns_text(tmp_path):
    img = _png(tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "### 图1\n你好"}}]
    }
    with patch("core.transcribe.client.requests.post",
               return_value=fake_resp) as post:
        client = VLMClient(base_url="http://x/v1", model="m",
                           api_key="k", timeout=30, retry=0)
        out = client.transcribe_images([img], "PROMPT")
    assert out == "### 图1\n你好"
    # 校验请求体
    kwargs = post.call_args.kwargs
    assert kwargs["json"]["model"] == "m"
    content = kwargs["json"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    b64 = base64.b64encode(img.read_bytes()).decode()
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{b64}"
    assert kwargs["headers"]["Authorization"] == "Bearer k"


def test_missing_key_raises():
    with pytest.raises(VLMError):
        VLMClient(base_url="http://x/v1", model="m",
                  api_key="", retry=0).transcribe_images([], "p")


def test_retries_then_raises(tmp_path):
    img = _png(tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "boom"
    with patch("core.transcribe.client.requests.post",
               return_value=fake_resp) as post, \
         patch("core.transcribe.client.time.sleep"):
        client = VLMClient(base_url="http://x/v1", model="m",
                           api_key="k", retry=2)
        with pytest.raises(VLMError):
            client.transcribe_images([img], "p")
    assert post.call_count == 3  # 1 + 2 retries
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_transcribe_client.py -v`
Expected: FAIL（`ModuleNotFoundError` / `ImportError`）

- [ ] **Step 3: 实现 client**

`core/transcribe/client.py`：

```python
"""OpenAI-compatible vision 客户端：把图片+提示词发给 VLM，返回文本。"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import requests


class VLMError(RuntimeError):
    """转录调用失败（key 缺失、网络、非预期响应）。"""


_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _data_uri(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower(), "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


class VLMClient:
    """单次 vision 调用封装。同步阻塞（调用方用线程隔离）。"""

    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout: int = 60, retry: int = 2) -> None:
        if not api_key:
            raise VLMError(
                "未提供 API key：请设置 config.transcribe.api_key_env "
                "指定的环境变量"
            )
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._key = api_key
        self._timeout = timeout
        self._retry = retry

    def transcribe_images(self, image_paths: list[Path], prompt: str) -> str:
        """把多张图片 + 提示词发给模型，返回转录文本。

        Raises:
            VLMError: 重试耗尽后仍失败，或响应缺少文本。
        """
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in image_paths:
            content.append(
                {"type": "image_url",
                 "image_url": {"url": _data_uri(Path(p))}}
            )
        payload = {"model": self._model,
                   "messages": [{"role": "user", "content": content}]}
        headers = {"Authorization": f"Bearer {self._key}",
                   "Content-Type": "application/json"}

        last = ""
        for attempt in range(self._retry + 1):
            try:
                resp = requests.post(self._url, json=payload,
                                     headers=headers, timeout=self._timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        return data["choices"][0]["message"]["content"]
                    except (KeyError, IndexError) as exc:
                        raise VLMError(f"响应缺少文本: {data}") from exc
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.RequestException as exc:
                last = str(exc)
            if attempt < self._retry:
                time.sleep(1.0 * (attempt + 1))
        raise VLMError(f"转录请求失败（重试 {self._retry} 次）: {last}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcribe_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add core/transcribe/client.py tests/test_transcribe_client.py
git commit -m "feat(transcribe): 加 OpenAI-compatible vision 客户端"
```

---

## Task 4: 转录器 runner.py（组装 + 幂等 + 工厂）

**Files:**
- Create: `core/transcribe/runner.py`
- Modify: `core/transcribe/__init__.py`（导出）
- Test: `tests/test_transcribe_runner.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_transcribe_runner.py
import json
from pathlib import Path

from core.models import TranscribeConfig
from core.transcribe.runner import (
    ImageTranscriber, build_image_transcriber, find_images, find_data_json,
)


class _FakeClient:
    def __init__(self, text="### 图1\nHELLO"):
        self.text = text
        self.calls = 0

    def transcribe_images(self, paths, prompt):
        self.calls += 1
        self.seen = list(paths)
        return self.text


def _make_note(dir_: Path, nickname="作者A", imgs=3):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "note_data.json").write_text(json.dumps({
        "aweme_id": "111", "desc": "正文内容",
        "author": {"nickname": nickname, "unique_id": "uidA"},
        "create_time": 1700000000,
        "statistics": {"digg_count": 10, "collect_count": 5,
                       "comment_count": 2, "share_count": 1},
        "text_extra": [{"hashtag_name": "标签1"}],
        "images": [{}] * imgs,
    }, ensure_ascii=False), encoding="utf-8")
    for i in range(imgs):
        (dir_ / f"image_{i}.webp").write_bytes(b"x")
    return dir_


def test_find_images_sorted_by_index(tmp_path):
    d = _make_note(tmp_path, imgs=3)
    (d / "image_10.webp").write_bytes(b"x")
    names = [p.name for p in find_images(d)]
    assert names == ["image_0.webp", "image_1.webp",
                     "image_2.webp", "image_10.webp"]


def test_transcribe_dir_writes_doc(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=False))
    out = t.transcribe_dir(d)
    assert out is not None and out.exists()
    text = out.read_text(encoding="utf-8")
    assert "作者A" in text
    assert "正文内容" in text          # 正文 desc
    assert "#标签1" in text            # 话题
    assert "点赞 10" in text           # 互动
    assert "### 图1" in text and "HELLO" in text  # 图片转录
    assert client.calls == 1


def test_idempotent_skip_when_exists(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=False))
    t.transcribe_dir(d)
    assert client.calls == 1
    second = t.transcribe_dir(d)       # 第二次应跳过
    assert second is None
    assert client.calls == 1


def test_overwrite_reruns(tmp_path):
    d = _make_note(tmp_path)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(overwrite=True))
    t.transcribe_dir(d)
    t.transcribe_dir(d)
    assert client.calls == 2


def test_max_images_truncates(tmp_path):
    d = _make_note(tmp_path, imgs=5)
    client = _FakeClient()
    t = ImageTranscriber(client, TranscribeConfig(max_images=2))
    t.transcribe_dir(d)
    assert len(client.seen) == 2


def test_build_factory_returns_none_when_disabled():
    assert build_image_transcriber(TranscribeConfig(enabled=False)) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_transcribe_runner.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 runner**

`core/transcribe/runner.py`：

```python
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
    imgs = [p for p in note_dir.iterdir()
            if p.suffix.lower() in _IMG_EXTS and _IMG_RE.search(p.name)]
    return sorted(imgs, key=lambda p: int(_IMG_RE.search(p.name).group(1)))


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


def build_image_transcriber(config: TranscribeConfig):
    """工厂：未启用返回 None；否则按配置建好 client 的 ImageTranscriber。

    供 pipeline 自动触发用——只在 enabled and auto_after_download 时生效。
    独立 CLI / TUI 面板不经过这里（它们即使 enabled=False 也能手动跑）。
    """
    if not config.enabled or not config.auto_after_download:
        return None
    api_key = os.environ.get(config.api_key_env, "")
    client = VLMClient(
        base_url=config.base_url, model=config.model, api_key=api_key,
        timeout=config.timeout, retry=config.retry,
    )
    return ImageTranscriber(client, config)
```

`core/transcribe/__init__.py` 改为导出：

```python
"""图文笔记图片转录：VLM 把图片转成结构化文字稿。"""

from core.transcribe.client import VLMClient, VLMError
from core.transcribe.runner import (
    ImageTranscriber, build_image_transcriber, build_transcribe_spec,
)

__all__ = [
    "VLMClient", "VLMError", "ImageTranscriber",
    "build_image_transcriber", "build_transcribe_spec",
]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcribe_runner.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add core/transcribe/runner.py core/transcribe/__init__.py tests/test_transcribe_runner.py
git commit -m "feat(transcribe): 加 ImageTranscriber（组装+幂等+工厂）"
```

---

## Task 5: 独立 CLI transcribe_images.py

**Files:**
- Create: `transcribe_images.py`
- Test: `tests/test_transcribe_cli.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_transcribe_cli.py
import json
from pathlib import Path

from transcribe_images import find_note_dirs


def _note(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "x_data.json").write_text("{}", encoding="utf-8")
    (d / "image_0.webp").write_bytes(b"x")


def test_find_note_dirs_self(tmp_path):
    _note(tmp_path)
    dirs = find_note_dirs(str(tmp_path))
    assert tmp_path in dirs


def test_find_note_dirs_recursive(tmp_path):
    _note(tmp_path / "作者" / "笔记1")
    _note(tmp_path / "作者" / "笔记2")
    dirs = find_note_dirs(str(tmp_path))
    assert len(dirs) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_transcribe_cli.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'transcribe_images'`）

- [ ] **Step 3: 实现 CLI**

`transcribe_images.py`：

```python
"""图文笔记图片转录 CLI（薄壳）。

用法:
    python transcribe_images.py ./downloads/某作者/某笔记/
    python transcribe_images.py ./downloads/           # 递归批量
    python transcribe_images.py <dir> --force --model qwen-vl-max
"""

import argparse
import os
import sys
from pathlib import Path

from core.config import ConfigLoader
from core.transcribe.client import VLMClient, VLMError
from core.transcribe.runner import ImageTranscriber, find_data_json


def find_note_dirs(path: str) -> list[Path]:
    """找出含 *_data.json 的笔记目录。给定目录自身命中则只返回自身，
    否则递归找子目录。"""
    p = Path(path)
    if not p.is_dir():
        return []
    if find_data_json(p) is not None:
        return [p]
    dirs = []
    for dj in sorted(p.rglob("*_data.json")):
        if dj.parent not in dirs:
            dirs.append(dj.parent)
    return dirs


def main() -> None:
    ap = argparse.ArgumentParser(description="图文笔记图片转录（VLM）")
    ap.add_argument("path", help="笔记目录或其父目录")
    ap.add_argument("-c", "--config", default="config.yml")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文字稿")
    ap.add_argument("--model", help="覆盖 config 的模型名")
    args = ap.parse_args()

    cfg = ConfigLoader(args.config).load().transcribe
    if args.force:
        cfg.overwrite = True
    if args.model:
        cfg.model = args.model

    api_key = os.environ.get(cfg.api_key_env, "")
    try:
        client = VLMClient(base_url=cfg.base_url, model=cfg.model,
                           api_key=api_key, timeout=cfg.timeout,
                           retry=cfg.retry)
    except VLMError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        sys.exit(1)

    transcriber = ImageTranscriber(client, cfg)
    dirs = find_note_dirs(args.path)
    if not dirs:
        print(f"未找到图文笔记目录: {args.path}", file=sys.stderr)
        sys.exit(1)

    print(f"找到 {len(dirs)} 个笔记目录")
    for i, d in enumerate(dirs, 1):
        try:
            out = transcriber.transcribe_dir(d)
            print(f"  [{i}/{len(dirs)}] {d.name} → "
                  f"{out.name if out else '跳过'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(dirs)}] {d.name} 失败: {exc}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_transcribe_cli.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add transcribe_images.py tests/test_transcribe_cli.py
git commit -m "feat(transcribe): 加独立 CLI transcribe_images.py"
```

---

## Task 6: TUI 转录面板

**Files:**
- Create: `tui/panels/transcribe.py`
- Modify: `tui/app.py`（`_SECTIONS`/`_NAV_ICONS`/`_SECTION_ID`/import/compose）
- Test: `tests/test_tui_transcribe_panel.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tui_transcribe_panel.py
from tui.panels.transcribe import build_transcribe_spec


def test_spec_rejects_empty():
    assert "error" in build_transcribe_spec("")


def test_spec_passes_path():
    assert build_transcribe_spec(" ./d ") == {"path": "./d"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_tui_transcribe_panel.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'tui.panels.transcribe'`）

- [ ] **Step 3: 实现面板**

`tui/panels/transcribe.py`（照 `SubtitlePanel` 结构，进度写 `LogPane`，线程跑阻塞调用）：

```python
"""转录面板：驱动 ImageTranscriber，在 Textual 线程 worker 中跑。"""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static

# 复用 runner 的纯函数；TUI 自己的 spec 校验也走它
from core.transcribe.runner import build_transcribe_spec  # noqa: F401


class TranscribePanel(Static):
    def __init__(self) -> None:
        super().__init__(id="panel-transcribe")
        self._worker = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(classes="card") as g_in:
                g_in.border_title = "输入"
                yield Input(placeholder="笔记目录或其父目录",
                            id="tr-path")
            with Vertical(classes="card") as g_opt:
                g_opt.border_title = "选项"
                yield Checkbox("覆盖重跑（默认已存在则跳过）",
                               value=False, id="tr-overwrite")
                yield Label("模型（留空用配置）")
                yield Input(placeholder="如 qwen-vl-max", id="tr-model")
            with Horizontal(classes="actions"):
                yield Button("开始转录", id="tr-start", variant="primary")
                yield Button("停止", id="tr-stop")
            yield Label("", id="tr-msg", classes="msg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tr-start":
            path = self.query_one("#tr-path", Input).value
            overwrite = self.query_one("#tr-overwrite", Checkbox).value
            model = self.query_one("#tr-model", Input).value.strip()
            self._dispatch(path, overwrite, model)
        elif event.button.id == "tr-stop":
            if self._worker is not None:
                self._worker.cancel()
                self.query_one("#tr-msg", Label).update("已停止")

    def _dispatch(self, path: str, overwrite: bool, model: str) -> None:
        spec = build_transcribe_spec(path)
        msg = self.query_one("#tr-msg", Label)
        if "error" in spec:
            msg.update(spec["error"])
            return
        msg.update("转录中…")
        self._run(spec["path"], overwrite, model)

    def _run(self, path: str, overwrite: bool, model: str) -> None:
        """线程 worker 跑 ImageTranscriber。测试整体 monkeypatch 此方法。"""
        from tui.widgets import LogPane
        try:
            log = self.app.query_one(LogPane)
        except Exception:
            return

        def work() -> None:
            try:
                from core.config import ConfigLoader
                from core.transcribe.client import VLMClient, VLMError
                from core.transcribe.runner import (
                    ImageTranscriber, find_data_json,
                )
                cfg = ConfigLoader("config.yml").load().transcribe
                if overwrite:
                    cfg.overwrite = True
                if model:
                    cfg.model = model
                api_key = os.environ.get(cfg.api_key_env, "")
                try:
                    client = VLMClient(
                        base_url=cfg.base_url, model=cfg.model,
                        api_key=api_key, timeout=cfg.timeout, retry=cfg.retry)
                except VLMError as exc:
                    self.app.call_from_thread(
                        log.write, f"[red]转录启动失败: {exc}[/red]")
                    return
                transcriber = ImageTranscriber(client, cfg)
                p = Path(path)
                if find_data_json(p) is not None:
                    dirs = [p]
                else:
                    dirs = sorted({dj.parent
                                   for dj in p.rglob("*_data.json")})
                if not dirs:
                    self.app.call_from_thread(
                        log.write, f"[yellow]未找到笔记目录: {path}[/yellow]")
                    return
                for d in dirs:
                    try:
                        out = transcriber.transcribe_dir(d)
                        self.app.call_from_thread(
                            log.write,
                            f"{d.name} → {out.name if out else '跳过'}")
                    except Exception as exc:  # noqa: BLE001
                        self.app.call_from_thread(
                            log.write, f"[red]{d.name} 失败: {exc}[/red]")
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(
                    log.write, f"[red]转录初始化失败: {exc}[/red]")

        self._worker = self.run_worker(work, thread=True, exclusive=True)
```

- [ ] **Step 4: app.py 接线**

`tui/app.py` import 区加：

```python
from tui.panels.transcribe import TranscribePanel
```

`_SECTIONS` 改为：

```python
_SECTIONS = ["下载", "字幕", "转录", "登录", "设置"]
```

`_NAV_ICONS` 加一个图标（在「字幕」`chr(0xF0F6)` 之后插入「转录」用 `chr(0xF02D)`＝book）：

```python
_NAV_ICONS = [chr(0xF019), chr(0xF0F6), chr(0xF02D), chr(0xF090), chr(0xF013)]
```

`_SECTION_ID` 加：

```python
_SECTION_ID: dict[str, str] = {
    "下载": "panel-download",
    "字幕": "panel-subtitle",
    "转录": "panel-transcribe",
    "登录": "panel-login",
    "设置": "panel-settings",
}
```

`compose` 里 `yield SubtitlePanel()` 之后加：

```python
                    yield TranscribePanel()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_tui_transcribe_panel.py tests/test_tui_app_smoke.py -v`
Expected: PASS（面板纯函数测试 + app smoke 仍通过）

- [ ] **Step 6: 提交**

```bash
git add tui/panels/transcribe.py tui/app.py tests/test_tui_transcribe_panel.py
git commit -m "feat(transcribe): 加 TUI 转录面板并接入侧边栏"
```

---

## Task 7: 下载流程自动触发

**Files:**
- Modify: `core/pipeline.py`（`__init__` 加 `_transcriber`；加 `_run_transcribe`；单 item 成功后调用）
- Test: `tests/test_pipeline_transcribe_hook.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_transcribe_hook.py
from pathlib import Path
from types import SimpleNamespace

from core.pipeline import DownloadPipeline


def _result(file_paths, media=1, success=True):
    task = SimpleNamespace(file_paths=file_paths)
    return SimpleNamespace(task=task, media_files_written=media,
                           success=success)


def test_run_transcribe_calls_per_dir(monkeypatch, tmp_path):
    calls = []
    fake = SimpleNamespace(
        transcribe_dir=lambda d: calls.append(Path(d)) or None)
    # 绕过 __init__ 直接构造一个空壳 pipeline
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = fake
    import asyncio
    asyncio.run(p._run_transcribe(_result([str(tmp_path)])))
    assert calls == [tmp_path]


def test_run_transcribe_noop_when_none():
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = None
    import asyncio
    asyncio.run(p._run_transcribe(_result(["/x"])))  # 不抛即可


def test_run_transcribe_skips_when_no_media():
    calls = []
    p = DownloadPipeline.__new__(DownloadPipeline)
    p._transcriber = SimpleNamespace(
        transcribe_dir=lambda d: calls.append(d))
    import asyncio
    asyncio.run(p._run_transcribe(_result(["/x"], media=0)))
    assert calls == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_pipeline_transcribe_hook.py -v`
Expected: FAIL（`AttributeError: 'DownloadPipeline' object has no attribute '_run_transcribe'`）

- [ ] **Step 3: 实现集成**

`core/pipeline.py` 顶部 import 区加：

```python
import asyncio
from core.transcribe.runner import build_image_transcriber
```

`__init__` 末尾 `self._subtitle_runner = build_subtitle_runner(config)` 之后加：

```python
        self._transcriber = build_image_transcriber(config.transcribe)
```

紧跟现有 `_run_subtitles` 方法之后，加对称的方法：

```python
    async def _run_transcribe(self, result) -> None:
        """下载成功的图文笔记，自动转录（失败只告警，不影响下载）。"""
        if self._transcriber is None:
            return
        if result.media_files_written <= 0:
            return
        for root in result.task.file_paths:
            try:
                await asyncio.to_thread(
                    self._transcriber.transcribe_dir, Path(root)
                )
            except Exception as exc:  # noqa: BLE001
                self._log.warn("自动转录失败", root=str(root), error=str(exc))
```

确认 `Path` 已在 `core/pipeline.py` 导入（文件已用 `p.glob`，应已 import；若无则加 `from pathlib import Path`）。

单 item 成功分支里，现有 `await self._run_subtitles(result)`（约 line 294）之后加一行：

```python
        await self._run_transcribe(result)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_pipeline_transcribe_hook.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归确认**

Run: `pytest tests/ -q -k "pipeline or transcribe or config"`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add core/pipeline.py tests/test_pipeline_transcribe_hook.py
git commit -m "feat(transcribe): 下载图文笔记后可选自动转录（照 _run_subtitles）"
```

---

## Task 8: 文档与配置说明

**Files:**
- Modify: `README.md`（加「图片转录」一节）
- Test: 无（文档）

- [ ] **Step 1: README 加一节**

在 README 合适位置（字幕功能附近）加：

```markdown
## 图文笔记图片转录

把图文笔记的配图用多模态大模型转成结构化文字稿（`文字稿_<作者>.md`）。

**配置**（`config.yml` 的 `transcribe` 段）：
- `enabled` / `auto_after_download`：总开关 / 下载后自动转录
- `base_url` / `model`：OpenAI-compatible vision 服务地址与模型
- `api_key_env`：从该环境变量读取 API key（不写进配置文件）
- `overwrite`：false 时已存在文字稿则跳过（幂等）
- `max_images`：单笔记最多转录张数，0 不限

**独立使用**：
```bash
export DASHSCOPE_API_KEY=sk-xxx
python transcribe_images.py ./downloads/某作者/某笔记/
python transcribe_images.py ./downloads/ --force
```

**TUI**：`python tui.py` → 侧边栏「转录」面板。

> 成本提示：每张图一次 vision 调用，费用随图片数线性增长；`overwrite=false`
> 的幂等避免重复付费。
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs(transcribe): README 加图片转录使用说明"
```

---

## 收尾验证

- [ ] 全量测试：`pytest tests/ -q` 全绿
- [ ] 真实冒烟（需 key）：`export DASHSCOPE_API_KEY=... && python transcribe_images.py ./JIN/douyin/某作者/某笔记/` 生成文字稿，再跑一次确认「跳过」（幂等）
- [ ] TUI 冒烟：`python tui.py`，「转录」面板可见、可输入路径、不崩
```
