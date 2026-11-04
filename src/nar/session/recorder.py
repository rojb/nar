"""Keeps one session's file in step with the running conversation."""

from __future__ import annotations

from collections.abc import Callable

from nar.context.manager import ContextManager
from nar.session.checkpoints import Checkpoint, CheckpointStore
from nar.session.store import Session, SessionStore


class SessionRecorder:
    def __init__(
        self,
        store: SessionStore,
        session: Session,
        context: ContextManager,
        warn: Callable[[str], None],
    ) -> None:
        self.store = store
        self.session = session
        self.context = context
        self.warn = warn
        self._warned = False
        self._checkpoints = CheckpointStore(store.directory)

    def save(self) -> None:
        """Write the conversation; a failure warns and never stops the work.

        Call it only where every tool call has its result: before a model call or when a
        request has ended.
        """
        try:
            if self.context.messages:
                self.session = self.session.with_messages(self.context.messages)
                self.store.save(self.session)
            else:  # a cancelled first request leaves nothing behind
                self.store.delete(self.session.id)
        except OSError as exc:
            if not self._warned:
                self._warned = True
                self.warn(f"warning: cannot save the session: {exc.strerror or exc}")
        else:
            self._warned = False

    def switch(self, session: Session) -> None:
        """Continue ``session``: its messages replace the conversation, later saves go to it."""
        self.context.replace_messages(session.messages)
        self.session = session

    def checkpoints(self) -> list[Checkpoint]:
        return self._checkpoints.load(self.session.id)

    def checkpoint(self, name: str) -> Checkpoint:
        """Save the conversation as a point of this session; it needs at least one message."""
        self.save()
        return self._checkpoints.add(self.session.id, name, self.context.messages)

    def restore(self, reference: str) -> Checkpoint:
        """Go back to a checkpoint; an unknown reference raises and changes nothing."""
        checkpoint = self._checkpoints.find(self.session.id, reference)
        self.context.replace_messages(checkpoint.messages)
        self.save()
        return checkpoint
