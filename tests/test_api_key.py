import io
import os

import pytest
from rich.console import Console

from nar.cli import api_key
from nar.cli.api_key import PendingKey, keep_api_key, obtain_api_key, prepare_api_key
from nar.config import NarConfig
from nar.errors import ProviderError
from nar.ui.key_prompt import KeyPrompter

SECRET = "sk-secret-123"


class TTY(io.StringIO):
    def isatty(self):
        return True


class FakePrompter:
    def __init__(self, key=SECRET, save=False, available=True):
        self.key, self.save, self._available = key, save, available
        self.asked_key = self.asked_save = 0

    def available(self):
        return self._available

    def ask_key(self, env, provider):
        self.asked_key += 1
        if isinstance(self.key, BaseException):
            raise self.key
        return self.key

    def ask_save(self, env):
        self.asked_save += 1
        return self.save


class Lines:
    def __init__(self):
        self.notes, self.warnings = [], []

    def note(self, text):
        self.notes.append(text)

    def warn(self, text):
        self.warnings.append(text)

    def all(self):
        return "\n".join(self.notes + self.warnings)


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(env, "")  # recorded, so what the code sets is undone after the test
        monkeypatch.delenv(env)
    saved = []
    monkeypatch.setattr(api_key, "save_user_variable", lambda name, value: saved.append(name))
    monkeypatch.setattr(api_key, "can_save_user_variables", lambda: True)
    return saved


@pytest.fixture
def saved(_no_keys):
    return _no_keys


def deepseek():
    return NarConfig(provider="deepseek")


# KeyPrompter


def terminal_prompter(secret="", lines=(), stdin=None):
    out = TTY()
    answers = iter(lines)

    def read_secret(prompt):
        if isinstance(secret, BaseException):
            raise secret
        return secret

    def read_line(prompt):
        value = next(answers)
        if isinstance(value, BaseException):
            raise value
        return value

    console = Console(file=out, force_terminal=False, width=200)
    prompter = KeyPrompter(
        console, stdin=stdin or TTY(), read_secret=read_secret, read_line=read_line
    )
    return prompter, out


def test_prompter_reads_the_key_and_never_prints_it():
    prompter, out = terminal_prompter(secret=f"  {SECRET}\n")
    assert prompter.ask_key("DEEPSEEK_API_KEY", "deepseek") == SECRET
    assert "DEEPSEEK_API_KEY is not set." in out.getvalue()
    assert SECRET not in out.getvalue()


@pytest.mark.parametrize("secret", ["", "   ", EOFError(), KeyboardInterrupt()])
def test_prompter_cancels_on_an_empty_answer_end_of_input_or_ctrl_c(secret):
    prompter, _ = terminal_prompter(secret=secret)
    assert prompter.ask_key("DEEPSEEK_API_KEY", "deepseek") is None


def test_prompter_asks_again_until_yes_or_no():
    prompter, out = terminal_prompter(lines=["", "maybe", "Y"])
    assert prompter.ask_save("DEEPSEEK_API_KEY") is True
    assert out.getvalue().count("Save DEEPSEEK_API_KEY for your user") == 3


@pytest.mark.parametrize("answer", ["n", EOFError(), KeyboardInterrupt()])
def test_prompter_save_means_no_on_no_end_of_input_or_ctrl_c(answer):
    prompter, _ = terminal_prompter(lines=[answer])
    assert prompter.ask_save("DEEPSEEK_API_KEY") is False


def test_prompter_is_not_available_without_a_terminal():
    piped = KeyPrompter(Console(file=TTY()), stdin=io.StringIO())
    redirected = KeyPrompter(Console(file=io.StringIO()), stdin=TTY())
    assert not piped.available()
    assert not redirected.available()


# obtain_api_key and keep_api_key


def test_a_missing_key_is_asked_for_and_set_for_this_process():
    pending = obtain_api_key(deepseek(), FakePrompter())
    assert pending.env == "DEEPSEEK_API_KEY"
    assert os.environ["DEEPSEEK_API_KEY"] == SECRET
    assert SECRET not in repr(pending)
    pending.discard()
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_nothing_is_asked_without_a_terminal():
    prompter = FakePrompter(available=False)
    assert obtain_api_key(deepseek(), prompter) is None
    assert prompter.asked_key == 0


def test_nothing_is_asked_when_the_key_is_set(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-already")
    prompter = FakePrompter()
    assert obtain_api_key(deepseek(), prompter) is None
    assert prompter.asked_key == 0


def test_nothing_is_asked_for_ollama():
    prompter = FakePrompter()
    assert obtain_api_key(NarConfig(provider="ollama"), prompter) is None
    assert prompter.asked_key == 0


def test_cancelling_sets_nothing():
    assert obtain_api_key(deepseek(), FakePrompter(key=None)) is None
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_saving_on_windows_writes_the_user_variable(saved):
    lines = Lines()
    keep_api_key(
        PendingKey("DEEPSEEK_API_KEY", SECRET), FakePrompter(save=True), lines.note, lines.warn
    )
    assert saved == ["DEEPSEEK_API_KEY"]
    assert "saved DEEPSEEK_API_KEY for your Windows user" in lines.all()
    assert SECRET not in lines.all()


def test_not_saving_keeps_it_for_this_session_only(saved):
    lines = Lines()
    keep_api_key(
        PendingKey("DEEPSEEK_API_KEY", SECRET), FakePrompter(save=False), lines.note, lines.warn
    )
    assert saved == []
    assert lines.notes == ["DEEPSEEK_API_KEY is set for this session only"]


def test_other_systems_show_the_export_line_without_the_key(monkeypatch, saved):
    monkeypatch.setattr(api_key, "can_save_user_variables", lambda: False)
    lines = Lines()
    keep_api_key(
        PendingKey("DEEPSEEK_API_KEY", SECRET), FakePrompter(save=True), lines.note, lines.warn
    )
    assert saved == []
    assert "export DEEPSEEK_API_KEY=<your key>" in lines.all()
    assert SECRET not in lines.all()


def test_a_failed_save_is_a_warning(monkeypatch):
    def fail(name, value):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(api_key, "save_user_variable", fail)
    lines = Lines()
    keep_api_key(
        PendingKey("DEEPSEEK_API_KEY", SECRET), FakePrompter(save=True), lines.note, lines.warn
    )
    assert lines.warnings == [
        "could not save DEEPSEEK_API_KEY (Access is denied); it works for this session"
    ]


# prepare_api_key


class CheckedProvider:
    def __init__(self, error=None):
        self.error = error
        self.checks = 0

    def check(self):
        self.checks += 1
        if self.error is not None:
            raise self.error


def test_startup_checks_the_pasted_key_before_saving_it(monkeypatch, saved):
    provider = CheckedProvider()
    monkeypatch.setattr("nar.providers.factory.create_provider", lambda config: provider)
    prompter, lines = FakePrompter(save=True), Lines()
    prepare_api_key(deepseek(), prompter, lines.note, lines.warn)
    assert provider.checks == 1
    assert saved == ["DEEPSEEK_API_KEY"]
    assert os.environ["DEEPSEEK_API_KEY"] == SECRET


def test_startup_with_a_rejected_key_saves_nothing_and_fails(monkeypatch, saved):
    rejected = ProviderError("deepseek rejected the API key; check DEEPSEEK_API_KEY", "deepseek")
    monkeypatch.setattr(
        "nar.providers.factory.create_provider", lambda config: CheckedProvider(rejected)
    )
    prompter, lines = FakePrompter(save=True), Lines()
    with pytest.raises(ProviderError):
        prepare_api_key(deepseek(), prompter, lines.note, lines.warn)
    assert prompter.asked_save == 0
    assert saved == []
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_startup_without_a_terminal_does_nothing(monkeypatch):
    def create(config):
        raise AssertionError("no provider is built here")

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    prepare_api_key(deepseek(), FakePrompter(available=False), Lines().note, Lines().warn)
