"""TDD tests for TUI-T13: settings panel subtitle.* / xhs.profile_dir fields.

Covers:
1. Nested round-trip + sibling-key preservation (deep-merge, the core requirement)
2. Read-out of existing values from config
3. Missing/bad config does not crash app
4. Invalid float shows inline error, does not crash
5. sources <-> comma-string round-trip
6. Existing 3-field regression (save_path / thread / retry_times)
"""
from __future__ import annotations

import yaml
import pytest

from tui.app import DownloaderApp


# ---------------------------------------------------------------------------
# 1. Nested round-trip + sibling-key preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_nested_roundtrip_and_sibling_preserved(tmp_path):
    """Core test: editing nested subtitle/xhs fields writes to correct yaml paths
    AND preserves unknown sibling keys not touched by the form (deep-merge proof)."""
    cfg = tmp_path / "c.yml"
    # Write a config with extra sibling keys that the form does NOT edit.
    cfg.write_text(
        "links: []\n"
        "save_path: ./dl\n"
        "subtitle:\n"
        "  enabled: false\n"
        "  sources: [track, ocr, asr]\n"
        "  asr:\n"
        "    model: '0.6b'\n"
        "    extra: keep\n"  # asr 2nd-level sibling NOT in form — must survive
        "  ocr:\n"
        "    interval: 0.5\n"
        "    similarity: 0.7\n"
        "    foo: 99\n"   # sibling key NOT in form — must survive
        "  bar: extra\n"  # sibling key NOT in form — must survive
        "xhs:\n"
        "  profile_dir: ''\n"
        "  extra_key: keep_me\n",  # sibling key NOT in form — must survive
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        # Change subtitle.enabled to True via checkbox
        cb = app.query_one("#set-subtitle-enabled")
        cb.value = True

        # Change asr model
        inp_asr = app.query_one("#set-subtitle-asr-model")
        inp_asr.value = "1.7b"

        # Change ocr interval
        inp_interval = app.query_one("#set-subtitle-ocr-interval")
        inp_interval.value = "0.3"

        # Change xhs profile_dir
        inp_prof = app.query_one("#set-xhs-profile-dir")
        inp_prof.value = "/home/me/.xhsprof"

        # Press save button (avoids OutOfBounds when button is off-screen)
        from textual.widgets import Button
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

    # Re-read raw yaml
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))

    # (a) Written values land in correct nested paths
    assert data["subtitle"]["enabled"] is True
    assert data["subtitle"]["asr"]["model"] == "1.7b"
    assert data["subtitle"]["ocr"]["interval"] == pytest.approx(0.3)
    assert data["xhs"]["profile_dir"] == "/home/me/.xhsprof"

    # (b) Sibling keys NOT in the form are preserved (deep-merge proof)
    assert data["subtitle"]["ocr"]["foo"] == 99, "ocr sibling 'foo' was lost!"
    assert data["subtitle"]["asr"]["extra"] == "keep", "asr sibling 'extra' was lost!"
    assert data["subtitle"]["bar"] == "extra", "subtitle sibling 'bar' was lost!"
    assert data["xhs"]["extra_key"] == "keep_me", "xhs sibling 'extra_key' was lost!"


# ---------------------------------------------------------------------------
# 2. Read-out of existing values
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_reads_existing_subtitle_xhs_values(tmp_path):
    """App startup should read existing subtitle/xhs values into the widgets."""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\n"
        "save_path: ./dl\n"
        "subtitle:\n"
        "  enabled: true\n"
        "  sources: [ocr, asr]\n"
        "  asr:\n"
        "    model: '1.7b'\n"
        "  ocr:\n"
        "    interval: 0.25\n"
        "    similarity: 0.85\n"
        "xhs:\n"
        "  profile_dir: /tmp/prof\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        from textual.widgets import Checkbox, Input

        assert app.query_one("#set-subtitle-enabled", Checkbox).value is True
        assert app.query_one("#set-subtitle-sources", Input).value == "ocr,asr"
        assert app.query_one("#set-subtitle-asr-model", Input).value == "1.7b"
        assert app.query_one("#set-subtitle-ocr-interval", Input).value == "0.25"
        assert app.query_one("#set-subtitle-ocr-similarity", Input).value == "0.85"
        assert app.query_one("#set-xhs-profile-dir", Input).value == "/tmp/prof"


# ---------------------------------------------------------------------------
# 3. Missing/bad config does not crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_missing_config_shows_defaults(tmp_path):
    """Missing config must not crash; new fields must show hardcoded defaults."""
    missing = tmp_path / "nope.yml"
    app = DownloaderApp(config_path=str(missing))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        from textual.widgets import Checkbox, Input

        # App must have started (nav labels present)
        assert app.nav_labels() == ["下载", "字幕", "转录", "登录", "设置"]

        # New fields show defaults (enabled=False, sources=default, etc.)
        assert app.query_one("#set-subtitle-enabled", Checkbox).value is False
        assert app.query_one("#set-subtitle-sources", Input).value == "track,ocr,asr"
        assert app.query_one("#set-subtitle-asr-model", Input).value == "0.6b"
        assert app.query_one("#set-subtitle-ocr-interval", Input).value == "0.5"
        assert app.query_one("#set-subtitle-ocr-similarity", Input).value == "0.7"
        assert app.query_one("#set-xhs-profile-dir", Input).value == ""


# ---------------------------------------------------------------------------
# 4. Invalid float shows inline error, does not crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_invalid_float_shows_inline_error(tmp_path):
    """Filling a non-numeric value in ocr.interval shows error msg, no crash."""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\nsave_path: ./dl\n"
        "subtitle:\n  ocr:\n    interval: 0.5\n    similarity: 0.7\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        inp = app.query_one("#set-subtitle-ocr-interval")
        inp.value = "abc"  # invalid float

        from textual.widgets import Button
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

        from textual.widgets import Label
        msg_text = str(app.query_one("#settings-msg", Label).render())
        assert "保存失败" in msg_text, f"Expected '保存失败' in msg, got: {msg_text!r}"


# ---------------------------------------------------------------------------
# 5. sources round-trip (comma-string <-> list)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_sources_roundtrip(tmp_path):
    """sources field: comma string round-trips to yaml list."""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\nsave_path: ./dl\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        inp = app.query_one("#set-subtitle-sources")
        inp.value = "ocr,asr"

        from textual.widgets import Button
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["subtitle"]["sources"] == ["ocr", "asr"]


@pytest.mark.asyncio
async def test_settings_sources_empty_falls_back_to_default(tmp_path):
    """Empty sources input falls back to default ['track', 'ocr', 'asr']."""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\nsave_path: ./dl\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        inp = app.query_one("#set-subtitle-sources")
        inp.value = ""  # empty → fallback to default

        from textual.widgets import Button
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["subtitle"]["sources"] == ["track", "ocr", "asr"]


# ---------------------------------------------------------------------------
# 6. Existing 3-field regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_existing_fields_still_work(tmp_path):
    """save_path / thread / retry_times still readable and saveable."""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\nsave_path: ./old\nthread: 3\nretry_times: 2\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()

        from textual.widgets import Input

        assert app.query_one("#set-save_path", Input).value == "./old"
        assert app.query_one("#set-thread", Input).value == "3"
        assert app.query_one("#set-retry_times", Input).value == "2"

        # Update save_path
        app.query_one("#set-save_path", Input).value = "./new"
        from textual.widgets import Button
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["save_path"] == "./new"


# ---------------------------------------------------------------------------
# 7. Transcribe nested fields round-trip + read-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_transcribe_roundtrip(tmp_path):
    """编辑图片转录字段写入正确 yaml 路径，且保留未触碰的 sibling。"""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\n"
        "transcribe:\n"
        "  enabled: false\n"
        "  base_url: old-url\n"
        "  model: old-model\n"
        "  api_key: ''\n"
        "  max_images: 0\n"
        "  sibling_keep: 1\n",  # 不在表单里的 sibling，必须保留
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()
        from textual.widgets import Input, Checkbox, Button
        app.query_one("#set-transcribe-enabled", Checkbox).value = True
        app.query_one("#set-transcribe-auto", Checkbox).value = True
        app.query_one("#set-transcribe-base-url", Input).value = "http://new/v1"
        app.query_one("#set-transcribe-model", Input).value = "qwen-vl-max"
        app.query_one("#set-transcribe-api-key", Input).value = "sk-secret"
        app.query_one("#set-transcribe-max-images", Input).value = "5"
        app.query_one("#settings-save", Button).press()
        await pilot.pause()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["transcribe"]["enabled"] is True
    assert data["transcribe"]["auto_after_download"] is True
    assert data["transcribe"]["base_url"] == "http://new/v1"
    assert data["transcribe"]["model"] == "qwen-vl-max"
    assert data["transcribe"]["api_key"] == "sk-secret"
    assert data["transcribe"]["max_images"] == 5
    # sibling 保留（deep-merge proof）
    assert data["transcribe"]["sibling_keep"] == 1


@pytest.mark.asyncio
async def test_settings_reads_existing_transcribe_values(tmp_path):
    """启动时把已有 transcribe 值读进 widget。"""
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "links: []\n"
        "transcribe:\n"
        "  enabled: true\n"
        "  base_url: http://x/v1\n"
        "  model: my-model\n"
        "  api_key: sk-abc\n"
        "  max_images: 3\n",
        encoding="utf-8",
    )
    app = DownloaderApp(config_path=str(cfg))
    async with app.run_test() as pilot:
        app.show_section("设置")
        await pilot.pause()
        from textual.widgets import Input, Checkbox
        assert app.query_one("#set-transcribe-enabled", Checkbox).value is True
        assert app.query_one(
            "#set-transcribe-base-url", Input).value == "http://x/v1"
        assert app.query_one(
            "#set-transcribe-model", Input).value == "my-model"
        assert app.query_one(
            "#set-transcribe-api-key", Input).value == "sk-abc"
        assert app.query_one(
            "#set-transcribe-max-images", Input).value == "3"
