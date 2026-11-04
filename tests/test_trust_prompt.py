import io
from pathlib import Path

import pytest
from rich.console import Console

from nar.ui.trust_prompt import TrustPrompter

FILES = [Path("/p/.nar/tools/[bold]a.py"), Path("/p/.nar/tools/b.py")]


def prompter(*answers):
    buffer = io.StringIO()
    replies = iter(answers)

    def read(_):
        reply = next(replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    return TrustPrompter(Console(file=buffer, width=120), read_line=read), buffer


def test_it_lists_the_files_literally_and_says_they_run_as_code():
    ask, buffer = prompter("y")
    assert ask.ask(Path("/p/.nar/tools"), FILES) is True
    text = buffer.getvalue()
    assert "[bold]a.py" in text
    assert "b.py" in text
    assert "run as code with your permissions" in text


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (["yes"], True),
        ([" Y "], True),
        (["n"], False),
        (["no"], False),
        (["", "maybe", "y"], True),
        ([EOFError()], False),
        ([KeyboardInterrupt()], False),
    ],
)
def test_answers(answers, expected):
    ask, _ = prompter(*answers)
    assert ask.ask(Path("/p"), FILES) is expected


def test_it_is_not_available_without_terminals():
    assert TrustPrompter(Console(file=io.StringIO()), stdin=io.StringIO()).available() is False
