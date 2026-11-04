"""The ``task`` tool: run another agent with its own conversation and return its answer.

The subagent shares nothing with the main conversation, so what it reads never reaches the
main context. What it may do is decided by the tools in its registry, not by asking its model
to behave, and its calls go through the same approval gate and hooks as the main agent's.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Protocol

from nar.agent.events import (
    LoopAborted,
    ModelCallStarted,
    RequestFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loop import Agent, ToolGate
from nar.agent.loops import LoopDetector
from nar.agent.prompt import build_subagent_prompt
from nar.context.compaction import Compactor
from nar.context.instructions import ProjectInstructions
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.registry import ToolRegistry

# Not given to a general subagent: task would allow nesting, and the one todos list
# belongs to the main agent.
MAIN_AGENT_ONLY = ("task", "todos")

_LAST_MESSAGE = (
    "Your last message is the only thing the main agent receives, and it has not seen what you "
    "did. Make it complete on its own: give the answer, with the file paths that support it."
)


@dataclass(frozen=True)
class SubagentType:
    name: str
    role: str
    tools: tuple[str, ...] | None  # None: every tool of the main agent but MAIN_AGENT_ONLY


SUBAGENT_TYPES = {
    "explorer": SubagentType(
        "explorer",
        "You are a read-only explorer working for the main agent. Search and read the code to "
        f"answer the request; you cannot change anything. {_LAST_MESSAGE}",
        ("read_file", "grep", "glob", "list_dir"),
    ),
    "general": SubagentType(
        "general",
        f"You carry out one task for the main agent, using your tools. {_LAST_MESSAGE}",
        None,
    ),
}


class SubagentProgress(Protocol):
    def update(self, kind: str, tool_calls: int) -> None:
        """A subagent of ``kind`` has made ``tool_calls`` calls so far."""

    def pause(self) -> None:
        """Stop any waiting line: the user is about to be asked something."""


class TaskTool(Tool):
    name = "task"
    description = (
        "Run a subagent with its own conversation and receive only its final answer, so what "
        "it reads does not fill this conversation. Use 'explorer' (read only) for broad searches "
        "of the code, such as exploring a repository or finding how something works. Use "
        "'general' for a separate piece of work that also needs to change things. Do not use it "
        "to read a file you already know. It cannot see this conversation, so the prompt must "
        "say everything it needs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "subagent": {
                "type": "string",
                "enum": list(SUBAGENT_TYPES),
                "description": "Which subagent to run.",
            },
            "prompt": {
                "type": "string",
                "minLength": 1,
                "description": "The complete task, including what to report back.",
            },
        },
        "required": ["subagent", "prompt"],
        "additionalProperties": False,
    }
    requires_approval = False  # launching changes nothing; each call inside is checked

    def __init__(
        self,
        parent: Agent,
        *,
        max_turns: int,
        instructions: ProjectInstructions | None = None,
        progress: SubagentProgress | None = None,
    ) -> None:
        # The parent, not copies: /model and MCP tools change it after this tool is created.
        self.parent = parent
        self.max_turns = max_turns
        self.instructions = instructions
        self.progress = progress

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        kind = SUBAGENT_TYPES[arguments["subagent"]]
        conversation = ContextManager(
            build_subagent_prompt(kind.role, context.cwd, self.instructions)
        )
        agent = self._subagent(kind, conversation)
        tool_calls = turns = 0
        ending = None
        answer = ""
        try:
            with closing(agent.run(arguments["prompt"])) as events:
                for event in events:
                    if isinstance(event, ModelCallStarted):
                        turns = event.turn
                    elif isinstance(event, ToolCallStarted):
                        tool_calls += 1
                        self._report(kind, tool_calls)
                    elif isinstance(event, RequestFinished):
                        answer = event.response.content
                    elif isinstance(event, TurnLimitReached):
                        ending = f"it stopped at its limit of {event.max_turns} turns"
                    elif isinstance(event, LoopAborted):
                        ending = "it stopped because it kept repeating the same tool calls"
        except ProviderError as exc:
            return ToolResult.error(f"the {kind.name} subagent failed: {exc.message}")
        if ending is not None:
            answer = _last_text(conversation)
        if not answer.strip():
            reason = f": {ending}" if ending else ""
            return ToolResult.error(f"the {kind.name} subagent returned no answer{reason}")
        if ending is not None:
            answer += f"\n\n[The {kind.name} subagent did not finish, {ending}. "
            answer += "This answer may be incomplete.]"
        return ToolResult.ok(answer, summary=f"{kind.name}: {tool_calls} tool calls, {turns} turns")

    def _subagent(self, kind: SubagentType, conversation: ContextManager) -> Agent:
        parent = self.parent
        registry = self._registry_for(kind)
        compactor = None
        if parent.compactor is not None:
            compactor = Compactor(parent.provider, parent.compactor.limits)
        return Agent(
            parent.provider,
            registry,
            conversation,
            max_turns=self.max_turns,
            gate=self._gate(),
            compactor=compactor,
            detector=LoopDetector(),
            hooks=parent.hooks,
        )

    def _registry_for(self, kind: SubagentType) -> ToolRegistry:
        registry = self.parent.registry
        if kind.tools is not None:
            return registry.subset(kind.tools)
        return registry.subset(n for n in registry.names() if n not in MAIN_AGENT_ONLY)

    def _gate(self) -> ToolGate | None:
        gate = self.parent.gate
        if gate is None:
            return None

        def asking(tool: Tool, arguments: Mapping[str, Any]) -> ToolResult | None:
            if tool.requires_approval and self.progress is not None:
                self.progress.pause()
            return gate(tool, arguments)

        return asking

    def _report(self, kind: SubagentType, tool_calls: int) -> None:
        if self.progress is not None:
            self.progress.update(kind.name, tool_calls)


def _last_text(conversation: ContextManager) -> str:
    for message in reversed(conversation.messages):
        if message.role == "assistant" and message.content.strip():
            return message.content
    return ""
