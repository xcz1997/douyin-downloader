"""Tests for TUI-T12: 字幕区 OCR interval/similarity 输入（parse_ocr_param + 接缝）。"""

import pytest


# ── 纯函数 parse_ocr_param ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("",      None),
    ("  ",    None),
    ("abc",   None),
    ("0",     None),
    ("-1",    None),
    ("-0.5",  None),
    ("0.5",   0.5),
    ("  0.7 ", 0.7),
    ("1",     1.0),
    ("2.5",   2.5),
])
def test_parse_ocr_param(raw, expected):
    from tui.panels.subtitle import parse_ocr_param
    assert parse_ocr_param(raw) == expected


# ── similarity 越界规则：>1 或 <=0 视同无效 ───────────────────────────────────

@pytest.mark.parametrize("raw,valid", [
    ("0.7",  True),
    ("1",    True),
    ("0.01", True),
    ("1.1",  False),  # >1 越界
    ("2",    False),  # >1 越界
])
def test_similarity_range_check(raw, valid):
    """similarity 用 parse_ocr_param 再做 0 < v <= 1 检查。"""
    from tui.panels.subtitle import parse_ocr_param
    v = parse_ocr_param(raw)
    if valid:
        assert v is not None and 0 < v <= 1
    else:
        # 越界时 v 非 None 但 >1，调用方应回退到默认
        assert v is None or v > 1


# ── compose 冒烟：#sub-ocr-interval / #sub-ocr-similarity 存在且有默认值 ────────

@pytest.mark.asyncio
async def test_ocr_inputs_exist_with_defaults():
    """compose 后两个 Input 存在，默认 value "0.5" / "0.7"。"""
    from tui.app import DownloaderApp
    from textual.widgets import Input

    app = DownloaderApp(config_path="config.yml")
    async with app.run_test() as pilot:
        app.show_section("字幕")
        await pilot.pause()
        from tui.panels.subtitle import SubtitlePanel
        panel = app.query_one(SubtitlePanel)
        interval_inp = panel.query_one("#sub-ocr-interval", Input)
        similarity_inp = panel.query_one("#sub-ocr-similarity", Input)
        assert interval_inp.value == "0.5"
        assert similarity_inp.value == "0.7"

        # 两个 Input 各有可区分的 Label（否则界面分不清谁是谁）
        from textual.widgets import Label
        interval_lbl = panel.query_one("#sub-ocr-interval-label", Label)
        similarity_lbl = panel.query_one("#sub-ocr-similarity-label", Label)
        interval_txt = str(interval_lbl.render())
        similarity_txt = str(similarity_lbl.render())
        assert "间隔" in interval_txt
        assert "相似度" in similarity_txt
        assert interval_txt != similarity_txt


# ── 接缝：合法值传入 OCRSource ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ocr_params_passed_to_ocr_source(monkeypatch):
    """interval="0.3" + similarity="0.9" → OCRSource(interval=0.3, similarity=0.9)。"""
    import core.subtitle.ocr_source as ocr_mod

    captured = {}

    class FakeOCRSource:
        def __init__(self, interval: float = 0.5, similarity: float = 0.7):
            captured["interval"] = interval
            captured["similarity"] = similarity

    monkeypatch.setattr(ocr_mod, "OCRSource", FakeOCRSource)

    from tui.app import DownloaderApp
    from tui.widgets import LogPane

    app = DownloaderApp(config_path="config.yml")
    async with app.run_test() as pilot:
        app.show_section("字幕")
        await pilot.pause()
        from tui.panels.subtitle import SubtitlePanel
        from textual.widgets import Input
        panel = app.query_one(SubtitlePanel)

        # 设置参数值
        panel.query_one("#sub-ocr-interval", Input).value = "0.3"
        panel.query_one("#sub-ocr-similarity", Input).value = "0.9"

        # 直接调 _run_subtitle（绕过路径检查），传 ocr_interval/ocr_similarity
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            vid_path = f.name
        try:
            panel._run_subtitle({
                "path": vid_path,
                "sources": ["ocr"],
                "asr_model": "0.6b",
                "ocr_interval": 0.3,
                "ocr_similarity": 0.9,
            })
            # 等 thread worker 完成
            for _ in range(30):
                if "interval" in captured:
                    break
                await pilot.pause(0.05)
        finally:
            os.unlink(vid_path)

    assert captured.get("interval") == 0.3
    assert captured.get("similarity") == 0.9


# ── 回退：空/非法/similarity 越界 → OCRSource 用默认 ──────────────────────────

@pytest.mark.asyncio
async def test_ocr_params_fallback_to_defaults(monkeypatch):
    """interval="" + similarity="2"(越界) → OCRSource(interval=0.5, similarity=0.7)。"""
    import core.subtitle.ocr_source as ocr_mod

    captured = {}

    class FakeOCRSource:
        def __init__(self, interval: float = 0.5, similarity: float = 0.7):
            captured["interval"] = interval
            captured["similarity"] = similarity

    monkeypatch.setattr(ocr_mod, "OCRSource", FakeOCRSource)

    from tui.app import DownloaderApp

    app = DownloaderApp(config_path="config.yml")
    async with app.run_test() as pilot:
        app.show_section("字幕")
        await pilot.pause()
        from tui.panels.subtitle import SubtitlePanel
        panel = app.query_one(SubtitlePanel)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            vid_path = f.name
        try:
            # interval=None（空/非法回退）、similarity=None（越界回退）→ 不传 kwarg
            panel._run_subtitle({
                "path": vid_path,
                "sources": ["ocr"],
                "asr_model": "0.6b",
                "ocr_interval": None,
                "ocr_similarity": None,
            })
            for _ in range(30):
                if "interval" in captured:
                    break
                await pilot.pause(0.05)
        finally:
            os.unlink(vid_path)

    # 未传 kwarg → OCRSource 用自身默认值
    assert captured.get("interval") == 0.5
    assert captured.get("similarity") == 0.7


# ── build_runner_spec 向后兼容：不传 ocr 参数时返回 None ─────────────────────

def test_build_runner_spec_ocr_defaults_to_none():
    """不传 ocr_interval/ocr_similarity → spec 里值为 None（向后兼容）。"""
    from tui.panels.subtitle import build_runner_spec
    spec = build_runner_spec(
        path="/v/a.mp4", sources=["ocr"], asr_model="0.6b"
    )
    assert spec.get("ocr_interval") is None
    assert spec.get("ocr_similarity") is None


def test_build_runner_spec_ocr_params_passed_through():
    """传 ocr_interval=0.3, ocr_similarity=0.9 → spec 里保留。"""
    from tui.panels.subtitle import build_runner_spec
    spec = build_runner_spec(
        path="/v/a.mp4", sources=["ocr"], asr_model="0.6b",
        ocr_interval=0.3, ocr_similarity=0.9,
    )
    assert spec["ocr_interval"] == 0.3
    assert spec["ocr_similarity"] == 0.9
