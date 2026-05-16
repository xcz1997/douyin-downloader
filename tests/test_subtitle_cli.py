import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_cli_help_lists_sources_flag():
    out = subprocess.run(
        [sys.executable, "extract_text.py", "--help"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "--sources" in out.stdout


def test_cli_errors_on_missing_path():
    out = subprocess.run(
        [sys.executable, "extract_text.py", "/no/such/path.mp4"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.returncode != 0
    assert "未找到视频" in (out.stdout + out.stderr)
