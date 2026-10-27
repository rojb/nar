from pathlib import Path

from nar.tools.files import is_binary, os_error_message, resolve_path


def test_relative_path_resolves_under_cwd(tmp_path):
    assert resolve_path("src/app.py", tmp_path) == (tmp_path / "src" / "app.py").resolve()


def test_absolute_path_ignores_cwd(tmp_path):
    target = (tmp_path / "a.txt").resolve()
    assert resolve_path(str(target), tmp_path / "other") == target


def test_tilde_is_not_expanded(tmp_path):
    resolved = resolve_path("~/x.txt", tmp_path)
    assert resolved == (tmp_path / "~" / "x.txt").resolve()
    assert resolved != Path.home() / "x.txt"


def test_is_binary_detects_nul_in_the_head():
    assert is_binary(b"\x89PNG\r\n\x1a\n\x00")
    assert not is_binary(b"plain text")


def test_nul_after_the_sniff_window_is_not_detected():
    assert not is_binary(b"a" * 8192 + b"\x00")


def test_os_error_message_names_action_path_and_reason(tmp_path):
    path = tmp_path / "f.txt"
    message = os_error_message("write", path, PermissionError(13, "Permission denied"))
    assert "cannot write" in message
    assert str(path) in message
    assert "Permission denied" in message
