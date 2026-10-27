import pytest

from nar.config import NarConfig
from nar.errors import ProviderError
from nar.providers.factory import create_provider


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(env, raising=False)


@pytest.mark.parametrize(
    ("provider", "env", "class_name"),
    [
        ("anthropic", "ANTHROPIC_API_KEY", "AnthropicProvider"),
        ("openrouter", "OPENROUTER_API_KEY", "OpenRouterProvider"),
        ("deepseek", "DEEPSEEK_API_KEY", "DeepSeekProvider"),
    ],
)
def test_builds_keyed_providers(monkeypatch, provider, env, class_name):
    monkeypatch.setenv(env, "sk-test")
    built = create_provider(NarConfig(provider=provider, model="m"))
    assert type(built).__name__ == class_name
    assert built.name == provider
    assert built.model == "m"


@pytest.mark.parametrize(
    ("provider", "env"),
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
    ],
)
def test_missing_key_fails_before_any_request(monkeypatch, provider, env):
    monkeypatch.setenv(env, "  ")
    with pytest.raises(ProviderError) as info:
        create_provider(NarConfig(provider=provider))
    assert info.value.message == f"{env} is not set; export it to use the {provider} provider"
    assert info.value.provider == provider


def test_unset_deepseek_key_fails_too():
    with pytest.raises(ProviderError) as info:
        create_provider(NarConfig(provider="deepseek"))
    assert info.value.message == (
        "DEEPSEEK_API_KEY is not set; export it to use the deepseek provider"
    )


def test_ollama_needs_no_key_and_uses_default_model():
    built = create_provider(NarConfig(provider="ollama"))
    assert type(built).__name__ == "OllamaProvider"
    assert built.model == "qwen2.5-coder"
    assert built.host == "http://localhost:11434"


def test_on_retry_is_passed_through(monkeypatch):
    notices = []
    built = create_provider(NarConfig(provider="ollama"), on_retry=notices.append)
    assert built.on_retry == notices.append
