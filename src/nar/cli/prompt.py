"""Reading a line at the interactive prompt.

``prompt_toolkit`` is imported only when a terminal is attached, so a piped run,
``nar "..."`` and the test suite never load it.
"""

from __future__ import annotations

import sys


def completions(line: str) -> list[tuple[str, str]]:
    """The commands that complete ``line``, as ``(/name, summary)`` pairs.

    Only the first word completes, and only when it starts with ``/``: a path or a
    request is not a command.
    """
    from nar.cli import slash

    text = line.lstrip()
    if not text.startswith("/") or " " in text:
        return []
    return [
        (f"/{name}", summary)
        for name, summary in slash.commands()
        if f"/{name}".startswith(text) and f"/{name}" != text
    ]


def read_line(prompt: str) -> str:
    """One line of input, with completion when standard input is a terminal."""
    if not sys.stdin.isatty():
        return input(prompt)
    return _terminal_line(prompt)


def _terminal_line(prompt: str) -> str:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):  # noqa: ANN001, D102
            word = document.get_word_before_cursor()
            for name, summary in completions(document.text_before_cursor):
                yield Completion(name, start_position=-len(word), display_meta=summary)

    session = PromptSession(completer=SlashCompleter())
    return session.prompt(prompt)
