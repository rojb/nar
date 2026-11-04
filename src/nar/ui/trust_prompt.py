"""Asks whether the tool files of a project may run."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.text import Text

from nar.ui.approval_prompt import _is_terminal

_YES = {"y", "yes"}
_NO = {"n", "no"}


class TrustPrompter:
    def __init__(
        self,
        console: Console,
        *,
        stdin: TextIO | None = None,
        read_line: Callable[[str], str] | None = None,
    ) -> None:
        self._console = console
        self._stdin = stdin  # None: whatever sys.stdin is when asked
        self._read_line = read_line  # None: the builtin input, looked up when asked

    def available(self) -> bool:
        stdin = self._stdin if self._stdin is not None else sys.stdin
        return _is_terminal(stdin) and _is_terminal(self._console.file)

    def ask(self, folder: Path, files: list[Path]) -> bool:
        """An empty or unknown answer asks again; end of input and Ctrl+C mean no."""
        self._print(f"This project has tool files in {folder}:", "yellow")
        for path in files:
            self._print(f"  {path.name}")
        self._print("They run as code with your permissions, as soon as you allow them.")
        read = self._read_line or input
        while True:
            self._console.print(Text("Run these files? [y]es / [n]o: "), end="", highlight=False)
            try:
                answer = read("").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return False
            if answer in _YES:
                return True
            if answer in _NO:
                return False

    def _print(self, text: str, style: str = "") -> None:
        self._console.print(Text(text, style), highlight=False, soft_wrap=True)
