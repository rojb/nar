"""Build the agent for the CLI and run one request through the terminal view.

Imported only when a request is about to run: it pulls in the provider SDKs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from nar.agent import (
    Agent,
    CompactionStarted,
    ContextCompacted,
    LoopAborted,
    LoopDetector,
    ModelCallStarted,
    TurnLimitReached,
    approval_gate,
    build_system_prompt,
)
from nar.config import NarConfig
from nar.context import ProjectInstructions
from nar.context.compaction import CompactionError, Compactor, ContextLimits
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.mcp import McpManager
from nar.providers import factory
from nar.session import Session, SessionRecorder, SessionStore, sessions_dir
from nar.tools import create_default_registry
from nar.tools.memory import MemoryFileError, MemoryStore, memory_path
from nar.ui.agent_view import AgentView
from nar.ui.approval_prompt import TerminalPrompter


class Outcome(StrEnum):
    FINISHED = "finished"
    TURN_LIMIT = "turn_limit"
    LOOP = "loop"


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
    limits = ContextLimits(config.context_window, config.context_threshold)
    return Agent(
        provider,
        registry,
        context,
        max_turns=config.max_turns,
        gate=approval_gate(config.approval, prompter, registry.context),
        compactor=Compactor(provider, limits),
        detector=LoopDetector(),
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


def run_request(
    agent: Agent, view: AgentView, prompt: str, recorder: SessionRecorder | None = None
) -> Outcome:
    """Run one request, showing and saving as it goes. Exceptions propagate after cleanup."""
    outcome = Outcome.FINISHED
    try:
        # closing(): an interrupt in the view still closes the generator, which
        # removes the unfinished request from the conversation.
        with closing(agent.run(prompt)) as events:
            for event in events:
                # Before a model call every earlier tool call has its result.
                if recorder is not None and isinstance(event, ModelCallStarted):
                    recorder.save()
                view.handle(event)
                if isinstance(event, TurnLimitReached):
                    outcome = Outcome.TURN_LIMIT
                elif isinstance(event, LoopAborted):
                    outcome = Outcome.LOOP
    finally:
        view.finish()
        # Here a cancelled or failed request has already been rolled back.
        if recorder is not None:
            recorder.save()
    return outcome


def open_session(
    agent: Agent, view: AgentView, cwd: Path, resumed: Session | None = None
) -> SessionRecorder:
    """The recorder of this run: a new session, or ``resumed`` loaded into the conversation."""
    provider = agent.provider
    session = resumed or Session.new(cwd, provider.name, provider.model)
    recorder = SessionRecorder(SessionStore(sessions_dir()), session, agent.context, view.warn)
    if resumed is not None:
        continue_session(agent, view, recorder, resumed, cwd)
    return recorder


def continue_session(
    agent: Agent, view: AgentView, recorder: SessionRecorder, session: Session, cwd: Path
) -> None:
    """Load ``session``; it goes on in this folder with the configured provider and model."""
    provider = agent.provider
    if session.cwd != str(cwd):
        view.warn(f"warning: this session was started in {session.cwd}; tools run in {cwd}")
    if (session.provider, session.model) != (provider.name, provider.model):
        view.note(
            f"session used {session.provider} / {session.model}; "
            f"continuing with {provider.name} / {provider.model}"
        )
    recorder.switch(replace(session, cwd=str(cwd), provider=provider.name, model=provider.model))


def show_context_usage(agent: Agent, view: AgentView) -> None:
    agent.measure_tools()
    view.context_usage(agent.context.token_count(), agent.compactor.limits.window)


def compact_now(agent: Agent, view: AgentView, recorder: SessionRecorder | None = None) -> None:
    """``/compact``: any failure or Ctrl+C is shown, and the history stays as it was."""
    if not agent.context.messages:
        view.note("context: there is nothing to compact")
        return
    before = agent.context.token_count()
    view.handle(CompactionStarted())
    try:
        agent.compactor.compact(agent.context)
    except KeyboardInterrupt:
        view.cancelled()
    except (CompactionError, ProviderError) as exc:
        view.error(exc.message)
    else:
        view.handle(ContextCompacted(before, agent.context.token_count()))
        if recorder is not None:
            recorder.save()
    finally:
        view.finish()
    show_context_usage(agent, view)


def _saved_memory(view: AgentView) -> dict[str, str]:
    try:
        return MemoryStore(memory_path()).load()
    except MemoryFileError as exc:
        view.warn(f"warning: {exc.message}; starting without saved memory")
        return {}
