"""Tests for TUI-T10: 下载区并发数 Input（parse_concurrency + 覆盖接缝）。"""

import pytest

from tui.panels.download import parse_concurrency


# ── 纯函数 parse_concurrency ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("",     None),
    ("  ",   None),
    ("0",    None),
    ("-3",   None),
    ("abc",  None),
    ("1.5",  None),
    ("5",    5),
    ("  8 ", 8),
    ("1",    1),
    ("100",  100),
])
def test_parse_concurrency(raw, expected):
    assert parse_concurrency(raw) == expected


# ── 覆盖生效：填 "7" → _run_download 调用时 cfg.thread 被设为 7 ──────────────

@pytest.mark.asyncio
async def test_concurrency_override_applied(monkeypatch):
    """填正整数 → _run_download 被调时 cfg.thread == 输入值。"""
    calls = {}

    async def fake_run(self, links, sink, interactive, concurrency_override):
        calls["concurrency_override"] = concurrency_override
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    from tui.panels.download import DownloadPanel
    panel = DownloadPanel(config_path="config.yml")
    await panel.start_download(
        source="manual",
        manual_url="https://v.douyin.com/Z",
        concurrency_override=7,
    )
    assert calls["concurrency_override"] == 7


# ── 留空回退：不传 override → 不覆盖 cfg.thread ───────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_empty_no_override(monkeypatch, tmp_path):
    """并发 Input 留空 → concurrency_override=None，cfg.thread 维持 config 原值。"""
    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./x\nthread: 3\n", encoding="utf-8")

    calls = {}

    async def fake_run(self, links, sink, interactive, concurrency_override):
        calls["concurrency_override"] = concurrency_override
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    from tui.panels.download import DownloadPanel
    panel = DownloadPanel(config_path=str(cfg))
    await panel.start_download(
        source="manual",
        manual_url="https://v.douyin.com/Z",
        concurrency_override=None,
    )
    assert calls["concurrency_override"] is None


# ── 非法值回退：和留空一样视为 None ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_invalid_no_override(monkeypatch):
    """非法字符串经 parse_concurrency 后是 None，最终不覆盖。"""
    for bad in ("abc", "0", "-1", ""):
        assert parse_concurrency(bad) is None


# ── cfg.thread 在 _run_download 内被正确赋值 ──────────────────────────────────

@pytest.mark.asyncio
async def test_run_download_sets_cfg_thread(monkeypatch, tmp_path):
    """当 concurrency_override=7 时，_run_download 真实实现在构造 engine
    前将 cfg.thread 改为 7（通过 monkeypatch ConfigLoader.load）。"""
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\nthread: 3\n", encoding="utf-8"
    )

    from unittest.mock import AsyncMock, MagicMock

    # 造一个假 config，thread 初始为 3
    fake_cfg = MagicMock()
    fake_cfg.thread = 3
    fake_cfg.links = []
    fake_cfg.save_path = str(tmp_path / "dl")
    fake_cfg.retry_times = 3
    fake_cfg.download = MagicMock(music=False, cover=False, json=False)
    fake_cfg.xhs = MagicMock(profile_dir=None)

    captured = {}

    # 拦住所有 core 组件，只记录 engine 构造时的 concurrency 值
    import core.config as cc
    monkeypatch.setattr(cc, "ConfigLoader", lambda path: MagicMock(load=MagicMock(return_value=fake_cfg)))

    import core.downloader_engine as de
    original_engine_cls = de.DownloadEngine

    class FakeEngine:
        def __init__(self, **kw):
            captured["concurrency"] = kw.get("concurrency")

        async def close(self):
            pass

    monkeypatch.setattr(de, "DownloadEngine", FakeEngine)

    # 替换 pipeline 避免真跑网络
    import core.pipeline as cp
    fake_pipeline = MagicMock()
    fake_pipeline.run = AsyncMock()
    monkeypatch.setattr(cp, "DownloadPipeline", lambda **kw: fake_pipeline)

    # 阻断其余需要网络的 core 件
    import core.api_client as ca
    monkeypatch.setattr(ca, "DouyinAPIClient", MagicMock(return_value=MagicMock(
        close=AsyncMock(), update_cookie=MagicMock()
    )))
    import core.cookie as ck
    cookie_mock = MagicMock()
    cookie_mock.ensure_valid_cookie = AsyncMock(return_value=MagicMock(value="tok"))
    monkeypatch.setattr(ck, "CookieManager", MagicMock(return_value=cookie_mock))
    import core.logger as cl
    monkeypatch.setattr(cl, "DualLogger", MagicMock(return_value=MagicMock(
        get=MagicMock(return_value=None), close=MagicMock()
    )))
    import core.tracer as ct
    monkeypatch.setattr(ct, "Tracer", MagicMock(return_value=MagicMock(close=MagicMock())))
    import core.platforms.douyin as cdou
    monkeypatch.setattr(cdou, "DouyinPlatform", MagicMock())
    monkeypatch.setattr(cdou, "DouyinPlatformClient", MagicMock())
    import core.platforms.xhs as cxhs
    xhs_plat = MagicMock()
    xhs_plat.return_value.match_url = MagicMock(return_value=None)
    monkeypatch.setattr(cxhs, "XHSPlatform", xhs_plat)
    monkeypatch.setattr(cxhs, "XHSPlatformClient", MagicMock())
    import core.platform as cplat
    monkeypatch.setattr(cplat, "PlatformRegistry", MagicMock(return_value=MagicMock(
        register=MagicMock()
    )))

    from tui.panels.download import DownloadPanel
    from tui.sink import TextualSink

    panel = DownloadPanel(config_path=str(cfg_file))
    sink = TextualSink(lambda e: None)
    await panel._run_download(
        links=["https://v.douyin.com/X"],
        sink=sink,
        interactive=False,
        concurrency_override=7,
    )
    assert captured["concurrency"] == 7


# ── 占位读配置失败不崩 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_placeholder_bad_config(tmp_path):
    """指向不存在 config → panel compose 正常（占位用通用文案），不崩。"""
    from tui.app import DownloaderApp

    missing = str(tmp_path / "no_such.yml")
    app = DownloaderApp(config_path=missing)
    async with app.run_test() as pilot:
        app.show_section("下载")
        await pilot.pause()
        from tui.panels.download import DownloadPanel
        from textual.widgets import Input
        panel = app.query_one(DownloadPanel)
        inp = panel.query_one("#dl-concurrency", Input)
        # placeholder 非空（通用兜底文案）
        assert inp.placeholder != ""
