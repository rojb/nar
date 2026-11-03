"""Terminal display of an agent request.

The answer goes to standard output; everything else (spinner, tool activity,
warnings) goes to standard error, so ``nar "..." > file`` keeps only the answer.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.status import Status

from nar.agent.events import (
    AgentEvent,
    AnswerCutOff,
    CompactionFailed,
    CompactionStarted,
    ContextCompacted,
    ContextPruned,
    ModelCallStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.providers.messages import TextDelta
from nar.providers.retry import RetryNotice
from nar.ui.tool_view import ToolRenderer


class AgentView:
    def __init__(self, out: Console | None = None, err: Console | None = None) -> None:
        self.out = out or Console()
        self.err = err or Console(stderr=True)
        self.tools = ToolRenderer(self.err)
        self.waiting_text = "waiting for the model..."
        self._status: Status | None = None
        self._line_open = False  # answer text printed without a final newline

    def handle(self, event: AgentEvent) -> None:
        if isinstance(event, ModelCallStarted):
            self._start_waiting(self.waiting_text)
            return
        if isinstance(event, CompactionStarted):
            self._close_line()
            self._start_waiting("compacting the conversation...")
            return
        self._stop_waiting()
        if isinstance(event, TextDelta):
            self.out.print(event.text, end="", markup=False, highlight=False, soft_wrap=True)
            self._line_open = not event.text.endswith("\n")
        elif isinstance(event, ToolCallStarted):
            self._close_line()
            self.tools.show_call(event.call.name, event.call.arguments)
        elif isinstance(event, ToolCallFinished):
            self.tools.show_result(event.call.name, event.result)
        elif isinstance(event, AnswerCutOff):
            self._close_line()
            self.warn(
                f"warning: the answer was cut off: {event.model} reached the output token limit"
            )
        elif isinstance(event, TurnLimitReached):
            self._close_line()
            self.warn(
                f"stopped after {event.max_turns} turns without a final answer; "
                "raise max_turns in .nar/config.toml to allow more"
            )
        elif isinstance(event, ContextPruned):
            self._close_line()
            done = [f"removed {event.count} old tool results"] if event.count else []
            if event.shortened:
                done.append(f"shortened {event.shortened} new tool results")
            self.note(
                f"context: {' and '.join(done)} "
                f"({event.tokens_before:,} -> {event.tokens_after:,} tokens)"
            )
        elif isinstance(event, ContextCompacted):
            self._close_line()
            self.compacted(event.tokens_before, event.tokens_after)
        elif isinstance(event, CompactionFailed):
            self._close_line()
            self.warn(
                f"warning: could not compact the conversation: {event.reason}; "
                "continuing with the full history"
            )

    def finish(self) -> None:
        """End the request's output: stop the spinner and end the answer line."""
        self._stop_waiting()
        self._close_line()

    def retry(self, notice: RetryNotice) -> None:
        self._stop_waiting()
        self.warn(str(notice))

    def warn(self, message: str) -> None:
        self.err.print(f"[yellow]{escape(message)}[/yellow]", highlight=False, soft_wrap=True)

    def note(self, message: str) -> None:
        """A dim progress line on standard error, printed literally."""
        self.err.print(f"[dim]{escape(message)}[/dim]", highlight=False, soft_wrap=True)

    def error(self, message: str) -> None:
        self.finish()
        self.err.print(f"[red]error: {escape(message)}[/red]", highlight=False, soft_wrap=True)

    def cancelled(self) -> None:
        self.finish()
        self.err.print("[yellow]cancelled[/yellow]", highlight=False)

    def compacted(self, tokens_before: int, tokens_after: int) -> None:
        self.note(f"context: compacted ({tokens_before:,} -> {tokens_after:,} tokens)")

    def context_usage(self, tokens: int, window: int) -> None:
        """How full the context is, as one dim line on standard error."""
        self.finish()
        self.note(f"context: {tokens:,} / {window:,} tokens ({tokens * 100 // window}%)")

    def _start_waiting(self, text: str) -> None:
        if self._status is None:
            self._status = self.err.status(escape(text))
            self._status.start()

    def _stop_waiting(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _close_line(self) -> None:
        if self._line_open:
            self.out.print()
            self._line_open = False
