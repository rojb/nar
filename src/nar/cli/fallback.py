"""When the provider in use cannot answer, offer the others and run the request again.

Nothing switches without asking: other providers may cost more, and the conversation
would go to another company.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

from nar.config import DEFAULT_MODELS, ProviderName
from nar.errors import NarError, ProviderError
from nar.i18n import t
from nar.providers import factory
from nar.ui.provider_menu import ProviderOption

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nar.agent import Agent
    from nar.session import SessionRecorder
    from nar.ui.agent_view import AgentView


class Menu(Protocol):
    def available(self) -> bool: ...
    def choose(self, failed: str, options: Sequence[ProviderOption]) -> str | None: ...


def provider_options(exclude: set[str]) -> list[ProviderOption]:
    options = []
    for name in ProviderName:
        if name.value in exclude:
            continue
        env = factory.API_KEY_ENVS.get(name)
        if env is None:
            status = t("provider.status.local")
        elif os.environ.get(env, "").strip():
            status = t("provider.status.key_set")
        else:
            status = t("provider.status.no_key")
        options.append(ProviderOption(name.value, DEFAULT_MODELS[name], status))
    return options


def retry_elsewhere(
    agent: Agent,
    view: AgentView,
    line: str,
    recorder: SessionRecorder | None,
    error: ProviderError,
    menu: Menu,
) -> bool:
    """Offer the other providers; ``True`` once another one ran ``line``.

    The failed request was rolled back by the agent, so running ``line`` again starts from
    the conversation as it was before it.
    """
    from nar.cli.runner import run_request, switch_model

    if not error.unavailable or not menu.available():
        return False
    tried = {agent.provider.name}
    while True:
        options = provider_options(tried)
        if not options:
            return False
        try:
            picked = menu.choose(agent.provider.name, options)
            if picked is None:
                return False
            switch_model(agent, view, picked)
        except NarError as exc:
            view.error(exc.message)
            continue
        except KeyboardInterrupt:
            view.cancelled()
            return False
        tried.add(picked)
        try:
            run_request(agent, view, line, recorder)
        except ProviderError as exc:
            view.error(exc.message)
            if not exc.unavailable:
                return True  # the request was handled here; nothing else to offer
            continue
        return True
