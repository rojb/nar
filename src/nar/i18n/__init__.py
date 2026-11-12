"""Interface texts in Spanish and English (RRG-98).

``t(key, **values)`` returns the text of the active language. The language is a module-level
value set once per run (``set_language``) and read when a text is shown, never at import time.
Text that goes to the model is not translated; see the README.
"""

from __future__ import annotations

from nar.i18n.messages_en import MESSAGES as _EN
from nar.i18n.messages_es import MESSAGES as _ES

DEFAULT_LANGUAGE = "es"

_CATALOGS: dict[str, dict[str, str]] = {"es": _ES, "en": _EN}
_active = DEFAULT_LANGUAGE


class UnknownMessageKey(LookupError):
    """A text was asked for by a key that is not in the catalog (a bug, not a user error)."""

    def __init__(self, key: str, language: str) -> None:
        super().__init__(f"no interface text for key {key!r} in language {language!r}")
        self.key = key
        self.language = language


def set_language(language: str) -> None:
    """Make ``language`` (``"es"`` or ``"en"``, or a ``Language`` member) the active one."""
    global _active
    value = str(getattr(language, "value", language))
    if value not in _CATALOGS:
        known = ", ".join(_CATALOGS)
        raise ValueError(f"unknown interface language {value!r}; expected one of: {known}")
    _active = value


def current_language() -> str:
    """The active language code."""
    return _active


def t(key: str, **values: object) -> str:
    """The text for ``key`` in the active language, with ``values`` put into its placeholders."""
    try:
        text = _CATALOGS[_active][key]
    except KeyError:
        raise UnknownMessageKey(key, _active) from None
    return text.format(**values)


def t_en(key: str, **values: object) -> str:
    """The English text for ``key``, whatever the active language is.

    For an error that the user sees translated (``t``) and that also goes to the model, which
    always gets English.
    """
    try:
        text = _CATALOGS["en"][key]
    except KeyError:
        raise UnknownMessageKey(key, "en") from None
    return text.format(**values)


def template(key: str) -> str:
    """The text for ``key`` in the active language, with its ``{placeholders}`` left as they are.

    For a library that fills the placeholders itself (Click does, see ``click_words``).
    """
    try:
        return _CATALOGS[_active][key]
    except KeyError:
        raise UnknownMessageKey(key, _active) from None


def tn(key: str, count: int, **values: object) -> str:
    """Like ``t`` for a text that depends on a count: uses ``<key>.one`` when ``count`` is 1.

    Any other count uses ``<key>.other``. ``count`` is passed to the text as ``{count}``, so each
    language writes its own grammar in its own pair of keys.
    """
    suffix = "one" if count == 1 else "other"
    return t(f"{key}.{suffix}", count=count, **values)


__all__ = [
    "DEFAULT_LANGUAGE",
    "UnknownMessageKey",
    "current_language",
    "set_language",
    "t",
    "t_en",
    "template",
    "tn",
]
