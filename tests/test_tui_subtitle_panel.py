import pytest

from tui.panels.subtitle import SubtitlePanel, build_runner_spec


def test_build_runner_spec_collects_sources():
    spec = build_runner_spec(
        path="/v/a.mp4", sources=["ocr", "asr"], asr_model="1.7b",
    )
    assert spec["path"] == "/v/a.mp4"
    assert spec["sources"] == ["ocr", "asr"]
    assert spec["asr_model"] == "1.7b"


def test_build_runner_spec_rejects_empty_sources():
    spec = build_runner_spec(path="/v/a.mp4", sources=[], asr_model="0.6b")
    assert spec["error"] == "未选择任何字幕源"


@pytest.mark.asyncio
async def test_panel_start_invokes_runner(monkeypatch):
    seen = {}

    def fake_run(self, spec):
        seen["spec"] = spec

    monkeypatch.setattr(
        "tui.panels.subtitle.SubtitlePanel._run_subtitle", fake_run
    )
    panel = SubtitlePanel()
    await panel.start_subtitle(
        path="/v/a.mp4", sources=["track"], asr_model="0.6b"
    )
    assert seen["spec"]["sources"] == ["track"]


@pytest.mark.asyncio
async def test_source_init_failure_is_isolated(monkeypatch, tmp_path):
    # ASRSource construction raising must NOT crash the app (spec §5
    # error isolation) — it should be caught and logged red.
    from tui.app import DownloaderApp
    from tui.widgets import LogPane

    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")

    import core.subtitle.asr_source as asr_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("asr model boom")

    monkeypatch.setattr(asr_mod, "ASRSource", _Boom)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("字幕")
        await pilot.pause()
        from tui.panels.subtitle import SubtitlePanel
        panel = app.query_one(SubtitlePanel)
        panel._run_subtitle(
            {"path": str(vid), "sources": ["asr"], "asr_model": "0.6b"}
        )
        # Let the thread worker run + drain the call_from_thread post.
        # Poll a bounded number of times to avoid flakiness.
        for _ in range(20):
            lines = app.query_one(LogPane).lines
            if any("字幕初始化失败" in str(l) for l in lines):
                break
            await pilot.pause(0.05)
        # Init failure must be contained AND logged; app NOT crashed.
        assert app.is_running
        lines = app.query_one(LogPane).lines
        assert any("字幕初始化失败" in str(l) for l in lines)
