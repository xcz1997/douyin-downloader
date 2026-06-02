from tui.panels.transcribe import build_transcribe_spec


def test_spec_rejects_empty():
    assert "error" in build_transcribe_spec("")


def test_spec_passes_path():
    assert build_transcribe_spec(" ./d ") == {"path": "./d"}
