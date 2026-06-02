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
        VLMClient(base_url="http://x/v1", model="m", api_key="")


def test_bad_response_raises(tmp_path):
    img = _png(tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"choices": []}
    with patch("core.transcribe.client.requests.post", return_value=fake_resp):
        client = VLMClient(base_url="http://x/v1", model="m", api_key="k", retry=0)
        with pytest.raises(VLMError):
            client.transcribe_images([img], "p")


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
