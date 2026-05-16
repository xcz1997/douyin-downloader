from core.config import ConfigLoader


def _load(tmp_path, body: str):
    f = tmp_path / "c.yml"
    f.write_text("links: []\nsave_path: ./x\n" + body, encoding="utf-8")
    return ConfigLoader(str(f)).load()


def test_xhs_profile_dir_defaults_empty(tmp_path):
    cfg = _load(tmp_path, "")
    assert cfg.xhs.profile_dir == ""


def test_xhs_profile_dir_parsed(tmp_path):
    cfg = _load(tmp_path, "xhs:\n  profile_dir: /home/me/.xhsprof\n")
    assert cfg.xhs.profile_dir == "/home/me/.xhsprof"


def test_xhs_partial_block_ok(tmp_path):
    cfg = _load(tmp_path, "xhs: {}\n")
    assert cfg.xhs.profile_dir == ""


def test_xhs_null_block_ok(tmp_path):
    cfg = _load(tmp_path, "xhs:\n")
    assert cfg.xhs.profile_dir == ""


def test_xhs_null_profile_dir_ok(tmp_path):
    cfg = _load(tmp_path, "xhs:\n  profile_dir:\n")
    assert cfg.xhs.profile_dir == ""
