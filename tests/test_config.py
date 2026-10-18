from pathlib import Path

import pytest

from nar.config import ApprovalLevel, NarConfig, load_config
from nar.config.paths import project_config, user_config
from nar.errors import ConfigError


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("NAR_HOME", str(path))
    return path


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_model():
    cfg = NarConfig()
    assert cfg.provider == "openrouter"
    assert cfg.model == "openrouter/auto"
    assert cfg.approval is ApprovalLevel.ASK
    assert cfg.mcp_servers == {}


def test_paths_honor_nar_home(home, project):
    assert user_config() == home / "config.toml"
    assert project_config(project) == project / ".nar" / "config.toml"


def test_no_files_uses_defaults_and_creates_nothing(home, project):
    loaded = load_config(project)
    assert loaded.config == NarConfig()
    assert list(home.iterdir()) == []
    assert not (project / ".nar").exists()
    assert set(loaded.provenance.values()) == {"default"}


def test_only_user_file(home, project):
    write(home / "config.toml", 'model = "a"\n')
    loaded = load_config(project)
    assert loaded.config.model == "a"
    assert loaded.config.provider == "openrouter"
    assert loaded.provenance["model"] == str(home / "config.toml")
    assert loaded.provenance["provider"] == "default"


def test_project_overrides_user(home, project):
    write(home / "config.toml", 'model = "a"\nprovider = "p"\n')
    write(project / ".nar" / "config.toml", 'model = "b"\n')
    loaded = load_config(project)
    assert loaded.config.model == "b"
    assert loaded.config.provider == "p"
    assert loaded.provenance["model"] == str(project / ".nar" / "config.toml")
    assert loaded.provenance["provider"] == str(home / "config.toml")


def test_nested_tables_merge(home, project):
    write(home / "config.toml", '[mcp_servers.jira]\ncommand = "jira"\n')
    write(project / ".nar" / "config.toml", '[mcp_servers.linear]\ncommand = "lin"\n')
    loaded = load_config(project)
    assert set(loaded.config.mcp_servers) == {"jira", "linear"}
    assert loaded.provenance["mcp_servers.jira.command"] == str(home / "config.toml")
    assert "mcp_servers" not in loaded.provenance


def test_nested_key_override(home, project):
    write(home / "config.toml", '[mcp_servers.jira]\ncommand = "a"\nargs = ["x"]\n')
    write(project / ".nar" / "config.toml", '[mcp_servers.jira]\nargs = ["y"]\n')
    loaded = load_config(project)
    assert loaded.config.mcp_servers["jira"] == {"command": "a", "args": ["y"]}


def test_approval_default_and_valid_values(home, project):
    assert load_config(project).config.approval is ApprovalLevel.ASK
    write(project / ".nar" / "config.toml", 'approval = "auto-edit"\n')
    assert load_config(project).config.approval is ApprovalLevel.AUTO_EDIT


def test_malformed_toml_names_file_and_line(home, project):
    path = write(project / ".nar" / "config.toml", 'model = "ok"\nbroken = \n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert str(path) in info.value.message
    assert "line 2" in info.value.message
    assert info.value.path == path
    assert "\n" not in info.value.message


def test_invalid_value_names_setting_and_allowed(home, project):
    path = write(project / ".nar" / "config.toml", 'approval = "always"\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    message = info.value.message
    assert "approval" in message
    assert "ask, auto-edit, yolo" in message
    assert str(path) in message
    assert "\n" not in message


def test_unknown_setting_names_key_and_file(home, project):
    path = write(home / "config.toml", 'modle = "x"\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert "modle" in info.value.message
    assert str(path) in info.value.message


def test_wrong_type_is_reported(home, project):
    write(project / ".nar" / "config.toml", "provider = 5\n")
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert "provider" in info.value.message


def test_describe_shows_sources(home, project):
    write(project / ".nar" / "config.toml", 'model = "b"\n')
    lines = load_config(project).describe(project)
    assert "model = b  (.nar/config.toml)" in lines
    assert "provider = openrouter  (default)" in lines
    assert "approval = ask  (default)" in lines
    assert "mcp_servers = {}  (default)" in lines
