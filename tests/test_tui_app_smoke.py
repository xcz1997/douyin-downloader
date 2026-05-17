import pytest

from tui.app import DownloaderApp


@pytest.mark.asyncio
async def test_app_boots_and_has_four_nav_sections(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        labels = app.nav_labels()
        assert labels == ["下载", "字幕", "登录", "设置"]
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_boots_with_missing_config(tmp_path):
    # Missing/bad config must NOT crash app startup (spec: core errors
    # contained). Without FIX 1 this raises FileNotFoundError in
    # SettingsPanel._load during compose and the app never launches.
    missing = tmp_path / "nope.yml"
    app = DownloaderApp(config_path=str(missing))
    async with app.run_test() as pilot:
        assert app.nav_labels() == ["下载", "字幕", "登录", "设置"]
        await pilot.pause()


@pytest.mark.asyncio
async def test_switch_to_settings_shows_config_fields(tmp_path):
    cfg = tmp_path / "c.yml"
    cfg.write_text("links: []\nsave_path: ./JIN/\nthread: 5\n",
                   encoding="utf-8")
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()
        assert app.current_section == "设置"
        assert app.settings_value("save_path") == "./JIN/"
