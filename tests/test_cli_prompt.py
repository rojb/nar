import sys

import pytest

from nar.cli.prompt import completions, read_line


def test_completions_offer_the_matching_commands_with_their_summaries():
    assert completions("/co") == [
        ("/compact", "summarize the conversation to free context"),
        ("/config", "show the effective configuration and where it came from"),
    ]


def test_completions_offer_everything_for_a_slash():
    names = [name for name, _ in completions("/")]
    assert "/model" in names
    assert "/help" in names
    assert "/mcp" in names
    assert names == sorted(names)


def test_completions_ignore_requests_paths_and_arguments():
    assert completions("hello") == []
    assert completions("/etc/hosts is what") == []
    assert completions("/model ollama") == []
    assert completions("hello /help") == []


def test_an_exact_command_is_not_offered_again():
    assert completions("/help") == []


def test_read_line_falls_back_to_input_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "from input")
    assert read_line("nar> ") == "from input"


def test_the_fallback_does_not_load_prompt_toolkit(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "x")
    monkeypatch.delitem(sys.modules, "prompt_toolkit", raising=False)
    read_line("nar> ")
    assert "prompt_toolkit" not in sys.modules


def test_a_terminal_uses_the_completing_prompt(monkeypatch):
    pytest.importorskip("prompt_toolkit")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    seen = {}

    class FakeSession:
        def __init__(self, completer=None):
            seen["completer"] = completer

        def prompt(self, text):
            return "from prompt_toolkit"

    monkeypatch.setattr("prompt_toolkit.PromptSession", FakeSession)
    assert read_line("nar> ") == "from prompt_toolkit"
    assert seen["completer"] is not None
