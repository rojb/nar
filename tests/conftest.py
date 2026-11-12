"""Shared fixtures."""

import pytest

from nar import i18n


@pytest.fixture(autouse=True)
def _english_interface():
    """Run every test with the English interface; the default (es) is restored afterwards.

    Tests that check Spanish output call ``set_language("es")`` themselves.
    """
    i18n.set_language("en")
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
