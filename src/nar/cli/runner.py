"""Build the agent for the CLI and run one request through the terminal view.

Imported only when a request is about to run: it pulls in the provider SDKs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import closing, contextmanager
from enum import StrEnum
from pathlib import Path

from nar.agent import Agent, TurnLimitReached, approval_gate, build_system_prompt
from nar.config import NarConfig
from nar.context import ProjectInstructions
from nar.context.manager import ContextManager
from nar.mcp import McpManager
from nar.providers import factory
from nar.tools import create_default_registry
from nar.tools.memory import MemoryFileError, MemoryStore, memory_path
from nar.ui.agent_view import AgentView
from nar.ui.approval_prompt import TerminalPrompter


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
    registry = create_default_registry(cwd)
    prompter = TerminalPrompter(view.tools)
    return Agent(
        provider,
        registry,
        context,
        max_turns=config.max_turns,
        gate=approval_gate(config.approval, prompter, registry.context),
    )


@contextmanager
def connect_mcp(config: NarConfig, agent: Agent, view: AgentView) -> Iterator[McpManager]:
    """Connect the configured MCP servers, offer their tools to ``agent``, close them at the end.

    Without enabled servers nothing is printed, loaded or started. Closing happens on every
    way out: normal end, ``exit``, end of input, Ctrl+C and errors.
    """
    manager = McpManager.connect(config.mcp_servers, os.environ, note=view.note, warn=view.warn)
    try:
        manager.register_tools(agent.registry, warn=view.warn)
        yield manager
    finally:
        manager.close()


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
