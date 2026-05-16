import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "downloader.py"


def test_xhs_session_constructed_with_profile_dir():
    """downloader.py must pass profile_dir from config into the session
    so persistent mode is reachable end-to-end."""
    src = SRC.read_text(encoding="utf-8")
    assert "XHSBrowserSession(" in src
    assert "profile_dir=config.xhs.profile_dir" in src
    ast.parse(src)
