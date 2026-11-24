"""Turn lifecycle: one turn at a time (Mac panel or Slack), each with a correlation id and a
cancellation token, recorded in the state store.

    running → done | stopped | abandoned | failed        (interrupted: cut off by a restart)

A stop sets the turn's token, tells the workers that can be told (the Developer container's run),
and then waits for the turn to reach its cleanup boundary: the step in progress ends, the agent run
is closed, and only then is the turn released. What already happened (a file written, a message
sent) stays done; the turn records how many tools ran and how much was said, so a partial
turn is reported as partial.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aura.state import StateStore, cap, now

STOP_WAIT = 30.0  # seconds a stop waits for the cleanup boundary


@dataclass
class Turn:
    id: str
    kind: str  # "chat" | "approval" | "slack"
    origin: str  # "mac" | "slack"
    conversation: str
    started: str = field(default_factory=now)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    released: threading.Event = field(default_factory=threading.Event, repr=False)
    state: str = "running"
    tools: int = 0  # tools that started during the turn
    chars: int = 0  # characters of reply sent
    error: str | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "origin": self.origin, "state": self.state, "started": self.started,
                "cancel_requested": self.cancelled, "tools": self.tools, "chars": self.chars}  # fmt: skip


class Lifecycle:
    """Wraps the server's turn lock: begin() takes it, end() gives it back (exactly once)."""

    def __init__(self, store: StateStore, lock: Any, on_cancel: Callable[[], None] = lambda: None):
        self.store = store
        self.lock = lock
        self.on_cancel = on_cancel  # e.g. stop the Developer container's run
        self.current: Turn | None = None
        self._guard = threading.Lock()
        self.interrupted_at_start = store.interrupt_unfinished_turns()
        failure = store.get_meta("last_failure")
        self.last_failure: str | None = failure

    def begin(self, kind: str, origin: str, conversation: str, blocking: bool = False,
              timeout: float = -1) -> Turn | None:  # fmt: skip
        """A new turn, or None when another one holds the lock (and `blocking` is False)."""
        if not self.lock.acquire(blocking, timeout if blocking else -1):
            return None
        turn = Turn(id="t-" + uuid.uuid4().hex[:12], kind=kind, origin=origin, conversation=conversation)
        try:
            self.store.begin_turn(turn.id, kind, origin, conversation)
        except Exception:
            self.lock.release()
            raise
        with self._guard:
            self.current = turn
        return turn

    def end(self, turn: Turn, state: str | None = None, error: str | None = None) -> None:
        """Record the outcome and release the lock. Safe to call twice: the second is a no-op."""
        with self._guard:
            if turn.released.is_set():
                return
            if state is None:
                state = "stopped" if turn.cancelled else "done"
            turn.state, turn.error = state, cap(error, 500)
            if self.current is turn:
                self.current = None
        try:
            self.store.finish_turn(turn.id, state, turn.tools, turn.chars, turn.error)
            if state == "failed":
                self.last_failure = f"{now()}: {turn.error or 'the turn failed'}"
                self.store.set_meta("last_failure", self.last_failure)
        finally:
            turn.released.set()
            self.lock.release()

    def stop(self, turn_id: str | None = None, wait: float = STOP_WAIT) -> dict[str, Any]:
        """Cancel the running turn (only if it is `turn_id`, when given) and wait for its cleanup
        boundary. Idempotent: stopping a finished or unknown turn changes nothing."""
        with self._guard:
            turn = self.current
        if turn is None or (turn_id is not None and turn.id != turn_id):
            past = self.store.turn(turn_id) if turn_id else None
            return {"stopped": False, "turn": past and {k: past[k] for k in ("id", "kind", "origin", "state")}}
        first = not turn.cancel.is_set()
        turn.cancel.set()
        if first:
            self.store.request_cancel(turn.id)
            try:
                self.on_cancel()
            except Exception:  # noqa: BLE001: the turn still ends at its next step
                pass
        released = turn.released.wait(wait)
        return {"stopped": released, "turn": turn.public() | {"state": turn.state if released else "stopping"}}

    def status(self) -> dict[str, Any]:
        with self._guard:
            turn = self.current
        return {"active_turn": turn.public() if turn else None, "last_failure": self.last_failure,
                "interrupted_at_start": self.interrupted_at_start}  # fmt: skip

