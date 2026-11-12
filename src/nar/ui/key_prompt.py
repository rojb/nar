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

from nar.i18n import t
from nar.i18n.answers import Answer, parse_answer
from nar.ui.approval_prompt import _is_terminal

CTRL_V = "\x16"  # what Ctrl+V sends in the classic Windows console, where it does not paste


def clean_key(env: str, text: str) -> tuple[str, str | None]:
    """The key without common copy mistakes, and why it cannot be used (``None`` if it can).

    Removes surrounding spaces, a leading ``<env>=`` and one pair of quotes. The reason never
    contains the key.
    """
    key = text.strip()
    if key.startswith(f"{env}="):
        key = key[len(env) + 1 :].strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    if CTRL_V in key:
        return key, t("key.reason.ctrl_v")
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        return key, t("key.reason.invisible")
    if any(char.isspace() for char in key):
        return key, t("key.reason.spaces")
    return key, None


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
        """The pasted key, or ``None`` for an empty answer, end of input or Ctrl+C.

        A key that cannot be right (Ctrl+V, other invisible characters, spaces) is not
        returned: the reason is shown, without the key, and the key is asked for again.
        """
        self._console.print(Text(t("key.not_set", env=env), "yellow"), highlight=False)
        read = self._read_secret or getpass.getpass
        while True:
            try:
                # getpass writes its prompt to the console itself, never to standard output.
                pasted = read(t("key.paste", provider=provider))
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return None
            if not pasted.strip():
                return None
            key, reason = clean_key(env, pasted)
            if reason is None:
                return key
            self._console.print(Text(reason, "yellow"), highlight=False)

    def ask_save(self, env: str) -> bool:
        """An empty or unknown answer asks again; end of input and Ctrl+C mean no."""
        read = self._read_line or input
        question = t("key.save", env=env)
        while True:
            self._console.print(Text(question), end="", highlight=False)
            try:
                answer = parse_answer(read(""))
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return False
            if answer is Answer.YES:
                return True
            if answer is Answer.NO:
                return False
