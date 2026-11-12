"""Yes / no / always answers to the questions nar asks, in both languages (RRG-98).

The accepted set does not depend on the interface language: a Spanish question accepts ``yes``
and an English one accepts ``sí``. Prompts call :func:`parse_answer` and keep only the answers
their question offers (the trust and API key questions do not offer ``ALWAYS``).
"""

from __future__ import annotations

import unicodedata
from enum import Enum


class Answer(Enum):
    YES = "yes"
    NO = "no"
    ALWAYS = "always"


_ANSWERS: dict[str, Answer] = {
    "y": Answer.YES,
    "yes": Answer.YES,
    "s": Answer.YES,
    "si": Answer.YES,
    "n": Answer.NO,
    "no": Answer.NO,
    "a": Answer.ALWAYS,
    "always": Answer.ALWAYS,
    "siempre": Answer.ALWAYS,
}


def _normalize(text: str) -> str:
    """Trim, lowercase and drop accents (``" SÍ "`` becomes ``"si"``)."""
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def parse_answer(text: str) -> Answer | None:
    """The answer ``text`` stands for, or ``None`` when it is not one of the known words."""
    return _ANSWERS.get(_normalize(text))
