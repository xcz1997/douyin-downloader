"""Tests for TUI-T11: 下载区「同时提取字幕」Checkbox（extract_subtitle 覆盖接缝）。"""

import pytest


# ── compose 冒烟：#dl-subtitle 存在且默认不勾选 ───────────────────────────────

@pytest.mark.asyncio
async def test_subtitle_checkbox_exists_and_unchecked():
    """compose 后 #dl-subtitle Checkbox 存在，默认 .value is False。"""
    from tui.app import DownloaderApp
    from textual.widgets import Checkbox

    app = DownloaderApp(config_path="config.yml")
    async with app.run_test() as pilot:
        app.show_section("下载")
        await pilot.pause()
        from tui.panels.download import DownloadPanel
        panel = app.query_one(DownloadPanel)
        cb = panel.query_one("#dl-subtitle", Checkbox)
        assert cb.value is False


# ── 勾选透传：extract_subtitle=True ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_subtitle_checked_passes_true(monkeypatch):
    """勾选 → start_download 被调时 extract_subtitle=True。"""
    calls = {}

    async def fake_run(self, links, sink, interactive,
                       concurrency_override=None, extract_subtitle=False):
        calls["extract_subtitle"] = extract_subtitle
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    from tui.panels.download import DownloadPanel
    panel = DownloadPanel(config_path="config.yml")
    await panel.start_download(
        source="manual",
        manual_url="https://v.douyin.com/Z",
        extract_subtitle=True,
    )
    assert calls["extract_subtitle"] is True


# ── 不勾选透传：extract_subtitle=False（默认）────────────────────────────────

@pytest.mark.asyncio
async def test_subtitle_unchecked_passes_false(monkeypatch):
    """不勾选 → extract_subtitle=False（默认）。"""
    calls = {}

    async def fake_run(self, links, sink, interactive,
                       concurrency_override=None, extract_subtitle=False):
        calls["extract_subtitle"] = extract_subtitle
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    from tui.panels.download import DownloadPanel
    panel = DownloadPanel(config_path="config.yml")
    await panel.start_download(
        source="manual",
        manual_url="https://v.douyin.com/Z",
    )
    assert calls["extract_subtitle"] is False


# ── 接缝生效：extract_subtitle=True → cfg.subtitle.enabled=True ───────────────

@pytest.mark.asyncio
async def test_run_download_sets_subtitle_enabled_when_true(monkeypatch, tmp_path):
    """extract_subtitle=True → _run_download 在构造 DownloadPipeline 前
    将 cfg.subtitle.enabled 置为 True。"""
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\nthread: 3\n", encoding="utf-8"
    )

    from unittest.mock import AsyncMock, MagicMock

    fake_cfg = MagicMock()
    fake_cfg.thread = 3
    fake_cfg.links = []
    fake_cfg.save_path = str(tmp_path / "dl")
    fake_cfg.retry_times = 3
    fake_cfg.download = MagicMock(music=False, cover=False, json=False)
    fake_cfg.xhs = MagicMock(profile_dir=None)
    fake_cfg.subtitle = MagicMock(enabled=False)

    captured = {}

    import tui.panels.download as tpd
    monkeypatch.setattr(tpd, "ConfigLoader",
                        lambda path: MagicMock(load=MagicMock(return_value=fake_cfg)))

    import core.pipeline as cp

    def fake_pipeline_cls(**kw):
        captured["subtitle_enabled"] = kw["config"].subtitle.enabled
        m = MagicMock()
        m.run = AsyncMock()
        return m

    monkeypatch.setattr(cp, "DownloadPipeline", fake_pipeline_cls)

    import core.downloader_engine as de

    class FakeEngine:
        def __init__(self, **kw): pass
        async def close(self): pass

    monkeypatch.setattr(de, "DownloadEngine", FakeEngine)

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
        extract_subtitle=True,
    )
    assert captured["subtitle_enabled"] is True


# ── 不勾选不强制关：extract_subtitle=False 且 config 原本 enabled=True → 保持 True

@pytest.mark.asyncio
async def test_run_download_no_override_keeps_existing(monkeypatch, tmp_path):
    """extract_subtitle=False 时不改动 cfg.subtitle.enabled——
    即使 config 原本已是 True，也不强制关闭。"""
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\nthread: 3\n", encoding="utf-8"
    )

    from unittest.mock import AsyncMock, MagicMock

    fake_cfg = MagicMock()
    fake_cfg.thread = 3
    fake_cfg.links = []
    fake_cfg.save_path = str(tmp_path / "dl")
    fake_cfg.retry_times = 3
    fake_cfg.download = MagicMock(music=False, cover=False, json=False)
    fake_cfg.xhs = MagicMock(profile_dir=None)
    # config 原本 subtitle.enabled=True
    fake_cfg.subtitle = MagicMock(enabled=True)

    captured = {}

    import tui.panels.download as tpd
    monkeypatch.setattr(tpd, "ConfigLoader",
                        lambda path: MagicMock(load=MagicMock(return_value=fake_cfg)))

    import core.pipeline as cp

    def fake_pipeline_cls(**kw):
        captured["subtitle_enabled"] = kw["config"].subtitle.enabled
        m = MagicMock()
        m.run = AsyncMock()
        return m

    monkeypatch.setattr(cp, "DownloadPipeline", fake_pipeline_cls)

    import core.downloader_engine as de

    class FakeEngine:
        def __init__(self, **kw): pass
        async def close(self): pass

    monkeypatch.setattr(de, "DownloadEngine", FakeEngine)

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
        extract_subtitle=False,
    )
    # 不勾选时不强制关，原值 True 保持 True
    assert captured["subtitle_enabled"] is True
