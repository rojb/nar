from pathlib import Path

from nar.errors import ConfigError, NarError, ProviderError


def test_nar_error_defaults():
    err = NarError("boom")
    assert str(err) == "boom"
    assert err.message == "boom"
    assert err.exit_code == 1


def test_nar_error_custom_exit_code():
    assert NarError("boom", exit_code=3).exit_code == 3


def test_config_error_is_nar_error_and_carries_path():
    path = Path("some/config.toml")
    err = ConfigError("bad", path=path)
    assert isinstance(err, NarError)
    assert err.path == path
    assert err.exit_code == 1


def test_config_error_path_optional():
    assert ConfigError("bad").path is None


def test_provider_error_is_nar_error_and_carries_provider():
    err = ProviderError("no key", provider="anthropic")
    assert isinstance(err, NarError)
    assert err.provider == "anthropic"
    assert err.exit_code == 1
