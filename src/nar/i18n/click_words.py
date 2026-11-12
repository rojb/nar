"""The words that Click prints by itself, from the catalog (RRG-98).

Click wraps its own texts in ``gettext`` (``_("Usage:")``). Each Click module binds that function
at import (``from gettext import gettext as _``), so the bindings cannot be replaced afterwards.
But ``gettext.gettext`` and ``gettext.ngettext`` look up ``gettext.translation`` in the ``gettext``
module at every call. ``installed()`` replaces that one function while the command runs, so the
words follow the language active at the moment Click formats them.

``_MESSAGES`` and ``_PLURALS`` map Click's English message ids to catalog keys. An id that is not
listed comes back unchanged, in Click's English. The English catalog texts are Click's own ids,
and ``tests/test_click_i18n.py`` pins both sides, so a Click upgrade that rewords an id fails
loudly instead of leaving a word in English without notice.
"""

from __future__ import annotations

import gettext
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from nar.i18n import template

# gettext message id -> catalog key
_MESSAGES: dict[str, str] = {
    "Usage:": "click.usage",
    "Options": "click.options",
    "Show this message and exit.": "click.help_option",
    "Show the version and exit.": "click.version_option",
    "Error: {message}": "click.error",
    "Try '{command} {option}' for help.": "click.try_help",
    "Invalid value for {param_hint}: {message}": "click.invalid_value_for",
    "Invalid value: {message}": "click.invalid_value",
    "Missing argument": "click.missing_argument",
    "Missing option": "click.missing_option",
    "Missing parameter": "click.missing_parameter",
    "No such option {name!r}.": "click.no_such_option",
    "Option {name!r} does not take a value.": "click.option_no_value",
}

# ngettext singular message id -> catalog key without ".one" / ".other"
_PLURALS: dict[str, str] = {
    "{value!r} is not {choice}.": "click.not_choice",
    "Option {name!r} requires an argument.": "click.option_requires",
    "Got unexpected extra argument ({args})": "click.extra_argument",
    "Did you mean {possibility}?": "click.did_you_mean",
}

CLICK_DOMAIN = "messages"  # Click never sets a text domain, so gettext uses the default one


class ClickTranslations(gettext.NullTranslations):
    """Click's message ids answered from the catalog, in the active language."""

    def gettext(self, message: str) -> str:
        key = _MESSAGES.get(message)
        return message if key is None else template(key)

    def ngettext(self, msgid1: str, msgid2: str, n: int) -> str:
        base = _PLURALS.get(msgid1)
        if base is None:
            return msgid1 if n == 1 else msgid2
        return template(f"{base}.{'one' if n == 1 else 'other'}")


_TRANSLATIONS = ClickTranslations()


@contextmanager
def installed() -> Iterator[None]:
    """Make Click's words follow the catalog until the block ends."""
    original: Callable[..., Any] = gettext.translation

    def translation(domain: str, *args: Any, **kwargs: Any) -> gettext.NullTranslations:
        if domain == CLICK_DOMAIN:
            return _TRANSLATIONS
        return original(domain, *args, **kwargs)  # type: ignore[no-any-return]

    gettext.translation = translation  # type: ignore[assignment]
    try:
        yield
    finally:
        gettext.translation = original  # type: ignore[assignment]


def message_ids() -> dict[str, str]:
    """Every id the catalog translates, mapped to its catalog key (singular ids of the plurals)."""
    return {**_MESSAGES, **{msgid: f"{base}.one" for msgid, base in _PLURALS.items()}}
