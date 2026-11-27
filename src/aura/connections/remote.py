"""Talking to Aura from Slack: "so I can use my phone to talk with Aura" (the user, 2026-11-22).

- Who: only the owner, the Slack account Aura is signed in as. Everyone else's messages stay
  notifications (live.py), never instructions.
- Where: a DM with Aura, a message that @mentions Aura, or the owner's chat channel (#aura-chat by
  default). Only while the user has switched it on in Settings.
- What: the Mac panel's own Aura, in the same conversation (the Unified Command Stream,
  docs/unified-command-stream.md, approved by Aura 2026-11-26). Same tools, same rules: anything
  outward still needs approval. A card raised by a Slack turn is asked for in Slack ("allow" /
  "deny", answered by the owner) and can also be answered on the Mac; a card raised at the Mac is
  never answered from Slack. Aura's answers go back where the owner wrote, as the Aura bot, once
  the turn ends.

Gemma's R1 floors gain one channel for approvals (the owner's own Slack account, for Slack's own
cards only); R2 and R5 apply unchanged. Aura's answers are stored by Slack, like anything else said
there.
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

from aura.tools.egress import QUERY_LINE

log = logging.getLogger(__name__)

SHOWN_LINES = 12  # of each approval's detail (a specification, a diff) in a Slack message
ALLOW = {"allow", "yes", "y", "approve", "approved", "ok", "okay", "go", "go ahead", "send it", "do it", "👍"}
DENY = {"deny", "no", "n", "don't", "dont", "stop", "cancel", "👎"}
MAX_REPLY = 11_000  # Slack's markdown_text takes up to 12,000 characters
QUEUE_LIMIT = 5  # messages waiting for their turn (Aura's ruling, 2026-11-26); more are turned away
BUSY = "I'm busy, try again in a moment."
MAC_CARD = "A request from the Mac is waiting there. Answer it on the Mac."
STOPPED = "\n\n(Stopped from the Mac before I finished.)"


@dataclass(frozen=True)
class Ask:
    """One message from the owner: where to answer, and what they said."""

    channel: str  # Slack's channel id (D… for a DM)
    ts: str
    thread_ts: str | None  # answer in this thread (None: in the channel itself)
    text: str
    place: str  # "a direct message", "#aura-chat"

    def reply_to(self) -> dict[str, Any]:
        """Where this message's turn answers (kept on the turn and on any card it raises)."""
        return {"channel": self.channel, "thread_ts": self.thread_ts, "place": self.place}


def slack_card(tool: str, args: dict[str, Any], description: str) -> str:
    """One approval as a Slack message shows it: the first line, then its detail quoted."""
    if tool == "web_search":
        return _search_card(str(args.get("query", "")), description)
    first, *rest = description.splitlines() or [tool]
    if len(rest) > SHOWN_LINES:  # e.g. a long diff: the Mac panel shows all of it
        rest = [*rest[:SHOWN_LINES], f"… {len(rest) - SHOWN_LINES} more lines (the Mac panel shows them all)"]
    return f"• {first}" + "".join(f"\n> {line.strip().lstrip('│').strip()}" for line in rest)


def unshowable_in_slack(tool: str, args: dict[str, Any]) -> bool:
    """A search query Slack can't show exactly: ``` in it would end the code block early."""
    return tool == "web_search" and "```" in str(args.get("query", ""))


def _search_card(query: str, description: str) -> str:
    """The Egress Gatekeeper's card in Slack: the same words, and the query in a code block taken
    from the tool call itself (not the card text), so Markdown and trimming can't change it."""
    shown = (f"```\n{query}\n```" if not unshowable_in_slack("web_search", {"query": query}) else
             "> This query contains ``` so Slack can't show it exactly. It can't be approved from Slack: "
             "reply **deny**, then ask again without backticks.")  # fmt: skip
    first, *rest = description.split("\n")
    return "\n".join([f"• {first}", *(shown if line.startswith(QUERY_LINE) else f"> {line}" for line in rest)])


def answer_word(text: str) -> str | None:
    """'allow', 'deny', or None (anything else)."""
    word = re.sub(r"[.!\s]+$", "", text.strip().lower())
    if word in ALLOW:
        return "allow"
    if word in DENY:
        return "deny"
    return None


class RemoteChat:
    """Runs the owner's Slack messages through the Mac panel's Aura, one at a time, between the
    Mac's own turns."""

    def __init__(self, aura: Any, turn: Any, post: Callable[[str, str, str | None], Any],
                 inbox: Any, settings_path: Path, lifecycle: Any = None, masker: Any = None,
                 choose_model: Callable[[str], str | None] | None = None, broadcast: Any = None):  # fmt: skip
        self._aura = aura  # the primary Aura: one conversation for the Mac and Slack
        self._turn = turn  # the server's lock: one turn at a time, Mac or Slack
        self._lifecycle = lifecycle  # when given (the server), turns get ids and can be stopped
        self._masker = masker
        self._post = post  # (channel, markdown, thread_ts) as the Aura bot
        # The Mac's automatic model choice for a message (server.choose_turn_model); returns the
        # Developer to hand it to, if any.
        self._choose_model = choose_model
        self._broadcast = broadcast  # the mirror feed: the Mac panel draws Slack turns from it
        self.inbox = inbox
        self._path = settings_path
        self._queue: queue.Queue[Ask] = queue.Queue(maxsize=QUEUE_LIMIT)
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
        """From the listener's thread: queue it and return at once (Slack wants its ack). A full
        queue turns the message away, so a flooded channel can't pile up turns."""
        try:
            self._queue.put_nowait(ask)
        except queue.Full:
            log.info("Slack chat queue full: a message was turned away")
            self._say(ask, BUSY)
            return
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
        from aura.brain import StaleApproval

        aura = self._aura
        turn = None
        if self._lifecycle is not None:  # waits while the Mac panel's turn runs
            turn = self._lifecycle.begin("slack", "slack", aura.thread_id, blocking=True)
        else:
            self._turn.acquire()
        outcome, error = None, None
        self._publish(turn, "turn", {"kind": "slack", "text": self._mask(ask.text), "place": ask.place})
        try:
            if aura.awaiting_approval:
                origin, _ = aura.pending_origin()
                if origin != "slack":  # §4.4: a card raised at the Mac is answered only there
                    self._answer(ask, turn, MAC_CARD)
                    return
                word = answer_word(ask.text)
                if word is None:
                    self._answer(ask, turn, "I'm still waiting for your answer on the request above. Reply **allow** or **deny**.")
                    return
                actions = aura.pending()["actions"]
                if word == "allow" and any(self._unshowable(a["tool"], a["args"]) for a in actions):
                    self._answer(ask, turn, "This request includes a search Slack can't show exactly, so nothing in it "
                                   "can be approved here. Reply **deny**, then ask again.")  # fmt: skip
                    return
                answered = aura.approval_id
                try:
                    events = aura.resume([word == "allow"] * len(actions), approval_id=aura.approval_id,
                                         turn_id=turn and turn.id, cancel=turn and turn.cancel)  # fmt: skip
                except StaleApproval as e:
                    self._answer(ask, turn, f"{e} Nothing was done.")
                    return
                self._publish(turn, "answered", {"approval_id": answered, "allowed": word == "allow"})
            else:
                aura.reload_tools()  # skills deployed and accounts connected since the last message
                try:
                    developer = self._choose_model(ask.text) if self._choose_model else None
                except ValueError as e:  # the chosen model isn't ready
                    self._answer(ask, turn, str(e))
                    return
                events = aura.stream(ask.text, developer=developer, turn_id=turn and turn.id,
                                     cancel=turn and turn.cancel, origin="slack", reply_to=ask.reply_to())  # fmt: skip
            self._finish(aura, events, ask, turn)
        except Exception as e:
            outcome, error = "failed", f"{type(e).__name__}: {e}"
            raise
        finally:
            if turn is not None:
                self._lifecycle.end(turn, outcome, error)
                self._publish(turn, "done", {"state": turn.state})
            else:
                self._turn.release()

    def _finish(self, aura: Any, events: Any, ask: Ask, turn: Any = None) -> None:
        from types import SimpleNamespace

        from aura.broadcast import TurnView

        # The same view as the Mac panel's stream: masked text, counted tools, the card, and
        # every frame published to the mirror feed. (No lifecycle in tests: a stand-in turn.)
        view = TurnView(turn or SimpleNamespace(id=None, origin="slack", tools=0, chars=0), self._masker,
                        lambda _, kind, data: self._publish(turn, kind, data))  # fmt: skip
        stopped = False
        for event in events:
            view.event(event)
            if turn is not None and turn.cancelled:  # stopped from the Mac: end at this step
                events.close()
                stopped = True
                break
        view.flush()
        if stopped:
            view.stopped()
        self._say(ask, self._compose("".join(view.reply) + (STOPPED if stopped else ""), view.card))

    @staticmethod
    def _compose(said: str, card: Any) -> str:
        """One Slack reply: what Aura said, then the card it raised, if any."""
        reply = said.strip()
        if card is not None:
            asks = [slack_card(a.tool, a.args, a.description) for a in card.actions]
            reply = (reply + "\n\n" if reply else "") + "**I need your OK to:**\n" + "\n".join(asks) + \
                "\n\nReply **allow** or **deny**."  # fmt: skip
        return reply or "…"

    def deliver(self, reply_to: dict[str, Any], said: str, card: Any, state: str | None) -> None:
        """A Slack card answered on the Mac: the rest of that turn goes back to the Slack thread
        it came from, once, when the continuation ends (the Mac panel has its own stream). A
        failed post is logged by _say; the Mac's turn has already ended either way."""
        ask = Ask(channel=str(reply_to["channel"]), ts="", thread_ts=reply_to.get("thread_ts"), text="",
                  place=str(reply_to.get("place") or "Slack"))  # fmt: skip
        ending = {"stopped": STOPPED, "abandoned": STOPPED,
                  "failed": "\n\nSorry, something went wrong on the Mac. Try again in a moment."}.get(state or "", "")
        body = self._compose(said + ending, card) if (said + ending).strip() or card is not None else "Done."
        self._say(ask, "(Answered on the Mac.)\n\n" + body)

    def _unshowable(self, tool: str, args: dict[str, Any]) -> bool:
        """A search Slack can't show exactly: ``` in it, or text the masker would hide on the card
        (a secret would then be sent while the card showed "[secret hidden]")."""
        query = str(args.get("query", "")) if tool == "web_search" else ""
        masked = self._masker is not None and self._masker.mask(query) != query
        return unshowable_in_slack(tool, args) or masked

    def _publish(self, turn: Any, kind: str, data: dict[str, Any]) -> None:
        if self._broadcast is not None and turn is not None:
            self._broadcast.publish(turn, kind, data)

    def _mask(self, text: str) -> str:
        return self._masker.mask(text) if self._masker is not None else text

    def _answer(self, ask: Ask, turn: Any, text: str) -> None:
        """A reply that isn't a model turn (a refusal, a reminder): to Slack and the mirror feed."""
        self._say(ask, text)
        self._publish(turn, "text", {"text": self._mask(text)})

    def _say(self, ask: Ask, text: str) -> None:
        if self._masker is not None:
            text = self._masker.mask(text)
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + "\n… (cut: it was too long for one Slack message)"
        try:
            self._post(ask.channel, text, ask.thread_ts)  # the Mac panel has it from the mirror feed
        except Exception as e:  # noqa: BLE001
            log.info("couldn't answer in Slack: %s", e)
            self.inbox.add("Slack", ask.place, "Aura", f"(Couldn't answer in Slack: {e})", "reply")
