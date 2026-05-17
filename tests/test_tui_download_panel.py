import pytest

from tui.panels.download import DownloadPanel, build_pipeline_args


def test_build_pipeline_args_manual_url():
    args = build_pipeline_args(
        source="manual", manual_url="https://v.douyin.com/X",
        config_path="config.yml",
    )
    assert args["links"] == ["https://v.douyin.com/X"]
    assert args["interactive"] is False  # TUI never blocks on input()


def test_build_pipeline_args_config_source(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("links:\n  - https://v.douyin.com/A\nsave_path: ./x\n",
                   encoding="utf-8")
    args = build_pipeline_args(
        source="config", manual_url="", config_path=str(cfg),
    )
    assert args["links"] == ["https://v.douyin.com/A"]
    assert args["interactive"] is False


@pytest.mark.asyncio
async def test_panel_start_invokes_runner(monkeypatch):
    calls = {}

    async def fake_run(self, links, sink, interactive):
        calls["links"] = links
        calls["interactive"] = interactive
        sink.set_status("done")

    monkeypatch.setattr(
        "tui.panels.download.DownloadPanel._run_download", fake_run
    )
    panel = DownloadPanel(config_path="config.yml")
    await panel.start_download(source="manual",
                               manual_url="https://v.douyin.com/Z")
    assert calls["links"] == ["https://v.douyin.com/Z"]
    assert calls["interactive"] is False


@pytest.mark.asyncio
async def test_sink_emit_routes_to_logpane_on_event_loop(tmp_path):
    # Regression guard for the call_from_thread-on-async-worker bug:
    # the sink built by _make_sink must deliver events to the LogPane
    # when called from the event-loop thread (no RuntimeError).
    from tui.app import DownloaderApp
    from tui.widgets import LogPane

    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("下载")
        await pilot.pause()
        from tui.panels.download import DownloadPanel
        panel = app.query_one(DownloadPanel)
        sink = panel._make_sink()
        sink.set_status("烟雾测试")  # would RuntimeError with call_from_thread
        await pilot.pause()
        log = app.query_one(LogPane)
        text = "".join(line.text for line in log.lines)
        assert "烟雾测试" in text
