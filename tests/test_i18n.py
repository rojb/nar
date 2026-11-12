"""The interface-text catalog (RRG-98)."""

import string

import pytest

from nar import i18n
from nar.i18n import UnknownMessageKey, current_language, messages_en, messages_es, set_language, t

# --- t / set_language ---------------------------------------------------------------------


def test_default_language_is_spanish_before_set_language(monkeypatch):
    monkeypatch.setattr(i18n, "_active", i18n.DEFAULT_LANGUAGE)

    assert current_language() == "es"
    assert t("repl.banner", provider="anthropic", model="m") == (
        "nar · anthropic · m. Escribe 'exit' para salir."
    )


def test_conftest_pins_english_for_tests():
    assert current_language() == "en"


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


# --- catalog parity -----------------------------------------------------------------------


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def parity_problems(es: dict[str, str], en: dict[str, str]) -> list[str]:
    """Describe every key or placeholder difference between two catalogs."""
    problems = [f"{key}: missing in es" for key in sorted(en.keys() - es.keys())]
    problems += [f"{key}: missing in en" for key in sorted(es.keys() - en.keys())]
    for key in sorted(es.keys() & en.keys()):
        if _placeholders(es[key]) != _placeholders(en[key]):
            problems.append(f"{key}: placeholders differ")
    return problems


def test_catalogs_have_the_same_keys_and_placeholders():
    assert parity_problems(messages_es.MESSAGES, messages_en.MESSAGES) == []


def test_parity_check_reports_a_missing_key():
    problems = parity_problems({"a": "x"}, {"a": "x", "b": "y"})

    assert problems == ["b: missing in es"]
    assert parity_problems({"a": "x", "b": "y"}, {"a": "x"}) == ["b: missing in en"]


def test_parity_check_reports_a_different_placeholder():
    problems = parity_problems({"a": "{count} archivos"}, {"a": "{n} files"})

    assert problems == ["a: placeholders differ"]
