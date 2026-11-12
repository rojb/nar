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
    LoopAborted,
    LoopDetected,
    ModelCallStarted,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loops import LoopKind
from nar.i18n import t, tn
from nar.providers.messages import TextDelta
from nar.providers.retry import RetryNotice
from nar.ui.tool_view import ToolRenderer


def _times(count: int) -> str:
    """``twice``, ``three times``, ... up to six; a bigger count is written with digits."""
    if 2 <= count <= 6:
        return t(f"agent.times.{count}")
    return t("agent.times.other", count=count)


def _detected(event: LoopDetected) -> str:
    """The warning shown when the agent loops for the first time."""
    prefix = t("warning.prefix")
    if event.kind is LoopKind.OSCILLATION:
        return t("agent.loop.alternate_detected", prefix=prefix, tool=event.call.name)
    times = _times(event.count)
    return t("agent.loop.repeat_detected", prefix=prefix, tool=event.call.name, times=times)


def _aborted(event: LoopAborted) -> str:
    """The line shown when the agent kept looping after the warning."""
    key = "alternate" if event.kind is LoopKind.OSCILLATION else "repeat"
    return t(f"agent.loop.{key}_aborted", tool=event.call.name)


def _pruned(event: ContextPruned) -> str:
    """The context line for removed and/or shortened tool results, plural-aware as a whole."""
    values = {
        "removed": event.count,
        "shortened": event.shortened,
        "before": f"{event.tokens_before:,}",
        "after": f"{event.tokens_after:,}",
    }
    removed = "one" if event.count == 1 else "other"
    shortened = "one" if event.shortened == 1 else "other"
    if event.count and event.shortened:
        return t(f"agent.context.both.{removed}_{shortened}", **values)
    if event.count:
        return t(f"agent.context.removed.{removed}", **values)
    return t(f"agent.context.shortened.{shortened}", **values)


class SubagentLine:
    """One updating line while a subagent works; its activity is otherwise folded away."""

    def __init__(self, view: AgentView) -> None:
        self._view = view

    def update(self, kind: str, tool_calls: int) -> None:
        self._view.show_progress(tn("agent.subagent_progress", tool_calls, kind=kind))

    def pause(self) -> None:
        self._view.finish()


class AgentView:
    def __init__(self, out: Console | None = None, err: Console | None = None) -> None:
        self.out = out or Console()
        self.err = err or Console(stderr=True)
        self.tools = ToolRenderer(self.err)
        self.waiting_text: str | None = None  # None: the default text, in the active language
        self._status: Status | None = None
        self._line_open = False  # answer text printed without a final newline

    def handle(self, event: AgentEvent) -> None:
        if isinstance(event, ModelCallStarted):
            self._start_waiting(self.waiting_text or t("agent.waiting_model"))
            return
        if isinstance(event, CompactionStarted):
            self._close_line()
            self._start_waiting(t("agent.compacting"))
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
            self.warn(t("agent.cut_off", prefix=t("warning.prefix"), model=event.model))
        elif isinstance(event, TurnLimitReached):
            self._close_line()
            self.warn(t("agent.turn_limit", max_turns=event.max_turns))
        elif isinstance(event, LoopDetected):
            self._close_line()
            self.warn(_detected(event))
        elif isinstance(event, LoopAborted):
            self._close_line()
            self.warn(_aborted(event))
        elif isinstance(event, ContextPruned):
            self._close_line()
            self.note(_pruned(event))
        elif isinstance(event, ContextCompacted):
            self._close_line()
            self.compacted(event.tokens_before, event.tokens_after)
        elif isinstance(event, CompactionFailed):
            self._close_line()
            self.warn(t("agent.compaction_failed", prefix=t("warning.prefix"), reason=event.reason))

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
        self.err.print(f"[yellow]{escape(t('agent.cancelled'))}[/yellow]", highlight=False)

    def compacted(self, tokens_before: int, tokens_after: int) -> None:
        self.note(
            t("agent.context.compacted", before=f"{tokens_before:,}", after=f"{tokens_after:,}")
        )

    def context_usage(self, tokens: int, window: int) -> None:
        """How full the context is, as one dim line on standard error."""
        self.finish()
        self.note(
            t(
                "agent.context.usage",
                tokens=f"{tokens:,}",
                window=f"{window:,}",
                percent=tokens * 100 // window,
            )
        )

    @property
    def subagents(self) -> SubagentLine:
        return SubagentLine(self)

    def show_progress(self, text: str) -> None:
        """Change the waiting line to ``text``, starting it if it is not there."""
        if self._status is None:
            self._start_waiting(text)
        else:
            self._status.update(escape(text))

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
