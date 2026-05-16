from core.config import ConfigLoader


def test_subtitle_defaults_off(tmp_path):
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text("links: []\nsave_path: ./x\n", encoding="utf-8")
    cfg = ConfigLoader(str(cfg_file)).load()
    assert cfg.subtitle.enabled is False
    assert cfg.subtitle.sources == ["track", "ocr", "asr"]
    assert cfg.subtitle.asr_model == "0.6b"


def test_subtitle_parsed_from_yaml(tmp_path):
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\n"
        "subtitle:\n"
        "  enabled: true\n"
        "  sources: [ocr]\n"
        "  asr:\n    model: '1.7b'\n"
        "  ocr:\n    interval: 0.3\n    similarity: 0.8\n",
        encoding="utf-8",
    )
    cfg = ConfigLoader(str(cfg_file)).load()
    assert cfg.subtitle.enabled is True
    assert cfg.subtitle.sources == ["ocr"]
    assert cfg.subtitle.asr_model == "1.7b"
    assert cfg.subtitle.ocr_interval == 0.3
    assert cfg.subtitle.ocr_similarity == 0.8


def test_subtitle_partial_block_fills_defaults(tmp_path):
    cfg_file = tmp_path / "c.yml"
    cfg_file.write_text(
        "links: []\nsave_path: ./x\n"
        "subtitle:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    cfg = ConfigLoader(str(cfg_file)).load()
    assert cfg.subtitle.enabled is True
    assert cfg.subtitle.sources == ["track", "ocr", "asr"]
    assert cfg.subtitle.asr_model == "0.6b"
    assert cfg.subtitle.ocr_interval == 0.5
    assert cfg.subtitle.ocr_similarity == 0.7
