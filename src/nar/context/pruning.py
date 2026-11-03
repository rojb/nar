"""Pruning: replace old tool results with a marker, without calling the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from nar.context.manager import estimate_tokens
from nar.providers.messages import Message

# A shorter result is left alone: the marker would save nothing.
MIN_PRUNED_CHARS = 200


def prune_tool_results(
    messages: Sequence[Message], keep_tokens: int
) -> tuple[tuple[Message, ...], int]:
    """Return the messages with old tool results pruned, and how many were pruned.

    Results after the last assistant message always stay: the model has not seen them yet,
    and removing them would only make it call the tool again. Then, walking from the newest
    result, the ones that fit in ``keep_tokens`` stay. Calls and their results stay paired, so
    the history is still valid for the providers.
    """
    tool_names = {call.id: call.name for message in messages for call in message.tool_calls}
    pruned = list(messages)
    count = 0
    kept_tokens = 0
    budget_open = True
    seen_by_model = False
    for index in range(len(pruned) - 1, -1, -1):
        message = pruned[index]
        seen_by_model = seen_by_model or message.role == "assistant"
        if message.role != "tool":
            continue
        kept_tokens += estimate_tokens(message.content)
        if not seen_by_model:
            continue
        budget_open = budget_open and kept_tokens <= keep_tokens
        if budget_open or len(message.content) <= MIN_PRUNED_CHARS:
            continue
        name = tool_names.get(message.tool_call_id or "", "the tool")
        pruned[index] = replace(message, content=_marker(name))
        count += 1
    return tuple(pruned), count


def _marker(tool_name: str) -> str:
    return f"[result of {tool_name} removed to save context; call the tool again if you need it]"
