"""Pruning: replace old tool results with a marker, without calling the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from nar.context.manager import CHARS_PER_TOKEN, estimate_tokens
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


def shorten_unseen_results(
    messages: Sequence[Message], budget_tokens: int
) -> tuple[tuple[Message, ...], int]:
    """Cut results the model has not seen so that together they fit in ``budget_tokens``.

    They must reach the model, so they are never removed; the note tells it to ask for less
    instead of repeating a call that cannot fit.
    """
    shortened = list(messages)
    unseen = [i for i in range(unseen_start(messages), len(messages)) if _is_tool(messages[i])]
    if not unseen:
        return tuple(shortened), 0
    limit = budget_tokens * CHARS_PER_TOKEN // len(unseen)
    count = 0
    for index in unseen:
        content = shortened[index].content
        if len(content) > limit:
            omitted = len(content) - limit
            note = (
                f"\n[result cut to fit the context: {omitted} characters omitted; "
                "request a smaller part if you need more]"
            )
            shortened[index] = replace(shortened[index], content=content[:limit] + note)
            count += 1
    return tuple(shortened), count


def unseen_start(messages: Sequence[Message]) -> int:
    """Index of the last assistant message if tool results follow it, else the end."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "assistant":
            has_results = any(_is_tool(message) for message in messages[index + 1 :])
            return index if has_results else len(messages)
    return len(messages)


def _is_tool(message: Message) -> bool:
    return message.role == "tool"


def _marker(tool_name: str) -> str:
    return f"[result of {tool_name} removed to save context; call the tool again if you need it]"
