"""Provider error wording: Spanish for the user, English for the model (RRG-98).

Only the words that ``nar`` writes are translated. The provider's own text inside them is not.
"""

import httpx2
import openai
import pytest

from nar.agent.events import ToolCallFinished
from nar.config.models import ApprovalLevel, ProviderName
from nar.errors import ProviderError
from nar.i18n import Message, set_language
from nar.providers.base import unknown_model_error
from nar.providers.factory import create_provider
from nar.providers.messages import LLMResponse
from nar.providers.openai_compat import DEEPSEEK_BALANCE_HINT
from nar.providers.retry import (
    ErrorContext,
    RetryPolicy,
    call_once,
    stream_with_retries,
    translate_error,
)
from nar.tools.base import ToolContext
from test_approval_gate import APPROVE, BrokenPreviewTool, make_gate
from test_provider_retry import context as deepseek_context
from test_provider_retry import status_error
from test_subagent import ScriptedProvider, call, calls, make_parent

REQUEST = httpx2.Request("POST", "https://api.example.test/chat/completions")


def _both(make):
    """The user message in Spanish and the model message, for the same failure."""
    set_language("es")
    error = make()
    return error.message, error.model_message


@pytest.mark.parametrize(
    ("status", "body", "spanish", "english"),
    [
        (
            401,
            None,
            "deepseek rechazó la clave de API; revisa DEEPSEEK_API_KEY",
            "deepseek rejected the API key; check DEEPSEEK_API_KEY",
        ),
        (
            404,
            None,
            "deepseek no encontró el modelo 'm'",
            "model 'm' was not found by deepseek",
        ),
        (
            400,
            "Bad thing happened",
            "deepseek rechazó la solicitud (400): Bad thing happened",
            "deepseek rejected the request (400): Bad thing happened",
        ),
    ],
)
def test_status_errors_wrap_the_provider_text_in_the_user_language(status, body, spanish, english):
    user, model = _both(lambda: translate_error(status_error(status, body), deepseek_context()))
    assert user == spanish
    assert model == english


def test_the_providers_own_text_is_left_as_it_is():
    set_language("es")
    error = translate_error(status_error(400, "Your credit is low"), deepseek_context())
    assert error.message.endswith("Your credit is low")
    assert error.model_message.endswith("Your credit is low")


def test_a_plain_error_and_a_connection_error_are_translated():
    set_language("es")
    plain = translate_error(ValueError("bad\nvalue"), deepseek_context())
    assert plain.message == "falló la solicitud a deepseek (ValueError): bad value"
    assert plain.model_message == "deepseek request failed (ValueError): bad value"
    down = translate_error(openai.APIConnectionError(request=REQUEST), deepseek_context())
    assert down.message.startswith("no se puede conectar con deepseek: ")
    assert down.model_message.startswith("cannot reach deepseek: ")
    assert down.unavailable


def test_a_hint_message_is_worded_in_both_languages():
    set_language("es")
    ctx = deepseek_context(status_hints={402: DEEPSEEK_BALANCE_HINT})
    error = translate_error(status_error(402, "Insufficient Balance"), ctx)
    assert error.message == (
        "la cuenta de deepseek no tiene saldo suficiente; recarga en platform.deepseek.com"
    )
    assert error.model_message == (
        "deepseek account has insufficient balance; top up at platform.deepseek.com"
    )
    assert error.unavailable


def test_a_plain_string_hint_is_the_same_for_both():
    ctx = deepseek_context(status_hints={402: "custom hint"})
    set_language("es")
    error = translate_error(status_error(402), ctx)
    assert error.message == error.model_message == "custom hint"


def test_ollama_hints_name_the_host_and_the_command():
    from nar.providers.openai_compat import OllamaProvider

    set_language("es")
    provider = OllamaProvider("m", host="localhost:1")
    hint = provider.context.unreachable_hint
    assert str(hint) == (
        "no se puede conectar con Ollama en http://localhost:1; ¿está en ejecución? "
        "(inícialo con 'ollama serve')"
    )
    assert hint.english() == (
        "cannot reach Ollama at http://localhost:1; is it running? (start it with 'ollama serve')"
    )
    missing = provider._missing_model(["other"])
    assert missing.message == ("el modelo 'm' no está instalado en Ollama; ejecuta 'ollama pull m'")
    assert missing.model_message == "model 'm' is not installed in Ollama; run 'ollama pull m'"


def test_retries_exhausted_wraps_the_translated_cause():
    policy = RetryPolicy(max_retries=1, base_delay=0, sleep=lambda _s: None)
    ctx = ErrorContext("deepseek", "m")

    def failing():
        raise status_error(503, "overloaded now")
        yield  # pragma: no cover

    set_language("es")
    with pytest.raises(ProviderError) as caught:
        list(stream_with_retries(failing, ctx, policy))
    assert caught.value.message == (
        "deepseek falló tras 2 intentos: deepseek rechazó la solicitud (503): overloaded now"
    )
    assert caught.value.model_message == (
        "deepseek failed after 2 attempts: deepseek rejected the request (503): overloaded now"
    )
    assert caught.value.unavailable


def test_call_once_timeout_is_translated():
    def slow():
        raise openai.APITimeoutError(request=REQUEST)

    set_language("es")
    with pytest.raises(ProviderError) as caught:
        call_once(slow, ErrorContext("deepseek", "m"), 10.0)
    assert caught.value.message == "deepseek no respondió en 10 segundos"
    assert caught.value.model_message == "deepseek did not answer within 10 seconds"


def test_unknown_model_error_suggests_names_in_both_languages():
    set_language("es")
    error = unknown_model_error("ollama", "qwen2.5-coder", ["unrelated"])
    assert error.message == "ollama no encontró el modelo 'qwen2.5-coder'"
    close = unknown_model_error("ollama", "qwen2.5-codr", ["qwen2.5-coder"])
    assert close.message == (
        "ollama no encontró el modelo 'qwen2.5-codr'; ¿quisiste decir qwen2.5-coder?"
    )
    assert close.model_message == (
        "model 'qwen2.5-codr' was not found by ollama; did you mean qwen2.5-coder?"
    )


def test_a_missing_api_key_is_translated(monkeypatch):
    from nar.config.models import NarConfig

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_language("es")
    with pytest.raises(ProviderError) as caught:
        create_provider(NarConfig(provider=ProviderName.ANTHROPIC))
    assert caught.value.message == (
        "ANTHROPIC_API_KEY no está definida; expórtala para usar el proveedor anthropic"
    )
    assert caught.value.model_message == (
        "ANTHROPIC_API_KEY is not set; export it to use the anthropic provider"
    )


def test_message_words_a_catalog_key_in_the_active_language():
    text = Message("provider.cannot_list", {"provider": "x"})
    set_language("es")
    assert str(text) == "x no puede listar sus modelos"
    assert text.english() == "x cannot list its models"


def test_a_subagent_failure_reaches_the_model_in_english_even_in_spanish(tmp_path):
    set_language("es")
    failure = ProviderError(
        "deepseek falló tras 2 intentos", "scripted", model_message="deepseek failed after 2"
    )
    provider = ScriptedProvider(
        calls(call("t", "task", subagent="explorer", prompt="look")), failure, LLMResponse("done")
    )
    parent, _, _ = make_parent(tmp_path, provider)
    events = list(parent.run("go"))
    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.result.is_error
    assert "explorer subagent failed: deepseek failed after 2" in finished.result.output
    assert "falló" not in finished.result.output


def test_the_unavailable_preview_note_is_spanish_for_the_user(tmp_path):
    set_language("es")
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, context=ToolContext(cwd=tmp_path))
    assert gate(BrokenPreviewTool(), {"text": "x"}) is None
    assert prompter.requests[0].preview.detail.startswith(
        "(vista previa no disponible; se muestran los argumentos sin procesar)"
    )
