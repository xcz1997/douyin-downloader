import yaml
from core.config import ConfigLoader


def _write(tmp_path, data):
    p = tmp_path / "config.yml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_transcribe_defaults_when_absent(tmp_path):
    cfg = ConfigLoader(_write(tmp_path, {"links": ["x"]})).load()
    assert cfg.transcribe.enabled is False
    assert cfg.transcribe.auto_after_download is False
    assert cfg.transcribe.model  # 非空默认
    assert cfg.transcribe.api_key_env  # 非空默认
    assert cfg.transcribe.overwrite is False


def test_transcribe_parsed_from_yaml(tmp_path):
    data = {
        "links": ["x"],
        "transcribe": {
            "enabled": True,
            "auto_after_download": True,
            "base_url": "http://local/v1",
            "model": "my-vl",
            "api_key_env": "MY_KEY",
            "max_images": 5,
            "overwrite": True,
            "timeout": 30,
            "retry": 1,
        },
    }
    cfg = ConfigLoader(_write(tmp_path, data)).load()
    t = cfg.transcribe
    assert t.enabled and t.auto_after_download
    assert t.base_url == "http://local/v1"
    assert t.model == "my-vl"
    assert t.api_key_env == "MY_KEY"
    assert t.max_images == 5
    assert t.overwrite is True
    assert t.timeout == 30
    assert t.retry == 1
