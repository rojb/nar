"""A missing API key, asked for in the terminal, checked, and saved only if the user agrees.

Keys stay in environment variables, as `factory` expects: the pasted key is set for this
process, and saving sets a Windows user variable, like ``setx``. Never a file, so a key
cannot end up in a repository.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from nar.config import NarConfig, ProviderName
from nar.errors import ProviderError
from nar.providers import factory

Note = Callable[[str], None]


class KeyAsker(Protocol):
    def available(self) -> bool: ...
    def ask_key(self, env: str, provider: str) -> str | None: ...
    def ask_save(self, env: str) -> bool: ...


@dataclass
class PendingKey:
    """A key set for this process that has not been checked or saved yet."""

    env: str
    value: str

    def discard(self) -> None:
        os.environ.pop(self.env, None)

    def __repr__(self) -> str:  # never show the key, even in a traceback
        return f"PendingKey(env={self.env!r})"


def obtain_api_key(config: NarConfig, prompter: KeyAsker) -> PendingKey | None:
    """Ask for the provider's key when it is missing and a person can answer.

    ``None`` means nothing was asked or the user cancelled: the factory then reports the
    missing variable, as without a terminal.
    """
    env = factory.API_KEY_ENVS.get(ProviderName(config.provider))
    if env is None or os.environ.get(env, "").strip() or not prompter.available():
        return None
    value = prompter.ask_key(env, ProviderName(config.provider).value)
    if value is None:
        return None
    os.environ[env] = value
    return PendingKey(env, value)


def keep_api_key(pending: PendingKey, prompter: KeyAsker, note: Note, warn: Note) -> None:
    """After the check passed: save it for the user if they want, on Windows."""
    if not prompter.ask_save(pending.env):
        note(f"{pending.env} is set for this session only")
        return
    if not can_save_user_variables():
        note(f"not saved: add 'export {pending.env}=<your key>' to your shell profile")
        return
    try:
        save_user_variable(pending.env, pending.value)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        warn(f"could not save {pending.env} ({reason}); it works for this session")
        return
    note(f"saved {pending.env} for your Windows user; new terminals will see it")


def prepare_api_key(config: NarConfig, prompter: KeyAsker, note: Note, warn: Note) -> None:
    """At startup: ask for a missing key, check it, and offer to save it.

    A rejected key is removed again and the error stops startup, as a missing key does.
    """
    pending = obtain_api_key(config, prompter)
    if pending is None:
        return
    try:
        note(f"checking {config.provider} · {config.model}...")
        factory.create_provider(config).check()
    except (ProviderError, KeyboardInterrupt):
        pending.discard()
        raise
    keep_api_key(pending, prompter, note, warn)


def can_save_user_variables() -> bool:
    """Only Windows has a user environment nar can write; elsewhere it is the shell profile."""
    return sys.platform == "win32"


def save_user_variable(name: str, value: str) -> None:
    """Set a Windows user environment variable, as ``setx`` does, and tell other programs."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    # New terminals read the updated environment without signing out.
    hwnd_broadcast, wm_settingchange, smto_abortifhung = 0xFFFF, 0x001A, 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 2000, None
    )
