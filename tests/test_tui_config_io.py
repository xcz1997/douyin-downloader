import yaml

from tui.config_io import save_config_fields


def test_save_merges_and_preserves_other_keys(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("links: [a]\nsave_path: ./old\nthread: 5\n",
                 encoding="utf-8")

    save_config_fields(str(f), {"save_path": "./new", "thread": 8})

    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert data["save_path"] == "./new"
    assert data["thread"] == 8
    assert data["links"] == ["a"]  # untouched key preserved


def test_save_nested_key(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("links: []\nsubtitle:\n  enabled: false\n",
                 encoding="utf-8")

    save_config_fields(str(f), {"subtitle": {"enabled": True,
                                              "sources": ["ocr"]}})

    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert data["subtitle"]["enabled"] is True
    assert data["subtitle"]["sources"] == ["ocr"]


def test_save_creates_file_if_absent(tmp_path):
    f = tmp_path / "new.yml"
    save_config_fields(str(f), {"save_path": "./x"})
    assert yaml.safe_load(f.read_text(encoding="utf-8")) == {
        "save_path": "./x"
    }
