"""The approval, trust and API-key prompts in both languages (RRG-98)."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from nar.agent.approval import (
    ApprovalAnswer,
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)
from nar.config.models import ApprovalLevel
from nar.i18n import set_language
from nar.tools.base import Tool, ToolPreview, ToolResult
from nar.ui.approval_prompt import TerminalPrompter
from nar.ui.key_prompt import KeyPrompter, clean_key
from nar.ui.tool_view import ToolRenderer
from nar.ui.trust_prompt import TrustPrompter

APPROVE = ApprovalAnswer(ApprovalDecision.APPROVE)
APPROVE_SESSION = ApprovalAnswer(ApprovalDecision.APPROVE_SESSION)
REJECT = ApprovalAnswer(ApprovalDecision.REJECT)
PREVIEW = ToolPreview(title="Create a.txt (1 lines)", detail="hello")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def reader(*lines):
    queue = iter(lines)

    def read(_prompt=""):
        line = next(queue)
        if isinstance(line, BaseException):
            raise line
        return line

    return read


def approval(*lines):
    file = Terminal()
    console = Console(file=file, width=200, force_terminal=False)
    prompter = TerminalPrompter(ToolRenderer(console), stdin=Terminal(), read_line=reader(*lines))
    return prompter, file


def trust(*lines):
    file = Terminal()
    console = Console(file=file, width=200, force_terminal=False)
    return TrustPrompter(console, stdin=Terminal(), read_line=reader(*lines)), file


def keys(*lines):
    file = Terminal()
    console = Console(file=file, width=200, force_terminal=False)
    return KeyPrompter(console, stdin=Terminal(), read_line=reader(*lines)), file


def request(tool="write_file"):
    return ApprovalRequest(tool, PREVIEW)


# --- approval -----------------------------------------------------------------------------


def test_the_approval_question_reads_in_spanish():
    set_language("es")
    prompter, file = approval("s")

    assert prompter.ask(request()) == APPROVE
    out = file.getvalue()
    assert "Se necesita aprobación: Create a.txt (1 lines)" in out
    assert "¿Permitir write_file? [s]í / [n]o / [a] siempre write_file en esta sesión: " in out


def test_the_approval_question_reads_in_english():
    prompter, file = approval("y")

    prompter.ask(request())

    assert "Allow write_file? [y]es / [n]o / [a]lways allow write_file this session: " in (
        file.getvalue()
    )


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("line", ["y", "YES", "s", "sí", "SI", " Sí "])
def test_yes_is_accepted_in_both_languages(language, line):
    set_language(language)
    prompter, _ = approval(line)

    assert prompter.ask(request()) == APPROVE


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("line", ["a", "always", "Siempre", " SIEMPRE "])
def test_always_is_accepted_in_both_languages(language, line):
    set_language(language)
    prompter, file = approval(line)

    assert prompter.ask(request("edit_file")) == APPROVE_SESSION
    expected = {
        "es": "edit_file no volverá a preguntar en esta sesión",
        "en": "edit_file will not ask again in this session",
    }[language]
    assert expected in file.getvalue()


@pytest.mark.parametrize("language", ["es", "en"])
def test_an_unknown_answer_asks_again(language):
    set_language(language)
    prompter, file = approval("quizás", "y")

    assert prompter.ask(request()) == APPROVE
    hint = {"es": "Responde s, n o a.", "en": "Please answer y, n or a."}[language]
    assert file.getvalue().count(hint) == 1


def test_no_asks_for_a_reason_in_spanish():
    set_language("es")
    prompter, file = approval("no", "usa la función auxiliar")

    assert prompter.ask(request()) == ApprovalAnswer(
        ApprovalDecision.REJECT, "usa la función auxiliar"
    )
    assert "Motivo (opcional, Enter para omitir): " in file.getvalue()


def test_long_previews_say_how_much_was_left_out():
    set_language("es")
    long = ToolPreview(title="t", detail="\n".join(f"l{i}" for i in range(62)))
    prompter, file = approval("n", "")

    prompter.ask(ApprovalRequest("write_file", long))

    assert "... 2 líneas más sin mostrar" in file.getvalue()
    assert "(vista previa acortada)" in file.getvalue()


class Plain(Tool):
    name = "plain"
    description = "A tool that needs approval."
    parameters = {"type": "object", "properties": {}}
    requires_approval = True

    def execute(self, arguments, context):
        return ToolResult.ok("done")


class Script:
    def __init__(self, answer, available=True):
        self.answer, self.is_available = answer, available

    def available(self):
        return self.is_available

    def ask(self, request):
        return self.answer


def test_the_results_sent_to_the_model_stay_english_in_spanish():
    set_language("es")
    said_no = ApprovalAnswer(ApprovalDecision.REJECT, "no")
    rejected = ApprovalGate(ApprovalLevel.ASK, Script(said_no))(Plain(), {})
    unavailable = ApprovalGate(ApprovalLevel.ASK, Script(REJECT, available=False))(Plain(), {})

    assert rejected.output.startswith("The user rejected this plain call, so it was not run.")
    assert 'The user said: "no"' in rejected.output
    assert unavailable.output.startswith("plain needs user approval, but nar cannot ask")


# --- trust --------------------------------------------------------------------------------

FILES = [Path("/p/.nar/tools/a.py"), Path("/p/.nar/tools/b.py")]


def test_the_trust_question_reads_in_spanish():
    set_language("es")
    prompter, file = trust("s")

    assert prompter.ask(Path("/p/.nar/tools"), FILES) is True
    out = file.getvalue()
    assert "Este proyecto tiene archivos de herramientas en" in out
    assert "a.py" in out
    assert "Se ejecutan como código con tus permisos" in out
    assert "¿Ejecutar estos archivos? [s]í / [n]o: " in out


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize(("line", "expected"), [("sí", True), ("YES", True), ("NO", False)])
def test_trust_answers_work_in_both_languages(language, line, expected):
    set_language(language)
    prompter, _ = trust(line)

    assert prompter.ask(Path("/p/.nar/tools"), FILES) is expected


@pytest.mark.parametrize("line", ["quizás", "siempre", "always"])
def test_trust_asks_again_for_unknown_answers_and_for_always(line):
    prompter, file = trust(line, "n")

    assert prompter.ask(Path("/p/.nar/tools"), FILES) is False
    assert file.getvalue().count("Run these files?") == 2


# --- API key ------------------------------------------------------------------------------


def test_the_save_question_reads_in_spanish():
    set_language("es")
    prompter, file = keys("sí")

    assert prompter.ask_save("ANTHROPIC_API_KEY") is True
    assert "¿Guardar ANTHROPIC_API_KEY para tu usuario, para la próxima vez? [s]í / [n]o: " in (
        file.getvalue()
    )


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize(("line", "expected"), [("Sí", True), ("y", True), ("no", False)])
def test_save_answers_work_in_both_languages(language, line, expected):
    set_language(language)
    prompter, _ = keys(line)

    assert prompter.ask_save("ANTHROPIC_API_KEY") is expected


def test_save_asks_again_for_always_and_unknown_answers():
    prompter, file = keys("siempre", "quizás", "n")

    assert prompter.ask_save("ANTHROPIC_API_KEY") is False
    assert file.getvalue().count("Save ANTHROPIC_API_KEY") == 3


def test_the_key_prompt_and_its_reasons_read_in_spanish():
    set_language("es")
    file = Terminal()
    seen = []

    def read_secret(prompt):
        seen.append(prompt)
        return ["a b", "sk-ok"][len(seen) - 1]

    prompter = KeyPrompter(
        Console(file=file, width=200, force_terminal=False),
        stdin=Terminal(),
        read_secret=read_secret,
    )

    assert prompter.ask_key("ANTHROPIC_API_KEY", "anthropic") == "sk-ok"
    assert "ANTHROPIC_API_KEY no está definida." in file.getvalue()
    assert "una clave no tiene espacios; vuelve a pegarla" in file.getvalue()
    assert seen[0] == "Pega tu clave de API de anthropic (oculta; Enter para cancelar): "


def test_clean_key_reasons_are_translated():
    set_language("es")

    assert clean_key("X", "a\x16b")[1] == "Ctrl+V no pega aquí: pega con clic derecho"
    assert clean_key("X", "a\x01b")[1] == "la clave tiene caracteres invisibles; vuelve a pegarla"
