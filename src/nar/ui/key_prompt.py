"""Asking for a missing API key in the terminal, and whether to save it.

The key is read with ``getpass``, so it is never echoed; nothing here prints it.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from typing import TextIO

from rich.console import Console
from rich.text import Text

from nar.ui.approval_prompt import _is_terminal

_YES = {"y", "yes"}
_NO = {"n", "no"}


class KeyPrompter:
    def __init__(
        self,
        console: Console,
        *,
        stdin: TextIO | None = None,
        read_secret: Callable[[str], str] | None = None,
        read_line: Callable[[str], str] | None = None,
    ) -> None:
        self._console = console
        self._stdin = stdin  # None: whatever sys.stdin is when asked
        self._read_secret = read_secret  # None: getpass.getpass, looked up when asked
        self._read_line = read_line  # None: the builtin input, looked up when asked

    def available(self) -> bool:
        """True when a person can answer: both the input and the console are terminals."""
        stdin = self._stdin if self._stdin is not None else sys.stdin
        return _is_terminal(stdin) and _is_terminal(self._console.file)

    def ask_key(self, env: str, provider: str) -> str | None:
        """The pasted key, or ``None`` for an empty answer, end of input or Ctrl+C."""
        self._console.print(Text(f"{env} is not set.", "yellow"), highlight=False)
        read = self._read_secret or getpass.getpass
        try:
            # getpass writes its prompt to the console itself, never to standard output.
            key = read(f"Paste your {provider} API key (hidden; Enter to cancel): ")
        except (EOFError, KeyboardInterrupt):
            self._console.print()
            return None
        return key.strip() or None

    def ask_save(self, env: str) -> bool:
        """An empty or unknown answer asks again; end of input and Ctrl+C mean no."""
        read = self._read_line or input
        question = f"Save {env} for your user, for the next time? [y]es / [n]o: "
        while True:
            self._console.print(Text(question), end="", highlight=False)
            try:
                answer = read("").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return False
            if answer in _YES:
                return True
            if answer in _NO:
                return False
