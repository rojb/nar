"""The interface-text catalog (RRG-98)."""

import pytest

from nar import i18n
from nar.i18n import UnknownMessageKey, current_language, set_language, t

# --- t / set_language ---------------------------------------------------------------------


def test_default_language_is_spanish_before_set_language(monkeypatch):
    monkeypatch.setattr(i18n, "_active", i18n.DEFAULT_LANGUAGE)

    assert current_language() == "es"
    assert t("repl.banner", provider="anthropic", model="m") == (
        "nar · anthropic · m. Escribe 'exit' para salir."
    )


def test_t_formats_placeholders_in_the_active_language():
    set_language("en")
    assert t("repl.banner", provider="anthropic", model="claude") == (
        "nar · anthropic · claude. Type 'exit' to leave."
    )
    set_language("es")
    assert t("repl.banner", provider="anthropic", model="claude") == (
        "nar · anthropic · claude. Escribe 'exit' para salir."
    )


def test_language_is_read_at_call_time():
    set_language("es")
    first = t("warning.prefix")
    set_language("en")

    assert (first, t("warning.prefix")) == ("aviso:", "warning:")


def test_set_language_accepts_the_config_enum():
    from nar.config import Language

    set_language(Language.ES)

    assert current_language() == "es"


def test_set_language_rejects_an_unknown_language():
    set_language("en")

    with pytest.raises(ValueError, match="fr"):
        set_language("fr")
    assert current_language() == "en"


def test_unknown_key_raises_an_error_that_names_the_key():
    with pytest.raises(UnknownMessageKey, match="no.such.key") as info:
        t("no.such.key")

    assert info.value.key == "no.such.key"


def test_missing_value_is_an_error_not_an_empty_text():
    with pytest.raises(KeyError):
        t("repl.banner", provider="anthropic")
