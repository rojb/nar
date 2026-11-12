"""The tool, agent, session and MCP views in Spanish (RRG-98)."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from nar.agent.events import (
    AnswerCutOff,
    CompactionFailed,
    CompactionStarted,
    ContextCompacted,
    ContextPruned,
    LoopAborted,
    LoopDetected,
    ModelCallStarted,
    ToolCallFinished,
    TurnLimitReached,
)
from nar.agent.loops import LoopKind
from nar.i18n import set_language
from nar.mcp.manager import ServerStatus
from nar.providers.messages import Message, ToolCall
from nar.session.checkpoints import Checkpoint
from nar.session.store import Session
from nar.tools.base import ToolResult
from nar.ui.agent_view import AgentView
from nar.ui.mcp_view import render_mcp_status
from nar.ui.session_view import render_checkpoints, render_history, render_sessions
from nar.ui.tool_view import ToolRenderer

CALL = ToolCall("c1", "read_file", {"path": "pyproject.toml"})
STAMP = "2026-11-04T12:00:00+00:00"


@pytest.fixture(autouse=True)
def spanish():
    set_language("es")


def buffers():
    return io.StringIO(), io.StringIO()


def make_view():
    out, err = buffers()
    view = AgentView(
        Console(file=out, width=100, force_terminal=False),
        Console(file=err, width=100, force_terminal=False),
    )
    return view, err


def console():
    buffer = io.StringIO()
    return Console(file=buffer, width=200, force_terminal=False), buffer


def session(number):
    return Session(f"id-{number}", STAMP, STAMP, "/project", "hola", "m", (Message.user("hi"),))


# --- tool view ----------------------------------------------------------------------------


def test_a_result_without_summary_is_described_in_spanish_and_the_output_is_untouched():
    buffer = io.StringIO()
    renderer = ToolRenderer(Console(file=buffer, width=100, force_terminal=False))
    result = ToolResult.ok("one\ntwo\nthree")

    renderer.show_result("read_file", result)

    assert "líneas: 3, caracteres: 13" in buffer.getvalue()
    assert result.output == "one\ntwo\nthree"  # what the model receives


def test_the_tool_error_that_reaches_the_model_stays_as_the_tool_wrote_it():
    buffer = io.StringIO()
    renderer = ToolRenderer(Console(file=buffer, width=100, force_terminal=False))
    result = ToolResult.error("old_string not found in a.txt")

    renderer.show_result("edit_file", result)

    assert "old_string not found in a.txt" in buffer.getvalue()
    assert result.output == "old_string not found in a.txt"


# --- agent view ---------------------------------------------------------------------------


def test_the_cut_off_answer_warning_starts_with_aviso():
    view, err = make_view()

    view.handle(AnswerCutOff("qwen"))

    assert err.getvalue().strip() == (
        "aviso: la respuesta se cortó: qwen alcanzó el límite de tokens de salida"
    )


def test_turn_limit_and_compaction_failure():
    view, err = make_view()

    view.handle(TurnLimitReached(3))
    view.handle(CompactionFailed("the model returned an empty summary"))

    text = err.getvalue()
    assert "se detuvo tras 3 turnos sin una respuesta final" in text
    assert (
        "aviso: no se pudo compactar la conversación: the model returned an empty summary" in text
    )
    assert "se continúa con el historial completo" in text


def test_loop_warning_and_abort_lines():
    view, err = make_view()

    view.handle(LoopDetected(LoopKind.REPEAT, CALL, 3))
    view.handle(LoopDetected(LoopKind.REPEAT, CALL, 9))
    view.handle(LoopAborted(LoopKind.REPEAT, CALL, 3))
    view.handle(LoopDetected(LoopKind.OSCILLATION, CALL, 2))
    view.handle(LoopAborted(LoopKind.OSCILLATION, CALL, 2))

    lines = err.getvalue().splitlines()
    assert lines[0] == (
        "aviso: el agente repitió read_file con los mismos argumentos tres veces; "
        "se le pidió que cambie de estrategia"
    )
    assert "con los mismos argumentos 9 veces;" in lines[1]
    assert lines[2] == "detenido: el agente siguió repitiendo read_file tras la advertencia"
    assert lines[3].startswith("aviso: el agente alternó entre las mismas llamadas (read_file)")
    assert lines[4].startswith("detenido: el agente siguió alternando entre las mismas llamadas")


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            ContextPruned(1, 9000, 8000),
            "contexto: se eliminó 1 resultado antiguo de herramienta (9,000 -> 8,000 tokens)",
        ),
        (
            ContextPruned(3, 9000, 8000),
            "contexto: se eliminaron 3 resultados antiguos de herramientas (9,000 -> 8,000 tokens)",
        ),
        (
            ContextPruned(0, 5000, 2000, shortened=1),
            "contexto: se acortó 1 resultado nuevo de herramienta (5,000 -> 2,000 tokens)",
        ),
        (
            ContextPruned(2, 9, 8, shortened=1),
            "contexto: se eliminaron 2 resultados antiguos de herramientas y se acortó 1 "
            "resultado nuevo (9 -> 8 tokens)",
        ),
        (
            ContextPruned(1, 9, 8, shortened=4),
            "contexto: se eliminó 1 resultado antiguo de herramienta y se acortaron 4 "
            "resultados nuevos (9 -> 8 tokens)",
        ),
        (ContextCompacted(9000, 1500), "contexto: compactado (9,000 -> 1,500 tokens)"),
    ],
)
def test_context_lines(event, expected):
    view, err = make_view()

    view.handle(event)

    assert err.getvalue().strip() == expected


def test_context_usage_and_cancelled():
    view, err = make_view()

    view.context_usage(1200, 8000)
    view.cancelled()

    assert "contexto: 1,200 / 8,000 tokens (15%)" in err.getvalue()
    assert "cancelado" in err.getvalue()


def test_waiting_lines_follow_the_language_when_they_are_shown():
    view, _ = make_view()

    view.handle(ModelCallStarted(1))
    assert "esperando al modelo..." in view._status.status
    view.handle(CompactionStarted())
    view.finish()
    view.handle(CompactionStarted())

    assert "compactando la conversación..." in view._status.status


def test_subagent_progress_has_a_singular_and_a_plural():
    view, _ = make_view()

    view.subagents.update("explorer", 1)
    assert "explorer: 1 llamada a herramientas..." in view._status.status
    view.subagents.update("explorer", 2)
    assert "explorer: 2 llamadas a herramientas..." in view._status.status


def test_an_agent_view_does_not_capture_the_language_when_it_is_created():
    set_language("en")
    view, err = make_view()
    set_language("es")

    view.handle(AnswerCutOff("m"))

    assert err.getvalue().startswith("aviso:")


def test_a_failed_tool_call_event_is_still_shown_with_the_tool_text():
    view, err = make_view()

    view.handle(ToolCallFinished(CALL, ToolResult.error("no such file")))

    assert "no such file" in err.getvalue()


# --- session view -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("invalid", "expected"),
    [
        (1, "aviso: 1 archivo de sesión no es válido y se omitió"),
        (2, "aviso: 2 archivos de sesión no son válidos y se omitieron"),
    ],
)
def test_invalid_session_files_get_their_own_sentence_per_count(invalid, expected):
    out, _ = console()
    err, err_buffer = console()

    render_sessions([session(1)], None, invalid, out, err)

    assert err_buffer.getvalue().strip() == expected


def test_the_session_list_is_spanish():
    out, buffer = console()
    err, _ = console()

    render_sessions([session(n) for n in range(22)], "id-1", 0, out, err)

    lines = buffer.getvalue().splitlines()
    assert lines[1].startswith("* id-1") and "1 mensaje  /project  hi" in lines[1]
    assert "(2 sesiones más antiguas sin mostrar)" in lines[-1]


def test_no_sessions_and_no_checkpoints():
    out, buffer = console()
    err, _ = console()

    render_sessions([], None, 0, out, err)
    render_checkpoints([], out)

    assert "no hay sesiones guardadas" in buffer.getvalue()
    assert "esta sesión no tiene puntos de control" in buffer.getvalue()


def test_checkpoints_and_history_are_spanish():
    out, buffer = console()
    messages = (Message.user("hi"), Message.user("hello"))

    render_checkpoints(
        [Checkpoint(1, "base", STAMP, messages), Checkpoint(2, "", STAMP, messages[:1])], out
    )
    render_history([Message.user(str(n)) for n in range(21)], out)

    text = buffer.getvalue()
    assert "2 mensajes  base" in text
    assert text.splitlines()[1].endswith("1 mensaje")
    assert "(1 mensaje anterior sin mostrar)" in text


def test_a_cut_message_says_how_much_was_left_out():
    out, buffer = console()

    render_history([Message.user("x" * 2005)], out)

    assert "[cortado: 5 caracteres más]" in buffer.getvalue()


# --- MCP view -----------------------------------------------------------------------------


def render_mcp(statuses, only=None):
    out, err = buffers()
    render_mcp_status(
        statuses,
        Console(file=out, width=140, force_terminal=False),
        only=only,
        err=Console(file=err, width=140, force_terminal=False),
    )
    return out.getvalue(), err.getvalue()


def test_mcp_servers_show_translated_words_and_untouched_names_and_reasons():
    statuses = [
        ServerStatus("linear", "http", "connected", ("mcp__linear__get_issue",)),
        ServerStatus(
            "files", "stdio", "failed", error="command 'npx' not found", log_path=Path("f.log")
        ),
        ServerStatus("old", "stdio", "disabled"),
        ServerStatus("mute", "stdio", "failed"),
    ]

    shown, _ = render_mcp(statuses)

    assert "linear  (http)  conectado" in shown
    assert "  1 herramienta" in shown
    assert "    mcp__linear__get_issue" in shown
    assert "files  (stdio)  con error" in shown
    assert "command 'npx' not found" in shown
    assert "registro: f.log" in shown
    assert "old  (stdio)  desactivado" in shown
    assert "error desconocido" in shown


def test_mcp_with_no_servers_and_an_unknown_name():
    none, _ = render_mcp([])
    _, err = render_mcp([ServerStatus("a", "http", "connected")], only="zzz")

    assert "No hay servidores MCP configurados. Define uno como [mcp_servers.<nombre>]" in none
    assert err.strip() == "error: no hay ningún servidor MCP llamado 'zzz'; configurados: a"


def test_the_english_mcp_texts_are_unchanged():
    set_language("en")

    shown, _ = render_mcp([ServerStatus("linear", "http", "connected", ("a", "b"))])

    assert "linear  (http)  connected" in shown
    assert "  2 tools" in shown
