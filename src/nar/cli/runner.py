"""Build the agent for the CLI and run one request through the terminal view.

Imported only when a request is about to run: it pulls in the provider SDKs.
"""

from __future__ import annotations

from contextlib import closing
from enum import StrEnum
from pathlib import Path

from nar.agent import Agent, TurnLimitReached, approval_gate, build_system_prompt
from nar.config import NarConfig
from nar.context import ProjectInstructions
from nar.context.manager import ContextManager
from nar.providers import factory
from nar.tools import create_default_registry
from nar.tools.memory import MemoryFileError, MemoryStore, memory_path
from nar.ui.agent_view import AgentView


class Outcome(StrEnum):
    FINISHED = "finished"
    TURN_LIMIT = "turn_limit"


def create_agent(
    config: NarConfig,
    cwd: Path,
    instructions: ProjectInstructions | None,
    view: AgentView,
) -> Agent:
    """Provider, built-in tools and a fresh conversation; a missing API key fails here."""
    provider = factory.create_provider(config, on_retry=view.retry)
    view.waiting_text = f"waiting for {provider.model}..."
    context = ContextManager(build_system_prompt(cwd, instructions, _saved_memory(view)))
    return Agent(
        provider,
        create_default_registry(cwd),
        context,
        max_turns=config.max_turns,
        gate=approval_gate(config.approval),
    )


def run_request(agent: Agent, view: AgentView, prompt: str) -> Outcome:
    """Run one request, showing its events. Exceptions propagate after cleanup."""
    outcome = Outcome.FINISHED
    try:
        # closing(): an interrupt in the view still closes the generator, which
        # removes the unfinished request from the conversation.
        with closing(agent.run(prompt)) as events:
            for event in events:
                view.handle(event)
                if isinstance(event, TurnLimitReached):
                    outcome = Outcome.TURN_LIMIT
    finally:
        view.finish()
    return outcome


def _saved_memory(view: AgentView) -> dict[str, str]:
    try:
        return MemoryStore(memory_path()).load()
    except MemoryFileError as exc:
        view.warn(f"warning: {exc.message}; starting without saved memory")
        return {}
