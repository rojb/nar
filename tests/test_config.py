import subprocess
import sys
from pathlib import Path

import pytest

from nar.config import (
    DEFAULT_MODELS,
    ApprovalLevel,
    Language,
    NarConfig,
    ProviderName,
    load_config,
)
from nar.config.models import HookEvent
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
    assert cfg.language is Language.ES
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
    write(home / "config.toml", 'model = "a"\nprovider = "ollama"\n')
    write(project / ".nar" / "config.toml", 'model = "b"\n')
    loaded = load_config(project)
    assert loaded.config.model == "b"
    assert loaded.config.provider is ProviderName.OLLAMA
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
    jira = loaded.config.mcp_servers["jira"]
    assert (jira.command, jira.args) == ("a", ["y"])


def test_approval_default_and_valid_values(home, project):
    assert load_config(project).config.approval is ApprovalLevel.ASK
    write(project / ".nar" / "config.toml", 'approval = "auto-edit"\n')
    assert load_config(project).config.approval is ApprovalLevel.AUTO_EDIT


def test_language_defaults_to_spanish(home, project):
    loaded = load_config(project)
    assert loaded.config.language is Language.ES
    assert loaded.provenance["language"] == "default"


def test_language_labels_name_the_language_in_english():
    assert Language.ES.label == "Spanish"
    assert Language.EN.label == "English"


def test_language_from_the_user_file(home, project):
    path = write(home / "config.toml", 'language = "en"\n')
    loaded = load_config(project)
    assert loaded.config.language is Language.EN
    assert loaded.provenance["language"] == str(path)


def test_language_project_file_beats_user_file(home, project):
    write(home / "config.toml", 'language = "en"\n')
    path = write(project / ".nar" / "config.toml", 'language = "es"\n')
    loaded = load_config(project)
    assert loaded.config.language is Language.ES
    assert loaded.provenance["language"] == str(path)


def test_language_override_beats_both_files(home, project):
    write(home / "config.toml", 'language = "es"\n')
    write(project / ".nar" / "config.toml", 'language = "es"\n')
    loaded = load_config(project, overrides={"language": "en"})
    assert loaded.config.language is Language.EN
    assert loaded.provenance["language"] == "command line"


def test_unsupported_language_names_setting_and_allowed(home, project):
    path = write(project / ".nar" / "config.toml", 'language = "fr"\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    message = info.value.message
    assert "language" in message
    assert "es, en" in message
    assert str(path) in message
    assert "\n" not in message


def test_malformed_toml_names_file_and_line(home, project):
    path = write(project / ".nar" / "config.toml", 'model = "ok"\nbroken = \n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert str(path) in info.value.message
    assert "line 2" in info.value.message
    assert info.value.path == path
    assert "\n" not in info.value.message


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_project_file_with_utf8_bom_loads(home, project):
    # Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM.
    path = write_bytes(project / ".nar" / "config.toml", b'\xef\xbb\xbfapproval = "yolo"\n')
    loaded = load_config(project)
    assert loaded.config.approval is ApprovalLevel.YOLO
    assert loaded.provenance["approval"] == str(path)


def test_user_file_with_utf8_bom_merges_like_one_without(home, project):
    write_bytes(home / "config.toml", b'\xef\xbb\xbfmodel = "a"\nprovider = "ollama"\n')
    write(project / ".nar" / "config.toml", 'model = "b"\n')
    loaded = load_config(project)
    assert loaded.config.model == "b"
    assert loaded.config.provider is ProviderName.OLLAMA
    assert loaded.provenance["provider"] == str(home / "config.toml")


def test_file_with_bom_and_malformed_toml_still_names_the_line(home, project):
    path = write_bytes(project / ".nar" / "config.toml", b'\xef\xbb\xbfmodel = "ok"\nbroken = \n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert str(path) in info.value.message
    assert "line 2" in info.value.message


def test_file_that_is_not_utf8_names_the_file(home, project):
    path = write_bytes(project / ".nar" / "config.toml", b'approval = "\xff"\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert str(path) in info.value.message
    assert info.value.path == path


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
    assert "language = es  (default)" in lines
    assert "mcp_servers = {}  (default)" in lines


def test_model_follows_provider_when_unset(home, project):
    write(project / ".nar" / "config.toml", 'provider = "ollama"\n')
    loaded = load_config(project)
    assert loaded.config.model == DEFAULT_MODELS[ProviderName.OLLAMA] == "qwen2.5-coder"
    assert loaded.provenance.get("model", "default") == "default"
    assert "model = qwen2.5-coder  (default)" in loaded.describe(project)


def test_deepseek_provider_uses_its_default_model(home, project):
    write(project / ".nar" / "config.toml", 'provider = "deepseek"\n')
    loaded = load_config(project)
    assert loaded.config.provider is ProviderName.DEEPSEEK
    assert loaded.config.model == DEFAULT_MODELS[ProviderName.DEEPSEEK] == "deepseek-flash"
    lines = loaded.describe(project)
    assert "provider = deepseek  (.nar/config.toml)" in lines
    assert "model = deepseek-flash  (default)" in lines


def test_deepseek_command_line_override(home, project):
    loaded = load_config(project, {"provider": "deepseek", "model": None})
    assert loaded.config.model == "deepseek-flash"
    assert "provider = deepseek  (command line)" in loaded.describe(project)


def test_explicit_model_is_kept_for_any_provider(home, project):
    write(project / ".nar" / "config.toml", 'provider = "anthropic"\nmodel = "claude-x"\n')
    assert load_config(project).config.model == "claude-x"


def test_unsupported_provider_lists_allowed(home, project):
    path = write(project / ".nar" / "config.toml", 'provider = "openai"\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    message = info.value.message
    assert "provider" in message
    assert "anthropic, openrouter, ollama, deepseek" in message
    assert str(path) in message


def test_command_line_overrides_project_file(home, project):
    write(project / ".nar" / "config.toml", 'provider = "openrouter"\nmodel = "a"\n')
    loaded = load_config(project, {"provider": "ollama", "model": "b"})
    assert loaded.config.provider is ProviderName.OLLAMA
    assert loaded.config.model == "b"
    assert loaded.provenance["provider"] == "command line"
    assert "provider = ollama  (command line)" in loaded.describe(project)


def test_provider_flag_alone_uses_provider_default_model(home, project):
    loaded = load_config(project, {"provider": "anthropic", "model": None})
    assert loaded.config.model == "claude-sonnet-5-5"
    assert loaded.provenance["provider"] == "command line"
    assert "model" not in loaded.provenance


def test_max_turns_default_and_source(home, project):
    loaded = load_config(project)
    assert loaded.config.max_turns == 20
    assert "max_turns = 20  (default)" in loaded.describe(project)


def test_invalid_max_turns_names_setting_and_file(home, project):
    path = write(project / ".nar" / "config.toml", "max_turns = 0\n")
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert "max_turns" in info.value.message
    assert str(path) in info.value.message


# --- MCP server definitions (RRG-74) ---


def mcp_project(project: Path, body: str) -> Path:
    return write(project / ".nar" / "config.toml", body)


def mcp_error(project: Path) -> str:
    with pytest.raises(ConfigError) as info:
        load_config(project)
    return info.value.message


def test_stdio_and_http_servers_load_with_defaults(home, project):
    mcp_project(
        project,
        '[mcp_servers.files]\ncommand = "npx"\nargs = ["-y", "srv"]\n'
        '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n'
        'headers = { Authorization = "Bearer ${LINEAR_API_KEY}" }\n',
    )
    loaded = load_config(project)
    files, linear = loaded.config.mcp_servers["files"], loaded.config.mcp_servers["linear"]
    assert (files.command, files.args, files.transport) == ("npx", ["-y", "srv"], "stdio")
    assert (linear.url, linear.transport) == ("https://mcp.linear.app/mcp", "http")
    for server in (files, linear):
        assert server.enabled is True
        assert server.enabled_tools is None
        assert (server.startup_timeout, server.tool_timeout) == (30, 60)
    lines = loaded.describe(project)
    assert "mcp_servers.files.command = npx  (.nar/config.toml)" in lines
    assert "mcp_servers.files.startup_timeout = 30  (default)" in lines
    assert "mcp_servers.files.enabled = true  (default)" in lines


def test_command_and_url_together_fail_naming_server(home, project):
    path = mcp_project(project, '[mcp_servers.both]\ncommand = "a"\nurl = "http://x"\n')
    message = mcp_error(project)
    assert "mcp_servers.both" in message
    assert "exactly one of 'command' or 'url'" in message
    assert str(path) in message


def test_neither_command_nor_url_fails_naming_server(home, project):
    mcp_project(project, "[mcp_servers.none]\nenabled = true\n")
    message = mcp_error(project)
    assert "mcp_servers.none" in message
    assert "exactly one of 'command' or 'url'" in message


def test_url_with_args_fails_naming_args(home, project):
    mcp_project(project, '[mcp_servers.r]\nurl = "http://x"\nargs = ["a"]\n')
    message = mcp_error(project)
    assert "mcp_servers.r" in message
    assert "'args'" in message


def test_command_with_headers_fails_naming_headers(home, project):
    mcp_project(project, '[mcp_servers.r]\ncommand = "x"\nheaders = { A = "b" }\n')
    message = mcp_error(project)
    assert "mcp_servers.r" in message
    assert "'headers'" in message


def test_typo_in_server_key_names_setting_and_file(home, project):
    path = mcp_project(project, '[mcp_servers.s]\ncomand = "npx"\n')
    message = mcp_error(project)
    assert "unknown setting 'mcp_servers.s.comand'" in message
    assert str(path) in message


@pytest.mark.parametrize("line", ["tool_timeout = 0", "startup_timeout = -1"])
def test_non_positive_timeout_fails_naming_key(home, project, line):
    mcp_project(project, f'[mcp_servers.s]\ncommand = "x"\n{line}\n')
    message = mcp_error(project)
    assert line.split()[0] in message
    assert "mcp_servers.s" in message


def test_server_names_differing_only_by_dash_are_ambiguous(home, project):
    mcp_project(
        project, '[mcp_servers.my-srv]\ncommand = "a"\n[mcp_servers.my_srv]\ncommand = "b"\n'
    )
    message = mcp_error(project)
    assert "my-srv" in message and "my_srv" in message


@pytest.mark.parametrize("name", ["jira server", "a" * 33, "-lead", "dot.ted"])
def test_invalid_server_names_fail(home, project, name):
    mcp_project(project, f'[mcp_servers."{name}"]\ncommand = "a"\n')
    assert name in mcp_error(project)


def test_server_name_of_32_characters_is_valid(home, project):
    mcp_project(project, f'[mcp_servers.{"a" * 32}]\ncommand = "a"\n')
    assert "a" * 32 in load_config(project).config.mcp_servers


def test_servers_from_both_files_merge_and_project_can_disable(home, project):
    write(home / "config.toml", '[mcp_servers.jira]\ncommand = "jira"\n')
    mcp_project(
        project,
        '[mcp_servers.linear]\nurl = "http://x"\n[mcp_servers.jira]\nenabled = false\n',
    )
    servers = load_config(project).config.mcp_servers
    assert set(servers) == {"jira", "linear"}
    assert servers["jira"].enabled is False
    assert servers["jira"].command == "jira"


def test_configuration_does_not_import_the_mcp_sdk():
    code = "import nar.config, sys; assert 'mcp' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


# --- masking of MCP secrets in --show-config ---

SECRET_SERVER = (
    '[mcp_servers.s]\nurl = "https://example.com/mcp"\n'
    'headers = { Authorization = "Bearer ${LINEAR_API_KEY}", X-Team = "abc123" }\n'
)


def test_describe_shows_references_and_masks_literal_headers(home, project):
    mcp_project(project, SECRET_SERVER)
    lines = load_config(project).describe(project)
    text = "\n".join(lines)
    assert (
        "mcp_servers.s.headers.Authorization = Bearer ${LINEAR_API_KEY}  (.nar/config.toml)"
        in lines
    )
    assert "mcp_servers.s.headers.X-Team = ********  (.nar/config.toml)" in lines
    assert "abc123" not in text


def test_describe_masks_literal_env_values(home, project):
    mcp_project(
        project,
        '[mcp_servers.s]\ncommand = "x"\nenv = { TOKEN = "lit-secret", FWD = "${TOKEN}" }\n',
    )
    lines = load_config(project).describe(project)
    assert "mcp_servers.s.env.TOKEN = ********  (.nar/config.toml)" in lines
    assert "mcp_servers.s.env.FWD = ${TOKEN}  (.nar/config.toml)" in lines
    assert "lit-secret" not in "\n".join(lines)


def test_describe_does_not_mask_command_and_url(home, project):
    mcp_project(
        project, '[mcp_servers.a]\ncommand = "npx"\n[mcp_servers.b]\nurl = "http://h/mcp"\n'
    )
    lines = load_config(project).describe(project)
    assert "mcp_servers.a.command = npx  (.nar/config.toml)" in lines
    assert "mcp_servers.b.url = http://h/mcp  (.nar/config.toml)" in lines


@pytest.mark.parametrize(
    ("provider", "window"),
    [("anthropic", 200000), ("openrouter", 128000), ("deepseek", 128000), ("ollama", 4096)],
)
def test_context_window_default_depends_on_the_provider(home, project, provider, window):
    write(project / ".nar" / "config.toml", f'provider = "{provider}"\n')
    assert load_config(project).config.context_window == window


def test_context_limit_defaults_are_shown(home, project):
    loaded = load_config(project)
    assert loaded.config.context_threshold == 80
    described = loaded.describe(project)
    assert "context_window = 128000  (default)" in described
    assert "context_threshold = 80  (default)" in described


def test_context_window_set_tells_a_user_value_from_the_default(home, project):
    assert load_config(project).config.context_window_set is False
    assert load_config(project, overrides={"provider": "ollama"}).config.context_window_set is False
    write(project / ".nar" / "config.toml", "context_window = 32000\n")
    assert load_config(project).config.context_window_set is True
    # The same number as the default still counts as the user's choice.
    write(project / ".nar" / "config.toml", "context_window = 128000\n")
    assert load_config(project).config.context_window_set is True


def test_context_window_from_a_file_wins_with_any_provider(home, project):
    write(project / ".nar" / "config.toml", 'provider = "ollama"\ncontext_window = 32000\n')
    loaded = load_config(project)
    assert loaded.config.context_window == 32000
    assert any(
        line.startswith("context_window = 32000") and ".nar" in line
        for line in loaded.describe(project)
    )


@pytest.mark.parametrize(
    "line",
    ["context_threshold = 0", "context_threshold = 101", "context_window = 999"],
)
def test_invalid_context_limits_name_setting_and_file(home, project, line):
    path = write(project / ".nar" / "config.toml", line + "\n")
    with pytest.raises(ConfigError) as caught:
        load_config(project)
    assert line.split(" ")[0] in caught.value.message
    assert str(path) in caught.value.message


# --- hooks (RRG-72) --------------------------------------------------------------


def test_hooks_default_to_empty_and_timeout_to_ten(home, project):
    loaded = load_config(project)
    assert loaded.config.hooks == {}
    assert loaded.config.hook_timeout == 10
    assert "hook_timeout = 10.0  (default)" in loaded.describe(project)


def test_hooks_load_per_event_in_order(home, project):
    write(
        project / ".nar" / "config.toml",
        '[hooks]\nafter_tool = ["ruff check --fix .", "true"]\non_error = ["notify.sh"]\n',
    )
    loaded = load_config(project)
    assert loaded.config.hooks[HookEvent.AFTER_TOOL] == ["ruff check --fix .", "true"]
    assert loaded.config.hooks[HookEvent.ON_ERROR] == ["notify.sh"]


def test_hooks_merge_user_and_project_files(home, project):
    write(home / "config.toml", '[hooks]\nbefore_tool = ["a"]\n')
    write(project / ".nar" / "config.toml", '[hooks]\nafter_tool = ["b"]\n')
    loaded = load_config(project)
    assert loaded.config.hooks[HookEvent.BEFORE_TOOL] == ["a"]
    assert loaded.config.hooks[HookEvent.AFTER_TOOL] == ["b"]


def test_unknown_hook_event_names_the_setting_and_the_file(home, project):
    path = write(project / ".nar" / "config.toml", '[hooks]\nbefore_tolls = ["x"]\n')
    with pytest.raises(ConfigError) as info:
        load_config(project)
    assert "hooks.before_tolls" in info.value.message
    assert str(path) in info.value.message


def test_hook_timeout_bounds(home, project):
    for value in ("0", "601"):
        write(project / ".nar" / "config.toml", f"hook_timeout = {value}\n")
        with pytest.raises(ConfigError) as info:
            load_config(project)
        assert "hook_timeout" in info.value.message


def test_hook_timeout_from_the_project_file(home, project):
    write(project / ".nar" / "config.toml", "hook_timeout = 30\n")
    loaded = load_config(project)
    assert loaded.config.hook_timeout == 30
    assert "hook_timeout = 30.0  (.nar/config.toml)" in loaded.describe(project)


def test_configuration_does_not_import_the_hook_runner(home, project):
    code = "import sys, nar.config; assert 'nar.hooks' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_subagent_max_turns_default_and_source(home, project):
    loaded = load_config(project)
    assert loaded.config.subagent_max_turns == 15
    assert "subagent_max_turns = 15  (default)" in loaded.describe(project)


def test_invalid_subagent_max_turns_names_setting_and_file(home, project):
    path = write(project / ".nar" / "config.toml", "subagent_max_turns = 0\n")
    with pytest.raises(ConfigError) as caught:
        load_config(project)
    assert "subagent_max_turns" in caught.value.message
    assert str(path) in caught.value.message
