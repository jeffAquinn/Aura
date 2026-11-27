"""The turn broadcast: every turn's events, as the user saw them, for the Mac panel's mirror feed
(GET /v1/turns/live; the Unified Command Stream, docs/unified-command-stream.md §4.2).

A turn started in Slack runs in the Mac conversation, but its events go to Slack, not to the Mac
panel's own /v1/chat stream. So each turn publishes what it shows, already masked, here: the
`turn` frame first (who asked, and what), then text, status lines, a card, and `done`. The app
draws any turn it didn't start itself from these frames.

Frames are numbered. A client that reconnects passes the last number it saw and gets what it
missed; if that has already left the bounded buffer, it gets a `reset` frame and reloads the
conversation instead. Numbers start again when the server restarts: a client follows a new server
from -1 (the app does, with each new server), and a number beyond the newest also gives `reset`.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from aura.brain import ApprovalNeeded, Changed, Event, Status, Text
from aura.masking import PATTERNS_ONLY, Masker

KEEP = 2000  # frames: a long reply streams many text chunks


def frame(ev: Event) -> tuple[str, dict[str, Any]]:
    """An event as (kind, data): the same for the Mac panel's stream and the mirror feed."""
    if isinstance(ev, Text):
        return "text", {"text": ev.text}
    if isinstance(ev, Status):
        return "status", {"text": ev.text}
    if isinstance(ev, Changed):
        return ev.kind, ev.data  # "voice", "persona", "performance"
    if isinstance(ev, ApprovalNeeded):
        return "approval", {"actions": [asdict(a) for a in ev.actions], "approval_id": ev.approval_id}
    raise TypeError(f"not a turn event: {ev!r}")


class TurnView:
    """What a turn shows, whoever drives it (the Mac panel's stream or the Slack chat): its text
    masked as it streams, its tools and characters counted, the card it raised, each as a
    (kind, data) frame, and every frame published to the mirror feed exactly as shown."""

    def __init__(self, turn: Any, masker: Masker | None = None,
                 publish: Callable[[Any, str, dict[str, Any]], None] | None = None):  # fmt: skip
        self.turn = turn
        self._texts = (masker or PATTERNS_ONLY).stream()  # a secret split across two chunks is still caught
        self._publish = publish
        self.reply: list[str] = []  # the masked text shown so far
        self.card: ApprovalNeeded | None = None

    def show(self, kind: str, data: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        """A frame that isn't one of the turn's events (thinking, an error, done)."""
        data = data or {}
        if self._publish is not None:
            self._publish(self.turn, kind, data)
        return kind, data

    def event(self, ev: Event) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(ev, Text):
            self.turn.chars += len(ev.text)
            safe = self._texts.feed(ev.text)
            return [self._text(safe)] if safe else []
        shown = self.flush()  # text that came before this event is shown before it
        if isinstance(ev, Status):
            self.turn.tools += 1
        elif isinstance(ev, ApprovalNeeded):
            self.card = ev
        return [*shown, self.show(*frame(ev))]

    def flush(self) -> list[tuple[str, dict[str, Any]]]:
        rest = self._texts.flush()
        return [self._text(rest)] if rest else []

    def stopped(self) -> tuple[str, dict[str, Any]]:
        t = self.turn
        return self.show("stopped", {"turn_id": t.id, "tools": t.tools, "partial": bool(t.tools or t.chars)})

    def note(self, text: str) -> tuple[str, dict[str, Any]]:
        """Text the server adds (a citation warning): part of the reply, already safe."""
        return self._text(text)

    def _text(self, text: str) -> tuple[str, dict[str, Any]]:
        self.reply.append(text)
        return self.show("text", {"text": text})


class TurnBroadcast:
    def __init__(self, keep: int = KEEP):
        self._frames: deque[dict[str, Any]] = deque(maxlen=keep)
        self._next = 1
        self._changed = threading.Condition()

    def publish(self, turn: Any, kind: str, data: dict[str, Any] | None = None) -> None:
        """One frame of `turn` (a lifecycle Turn). `data` must already be masked."""
        with self._changed:
            self._frames.append({"seq": self._next, "turn_id": turn.id, "origin": turn.origin, "event": kind,
                                 "data": data or {}})  # fmt: skip
            self._next += 1
            self._changed.notify_all()

    @property
    def latest(self) -> int:
        with self._changed:
            return self._next - 1

    def wait(self, after: int, timeout: float) -> list[dict[str, Any]]:
        """Frames newer than `after`, waiting up to `timeout` seconds for one. A gap (frames
        already dropped, or an `after` from before a restart) gives one `reset` frame instead."""
        with self._changed:
            if after > self._next - 1:  # numbered by a server that has since restarted
                return [self._reset()]
            if timeout > 0:
                self._changed.wait_for(lambda: self._next - 1 > after, timeout=timeout)
            oldest = self._frames[0]["seq"] if self._frames else self._next
            if after < oldest - 1:  # what it missed is no longer kept
                return [self._reset()]
            return list(itertools.islice(self._frames, after - oldest + 1, None))  # numbers are contiguous

    def _reset(self) -> dict[str, Any]:
        return {"seq": self._next - 1, "event": "reset", "data": {}}
