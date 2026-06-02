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
                        text = data["choices"][0]["message"]["content"]
                    except (KeyError, IndexError) as exc:
                        raise VLMError(f"响应缺少文本: {data}") from exc
                    if not isinstance(text, str):
                        raise VLMError(f"响应 content 不是字符串: {text!r}")
                    return text
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.RequestException as exc:
                last = str(exc)
            if attempt < self._retry:
                time.sleep(1.0 * (attempt + 1))
        raise VLMError(f"转录请求失败（重试 {self._retry} 次）: {last}")
