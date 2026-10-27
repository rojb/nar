"""Cap on the amount of tool output sent to the model."""

from __future__ import annotations

MAX_OUTPUT_CHARS = 30_000

# Room kept for the marker so head plus marker never exceeds the limit.
_MARKER_RESERVE = 100


def truncate_output(text: str) -> str:
    """Return ``text``, or its head followed by a marker when it is over the limit."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = text[: MAX_OUTPUT_CHARS - _MARKER_RESERVE]
    omitted = len(text) - len(head)
    return f"{head}\n[output truncated: {omitted} characters omitted]"
