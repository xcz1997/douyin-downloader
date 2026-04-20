import yaml
from pathlib import Path
from core.config import ConfigLoader
from core.models import AppConfig


def _write_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def test_load_new_format(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "links": ["https://example.com/video/123"],
        "save_path": str(tmp_path / "out"),
        "cookie": "abc=123",
        "mode": ["post"],
        "limit": {"post": 10},
        "time_range": {"start": "", "end": ""},
        "download": {"music": True, "cover": False, "metadata": True},
        "incremental": {"post": True},
        "concurrency": 3,
        "retry": 5,
        "database": True,
        "log_level": "DEBUG",
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert isinstance(config, AppConfig)
    assert config.links == ["https://example.com/video/123"]
    assert config.thread == 3
    assert config.retry_times == 5
    assert config.download.cover is False
    assert config.download.json is True
    assert config.log_level == "DEBUG"


def test_migrate_old_format(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "link": ["https://example.com/video/123"],
        "path": str(tmp_path / "out"),
        "cookies": "abc=123",
        "mode": ["post"],
        "number": {"post": 10},
        "start_time": "2026-01-01",
        "end_time": "2026-12-31",
        "json": True,
        "music": True,
        "cover": True,
        "thread": 5,
        "retry_times": 3,
        "database": True,
        "increase": {"post": True},
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://example.com/video/123"]
    assert config.save_path == Path(tmp_path / "out")
    assert config.thread == 5
    assert config.number == {"post": 10}
    assert config.start_time == "2026-01-01"


def test_single_link_becomes_list(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {
        "links": "https://example.com/video/123",
        "save_path": str(tmp_path / "out"),
    })
    loader = ConfigLoader(str(cfg_path))
    config = loader.load()
    assert config.links == ["https://example.com/video/123"]


def test_validate_missing_links(tmp_path):
    cfg_path = tmp_path / "config.yml"
    _write_yaml(cfg_path, {"save_path": str(tmp_path / "out")})
    loader = ConfigLoader(str(cfg_path))
    errors = loader.validate()
    assert any("links" in e.lower() for e in errors)


def test_generate_default(tmp_path):
    cfg_path = tmp_path / "config.yml"
    ConfigLoader.generate_default(str(cfg_path))
    assert cfg_path.exists()
    with open(cfg_path) as f:
        data = yaml.safe_load(f)
    assert "links" in data
    assert "save_path" in data
