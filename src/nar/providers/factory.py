"""Build the provider selected by the configuration.

Adapters are imported here, on demand: the SDKs take about two seconds to
import, which commands like ``nar --version`` should not pay.
"""

from __future__ import annotations

import os

from nar.config.models import NarConfig, ProviderName
from nar.errors import ProviderError
from nar.providers.base import LLMProvider
from nar.providers.retry import OnRetry

API_KEY_ENVS = {
    ProviderName.ANTHROPIC: "ANTHROPIC_API_KEY",
    ProviderName.OPENROUTER: "OPENROUTER_API_KEY",
    ProviderName.DEEPSEEK: "DEEPSEEK_API_KEY",
}


def create_provider(config: NarConfig, on_retry: OnRetry | None = None) -> LLMProvider:
    """Return the configured provider; a missing API key fails before any request."""
    name = ProviderName(config.provider)
    model = config.model or ""
    if name is ProviderName.ANTHROPIC:
        from nar.providers.anthropic import AnthropicProvider

        return AnthropicProvider(model, _api_key(name), on_retry=on_retry)
    if name is ProviderName.OPENROUTER:
        from nar.providers.openai_compat import OpenRouterProvider

        return OpenRouterProvider(model, _api_key(name), on_retry=on_retry)
    if name is ProviderName.DEEPSEEK:
        from nar.providers.openai_compat import DeepSeekProvider

        return DeepSeekProvider(model, _api_key(name), on_retry=on_retry)
    if name is ProviderName.OLLAMA:
        from nar.providers.openai_compat import OllamaProvider

        return OllamaProvider(model, on_retry=on_retry)
    raise ProviderError(f"unsupported provider: {name}", str(name))


def _api_key(name: ProviderName) -> str:
    env = API_KEY_ENVS[name]
    key = os.environ.get(env, "").strip()
    if not key:
        raise ProviderError(f"{env} is not set; export it to use the {name} provider", str(name))
    return key
