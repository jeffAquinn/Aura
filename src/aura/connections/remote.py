"""Talking to Aura from Slack: "so I can use my phone to talk with Aura" (the user, 2026-11-22).

- Who: only the owner, the Slack account Aura is signed in as. Everyone else's messages stay
  notifications (live.py), never instructions.
- Where: a DM with Aura, a message that @mentions Aura, or the owner's chat channel (#aura-chat by
  default). Only while the user has switched it on in Settings.
- What: the same Aura, in a conversation of its own (the Mac panel keeps its own). Same tools, same
  rules: anything outward still needs approval, and Aura asks for it in Slack ("allow" / "deny",
  answered by the owner). Its answers go back where the owner wrote, as the Aura bot.

Gemma's R1 floors gain one channel for approvals (the owner's own Slack account); R2 and R5 apply
unchanged. Aura's answers are stored by Slack, like anything else said there.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ALLOW = {"allow", "yes", "y", "approve", "approved", "ok", "okay", "go", "go ahead", "send it", "do it", "👍"}
DENY = {"deny", "no", "n", "don't", "dont", "stop", "cancel", "👎"}
MAX_REPLY = 11_000  # Slack's markdown_text takes up to 12,000 characters


@dataclass(frozen=True)
class Ask:
    """One message from the owner: where to answer, and what they said."""

    channel: str  # Slack's channel id (D… for a DM)
    ts: str
    thread_ts: str | None  # answer in this thread (None: in the channel itself)
    text: str
    place: str  # "a direct message", "#aura-chat"


def answer_word(text: str) -> str | None:
    """'allow', 'deny', or None (anything else)."""
    word = re.sub(r"[.!\s]+$", "", text.strip().lower())
    if word in ALLOW:
        return "allow"
    if word in DENY:
        return "deny"
    return None


class RemoteChat:
    """Runs the owner's Slack messages through Aura, one at a time, between the Mac's own turns."""

    def __init__(self, make_aura: Callable[[], Any], turn: Any, post: Callable[[str, str, str | None], Any],
                 inbox: Any, settings_path: Path):  # fmt: skip
        self._make_aura = make_aura
        self._turn = turn  # the server's lock: one turn at a time, Mac or Slack
        self._post = post  # (channel, markdown, thread_ts) as the Aura bot
        self.inbox = inbox
        self._path = settings_path
        self._aura: Any = None
        self._pending: Any = None  # the approval Aura is waiting for, in Slack
        self._queue: queue.Queue[Ask] = queue.Queue()
        self._worker: threading.Thread | None = None

    # ── the switch (Settings → Connections → Slack) ───────────────────────────────────────

    def settings(self) -> dict[str, Any]:
        try:
            saved = json.loads(self._path.read_text())
        except (OSError, ValueError):
            saved = {}
        return {"enabled": bool(saved.get("enabled")), "channel": str(saved.get("channel") or "aura-chat")}

    def configure(self, enabled: bool, channel: str) -> dict[str, Any]:
        channel = channel.strip().lstrip("#").lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", channel):
            raise ValueError("A Slack channel name: lowercase letters, numbers, - and _.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"enabled": enabled, "channel": channel}))
        return self.settings()

    # ── messages ──────────────────────────────────────────────────────────────────────────

    def submit(self, ask: Ask) -> None:
        """From the listener's thread: queue it and return at once (Slack wants its ack)."""
        self._queue.put(ask)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="aura-remote-chat", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            ask = self._queue.get()
            try:
                self.handle(ask)
            except Exception as e:  # noqa: BLE001 -- say so in Slack, keep serving
                log.exception("Slack chat turn failed")
                self._say(ask, f"Sorry, something went wrong on the Mac ({type(e).__name__}). Try again in a moment.")

    def handle(self, ask: Ask) -> None:
        with self._turn:  # waits while the Mac panel's turn runs
            aura = self._aura or self._make_aura()
            self._aura = aura
            if self._pending is not None:
                word = answer_word(ask.text)
                if word is None:
                    self._say(ask, "I'm still waiting for your answer on the request above. Reply **allow** or **deny**.")
                    return
                actions = self._pending.actions
                self._pending = None
                events = aura.resume([word == "allow"] * len(actions))
            else:
                aura.reload_tools()  # skills deployed and accounts connected since the last message
                events = aura.stream(ask.text)
            self._finish(aura, events, ask)

    def _finish(self, aura: Any, events: Any, ask: Ask) -> None:
        from aura.brain import ApprovalNeeded, Text

        said: list[str] = []
        for event in events:
            if isinstance(event, Text):
                said.append(event.text)
            elif isinstance(event, ApprovalNeeded):
                self._pending = event
        reply = "".join(said).strip()
        if self._pending is not None:
            asks = []
            for action in self._pending.actions:
                first, *rest = action.description.splitlines() or [action.tool]
                asks.append(f"• {first}" + "".join(f"\n> {line.strip().lstrip('│').strip()}" for line in rest))
            reply = (reply + "\n\n" if reply else "") + "**I need your OK to:**\n" + "\n".join(asks) + \
                "\n\nReply **allow** or **deny**."  # fmt: skip
        self._say(ask, reply or "…")

    def _say(self, ask: Ask, text: str) -> None:
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + "\n… (cut: it was too long for one Slack message)"
        try:
            self._post(ask.channel, text, ask.thread_ts)
            self.inbox.add("Slack", ask.place, "Aura", text, "reply")  # the Mac panel shows the answer too
        except Exception as e:  # noqa: BLE001
            log.info("couldn't answer in Slack: %s", e)
            self.inbox.add("Slack", ask.place, "Aura", f"(Couldn't answer in Slack: {e})", "reply")
