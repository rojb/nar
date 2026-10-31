import pytest

from nar.config import McpServerConfig
from nar.mcp.expand import MissingVariable, expand_env, expand_server, referenced_values


def test_reference_is_replaced():
    assert expand_env("Bearer ${KEY}", {"KEY": "abc"}) == "Bearer abc"


def test_two_references_in_one_string():
    assert expand_env("${A}-${B}", {"A": "1", "B": "2"}) == "1-2"


def test_text_without_reference_is_unchanged():
    assert expand_env("plain text", {"KEY": "abc"}) == "plain text"


@pytest.mark.parametrize("environ", [{}, {"KEY": ""}])
def test_unset_or_empty_variable_raises_naming_it_only(environ):
    with pytest.raises(MissingVariable) as info:
        expand_env("x ${KEY}", {**environ, "OTHER": "other-secret"})
    assert info.value.name == "KEY"
    assert "KEY" in info.value.message
    assert "other-secret" not in info.value.message


def test_dollar_without_braces_is_left_alone():
    assert expand_env("$KEY and $", {"KEY": "abc"}) == "$KEY and $"


def test_literal_secret_with_dollar_is_left_alone():
    assert expand_env("pa$$w0rd{x}", {}) == "pa$$w0rd{x}"


def make_server(**values):
    return McpServerConfig.model_validate(values)


def test_expand_server_resolves_every_field_of_a_stdio_server():
    config = make_server(
        command="${BIN}",
        args=["--token", "${KEY}"],
        env={"TOKEN": "${KEY}", "PLAIN": "x"},
    )
    resolved = expand_server(config, {"BIN": "npx", "KEY": "abc"})
    assert resolved.command == "npx"
    assert resolved.args == ["--token", "abc"]
    assert resolved.env == {"TOKEN": "abc", "PLAIN": "x"}


def test_expand_server_resolves_url_and_headers():
    config = make_server(
        url="https://${HOST}/mcp", headers={"Authorization": "Bearer ${KEY}", "X": "y"}
    )
    resolved = expand_server(config, {"HOST": "h.example", "KEY": "abc"})
    assert resolved.url == "https://h.example/mcp"
    assert resolved.headers == {"Authorization": "Bearer abc", "X": "y"}


def test_expand_server_leaves_the_input_unchanged():
    config = make_server(url="http://h", headers={"Authorization": "Bearer ${KEY}"})
    expand_server(config, {"KEY": "abc"})
    assert config.headers == {"Authorization": "Bearer ${KEY}"}


def test_expand_server_raises_for_a_missing_variable_in_any_field():
    config = make_server(command="x", args=["${NOPE}"])
    with pytest.raises(MissingVariable) as info:
        expand_server(config, {"KEY": "abc"})
    assert info.value.name == "NOPE"


def test_referenced_values_lists_only_variables_the_server_uses():
    config = make_server(url="http://h", headers={"A": "Bearer ${KEY}", "B": "lit"})
    values = referenced_values(config, {"KEY": "abc", "OTHER": "zzz"})
    assert values == ("abc",)
