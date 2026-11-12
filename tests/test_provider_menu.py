import io

import pytest
from rich.console import Console

from nar.cli.fallback import provider_options
from nar.ui.provider_menu import ProviderMenu, ProviderOption

OPTIONS = [
    ProviderOption("anthropic", "claude-sonnet-5-5", "key set"),
    ProviderOption("openrouter", "openrouter/auto", "no key: it will ask for one"),
    ProviderOption("ollama", "qwen2.5-coder", "local, no key"),
]


class TTY(io.StringIO):
    def isatty(self):
        return True


def menu(*answers):
    out = TTY()
    queue = iter(answers)

    def read_line(prompt):
        value = next(queue)
        if isinstance(value, BaseException):
            raise value
        return value

    console = Console(file=out, force_terminal=False, width=200)
    return ProviderMenu(console, stdin=TTY(), read_line=read_line), out


def test_lists_each_provider_with_its_model_and_status():
    chooser, out = menu("1")
    assert chooser.choose("deepseek", OPTIONS) == "anthropic"
    text = out.getvalue()
    assert "deepseek can't answer. Continue with another provider?" in text
    assert "1. anthropic   · claude-sonnet-5-5  (key set)" in text
    assert "2. openrouter  · openrouter/auto  (no key: it will ask for one)" in text
    assert "3. ollama      · qwen2.5-coder  (local, no key)" in text
    assert "Enter: stay on deepseek" in text
    assert "Choose 1-3: " in text


@pytest.mark.parametrize("answer", ["", "   ", EOFError(), KeyboardInterrupt()])
def test_enter_end_of_input_or_ctrl_c_stays(answer):
    chooser, _ = menu(answer)
    assert chooser.choose("deepseek", OPTIONS) is None


def test_an_unknown_answer_asks_again():
    chooser, out = menu("9", "abc", "0", "3")
    assert chooser.choose("deepseek", OPTIONS) == "ollama"
    assert out.getvalue().count("Choose 1-3: ") == 4


def test_not_available_without_a_terminal():
    assert not ProviderMenu(Console(file=TTY()), stdin=io.StringIO()).available()
    assert not ProviderMenu(Console(file=io.StringIO()), stdin=TTY()).available()


def test_options_show_whether_each_key_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    options = {option.name: option for option in provider_options({"deepseek"})}
    assert set(options) == {"anthropic", "openrouter", "ollama"}
    assert options["anthropic"].status == "key set"
    assert options["anthropic"].model == "claude-sonnet-5-5"
    assert options["openrouter"].status == "no key: it will ask for one"
    assert options["ollama"].status == "local, no key"


# --- Spanish -------------------------------------------------------------------------------


def test_the_menu_text_is_spanish_and_names_and_models_are_unchanged(monkeypatch):
    from nar.i18n import set_language

    set_language("es")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    options = provider_options({"deepseek"})
    chooser, out = menu("", "9", "2")

    assert chooser.choose("deepseek", options) is None
    assert chooser.choose("deepseek", options) == "openrouter"
    text = out.getvalue()
    assert "deepseek no puede responder. ¿Continuar con otro proveedor?" in text
    assert "1. anthropic   · claude-sonnet-5-5  (clave definida)" in text
    assert "2. openrouter  · openrouter/auto  (sin clave: se pedirá una)" in text
    assert "3. ollama      · qwen2.5-coder  (local, sin clave)" in text
    assert "Enter: quedarse en deepseek" in text
    assert "Elige 1-3: " in text
