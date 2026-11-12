"""Offering the other providers when the current one cannot answer."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from rich.console import Console
from rich.text import Text

from nar.i18n import t
from nar.ui.approval_prompt import _is_terminal


@dataclass(frozen=True)
class ProviderOption:
    name: str
    model: str
    status: str  # already in the interface language, e.g. "key set" or "local, no key"


class ProviderMenu:
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
        """True when a person can answer: both the input and the console are terminals."""
        stdin = self._stdin if self._stdin is not None else sys.stdin
        return _is_terminal(stdin) and _is_terminal(self._console.file)

    def choose(self, failed: str, options: Sequence[ProviderOption]) -> str | None:
        """The picked provider's name, or ``None`` to stay (Enter, end of input, Ctrl+C)."""
        self._print(t("menu.cant_answer", failed=failed), "yellow")
        width = max(len(option.name) for option in options)
        for number, option in enumerate(options, 1):
            self._print(f"  {number}. {option.name:<{width}}  · {option.model}  ({option.status})")
        self._print(f"  {t('menu.stay', failed=failed)}")
        read = self._read_line or input
        while True:
            self._console.print(Text(t("menu.choose", count=len(options))), end="", highlight=False)
            try:
                answer = read("").strip()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return None
            if not answer:
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1].name

    def _print(self, text: str, style: str = "") -> None:
        self._console.print(Text(text, style), highlight=False, soft_wrap=True)
