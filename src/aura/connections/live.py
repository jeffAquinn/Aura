"""Live messages: Aura hears new Slack and Discord messages as they arrive.

The user's choice (2026-11-22): notify now; two-way chat (Aura answering in Slack or Discord)
comes later, as a design for Gemma. So nothing here acts on a message. It goes into the Inbox,
the app shows it (a notification and a card in the chat), and the model sees it at the start of
the user's next turn, framed as untrusted (R5) and counted as a private read (R2).

Discord (docs.discord.com/developers/events/gateway, read 2026-11-22):
- GET /gateway/bot gives the URL (opened with ?v=10&encoding=json). Discord sends Hello (op 10)
  with heartbeat_interval; the bot heartbeats (op 1, the last sequence number) from interval ×
  jitter, and identifies (op 2) with its token and intents.
- A Heartbeat ACK (op 11) must arrive between heartbeats, or the connection is a zombie: reconnect.
- Reconnect (op 7) or a dropped connection: resume (op 6) on resume_gateway_url with session_id
  and the last seq. Invalid Session (op 9) with d=false: identify afresh.
- Close codes 4004 and 4010-4014 mean don't reconnect (4014: a privileged intent is off).
- Without Message Content, guild message text is empty (DMs and @mentions still come through).

Slack (docs.slack.dev/apis/events-api/using-socket-mode, read 2026-11-22):
- apps.connections.open with the app-level token (xapp-, scope connections:write) gives a wss URL.
- Slack sends "hello", then envelopes {envelope_id, type, payload}. Each is acknowledged by
  sending back its envelope_id.
- "disconnect" (warning, refresh_requested, link_disabled) means reconnect: connections refresh
  every few hours.
- Events reach the app's bot user: DMs to it, @mentions, and channels it's invited to. Slack's
  PKCE desktop sign-in can't request bot scopes, so the user installs the bot from the app's page
  and pastes its token.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_TEXT = 1500  # one message as kept; the model is shown at most 10 at a time


# ── the inbox ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Incoming:
    id: int
    source: str  # "Slack" or "Discord"
    place: str  # "#aura-chat", "#general in Aura HQ", "a direct message"
    author: str
    text: str
    kind: str  # "dm", "mention", "channel"; "reply": a Slack answer Aura couldn't post
    at: float

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        when = datetime.fromtimestamp(self.at).strftime("%b %d %H:%M")
        to_aura = " (to Aura)" if self.kind in ("dm", "mention") else ""
        return f"{when} · {self.source} {self.place} · {self.author}{to_aura}: {self.text}"


class Inbox:
    """Messages heard live, newest last. A bounded memory: Slack and Discord keep the history."""

    def __init__(self, keep: int = 200, clock: Callable[[], float] = time.time):
        self._items: deque[Incoming] = deque(maxlen=keep)
        self._next = 1
        self._given = 0  # the newest id the model has been shown
        self._changed = threading.Condition()
        self.clock = clock

    def add(self, source: str, place: str, author: str, text: str, kind: str) -> Incoming:
        text = text.strip()
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "…"
        with self._changed:
            item = Incoming(self._next, source, place, author or "someone", text or "(no text)", kind, self.clock())
            self._next += 1
            self._items.append(item)
            self._changed.notify_all()
        log.info("live message from %s (%s)", source, kind)  # never the text or the author
        return item

    @property
    def latest(self) -> int:
        with self._changed:
            return self._next - 1

    def wait(self, after: int, timeout: float) -> list[Incoming]:
        """Long poll: messages newer than `after`, waiting up to `timeout` seconds for one. An
        `after` from before a restart (beyond the newest id) starts again from the beginning."""
        with self._changed:
            if after > self._next - 1:
                after = 0
            if timeout > 0:
                self._changed.wait_for(lambda: self._next - 1 > after, timeout=timeout)
            return [m for m in self._items if m.id > after]

    def recent(self, n: int = 10) -> list[Incoming]:
        with self._changed:
            return list(self._items)[-n:] if n > 0 else []

    def unseen_by_model(self, n: int = 10) -> list[Incoming]:
        """What arrived since the model last looked (the newest n), now marked as shown. The
        owner's Slack commands are turns of their own (remote.py), and a failed-post notice
        ("reply") is for the Mac panel only: neither is here."""
        with self._changed:
            fresh = [m for m in self._items if m.id > self._given and m.kind != "reply"]
            self._given = self._next - 1
            return fresh[-n:]


# ── a listener: one thread, reconnecting until stopped ─────────────────────────────────────


class Fatal(Exception):
    """A problem reconnecting can't fix (a bad token, an intent that's off): stop, and say why."""


def _connect(url: str) -> Any:
    from websockets.sync.client import connect

    return connect(url, open_timeout=20, close_timeout=3, max_size=8 * 2**20, compression=None,
                   user_agent_header="Aura (local assistant)")  # fmt: skip


def _recv_json(ws: Any, timeout: float | None) -> dict[str, Any]:
    raw = ws.recv(timeout=timeout)
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return msg if isinstance(msg, dict) else {}


def _close_code(e: Exception) -> int | None:
    rcvd = getattr(e, "rcvd", None)
    return getattr(rcvd, "code", None)


class Listener:
    source = ""

    def __init__(self, inbox: Inbox, connect: Callable[[str], Any] | None = None):
        self.inbox = inbox
        self.connect = connect or _connect
        self._stop = threading.Event()
        self._stop.set()
        self._thread: threading.Thread | None = None
        self._ws: Any = None
        self._state, self._detail = "off", ""

    def ready(self) -> bool:
        """Whether everything it needs is set up (tokens, a connected account)."""
        raise NotImplementedError

    def _session(self, stop: threading.Event) -> None:
        """One connection, until it ends (return: reconnect now; raise: trouble)."""
        raise NotImplementedError

    def _reset(self) -> None:
        """Forget anything tied to the old token (e.g. a resumable session)."""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def status(self) -> dict[str, Any]:
        state = self._state if self.running or self._state == "problem" else "off"
        return {"state": state, "detail": self._detail if state != "off" else ""}

    def start(self) -> None:
        if self.running:
            return
        self._stop = stop = threading.Event()
        self._state, self._detail = "connecting", f"Connecting to {self.source}…"
        self._thread = threading.Thread(target=self._loop, args=(stop,), name=f"aura-live-{self.source.lower()}",
                                        daemon=True)  # fmt: skip
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._state, self._detail = "off", ""
        self._reset()

    def _listening(self, detail: str) -> None:
        if self._state != "listening":
            log.info("%s live messages: listening", self.source)
        self._state, self._detail = "listening", detail

    def _loop(self, stop: threading.Event) -> None:
        delay = 2.0
        while not stop.is_set():
            try:
                self._session(stop)
                delay = 2.0
                stop.wait(0.5)  # asked to reconnect: go straight back (but never spin)
            except Fatal as e:
                if not stop.is_set():
                    self._state, self._detail = "problem", str(e)
                    log.info("%s live messages stopped: %s", self.source, e)
                return
            except Exception as e:  # noqa: BLE001 -- the network: try again, more slowly each time
                if stop.is_set():
                    return
                self._state = "connecting"
                self._detail = f"Lost the connection to {self.source} ({type(e).__name__}). Trying again in {int(delay)} s."
                log.info("%s live messages: %s; retrying in %ss", self.source, type(e).__name__, int(delay))
                stop.wait(delay)
                delay = min(delay * 2, 120)


# ── Discord: the bot's Gateway connection ───────────────────────────────────────────────────

GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT = 1 << 0, 1 << 9, 1 << 12, 1 << 15
INTENTS = GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT  # 37377
FATAL_CLOSES = {
    4004: "Discord rejected the bot token. On the Bot page, use Reset Token and paste the new one.",
    4010: "Discord refused the connection (invalid shard).",
    4011: "Discord wants this bot sharded: it's in too many servers for Aura.",
    4012: "Discord no longer accepts the API version Aura uses. Update Aura.",
    4013: "Discord refused the events Aura asked for. Update Aura.",
    4014: "The Message Content intent is off, so Discord won't deliver messages. Turn it on under Bot → "
          "Privileged Gateway Intents, then press Try Again.",
}  # fmt: skip
TEXT_MESSAGES = (0, 19)  # a message and a reply (not joins, pins or other system messages)


class DiscordGateway(Listener):
    source = "Discord"

    def __init__(self, discord: Any, inbox: Inbox, connect: Callable[[str], Any] | None = None,
                 rand: Callable[[], float] = random.random):  # fmt: skip
        super().__init__(inbox, connect)
        self.discord = discord
        self.rand = rand
        self.bot_id = ""
        self.bot_name = "Aura"
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._seq: int | None = None
        self._servers: dict[str, str] = {}
        self._channels: dict[str, tuple[str, str]] = {}  # id -> (name, server)
        discord.listener = self

    def ready(self) -> bool:
        return bool(self.discord.secrets.get("discord_bot_token") and self.discord._profile())

    def _reset(self) -> None:
        self._session_id = self._resume_url = self._seq = None

    def _gateway_url(self, token: str) -> str:
        from aura.connections.discord import API, USER_AGENT

        r = self.discord.http.get(API + "gateway/bot", headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})
        if r.status_code == 401:
            raise Fatal(FATAL_CLOSES[4004])
        url = r.json().get("url") if r.status_code == 200 else None
        return str(url or "wss://gateway.discord.gg")

    def _session(self, stop: threading.Event) -> None:
        token = self.discord.secrets.get("discord_bot_token")
        if not token:
            raise Fatal("Discord isn't connected.")
        resuming = bool(self._session_id and self._resume_url)
        base = self._resume_url if resuming else self._gateway_url(token)
        ws = self._ws = self.connect(f"{str(base).rstrip('/')}/?v=10&encoding=json")
        try:
            hello = _recv_json(ws, 30)
            if hello.get("op") != 10:
                raise ConnectionError("Discord didn't say hello")
            interval = float(hello["d"]["heartbeat_interval"]) / 1000
            if resuming:
                ws.send(json.dumps({"op": 6, "d": {"token": token, "session_id": self._session_id, "seq": self._seq}}))
            else:
                ws.send(json.dumps({"op": 2, "d": {"token": token, "intents": INTENTS,
                                                    "properties": {"os": "macos", "browser": "aura", "device": "aura"}}}))  # fmt: skip
            next_beat, acked = time.monotonic() + interval * self.rand(), True
            while not stop.is_set():
                try:
                    msg = _recv_json(ws, max(0.0, next_beat - time.monotonic()))
                except TimeoutError:
                    if not acked:
                        raise ConnectionError("Discord stopped answering heartbeats") from None  # a zombie: resume
                    ws.send(json.dumps({"op": 1, "d": self._seq}))
                    next_beat, acked = time.monotonic() + interval, False
                    continue
                op = msg.get("op")
                if op == 0:
                    self._seq = msg.get("s") if msg.get("s") is not None else self._seq
                    self._dispatch(str(msg.get("t")), msg.get("d") or {})
                elif op == 1:
                    ws.send(json.dumps({"op": 1, "d": self._seq}))
                elif op == 11:
                    acked = True
                elif op == 7:
                    return  # Discord is going away: reconnect and resume
                elif op == 9:
                    if not msg.get("d"):
                        self._reset()  # not resumable: identify again
                    stop.wait(1 + 4 * self.rand())
                    return
        except Exception as e:
            code = _close_code(e)
            if code in FATAL_CLOSES:
                self._reset()
                raise Fatal(FATAL_CLOSES[code]) from e
            if code in (4007, 4009):
                self._reset()  # the session can't be resumed
            raise
        finally:
            self._ws = None
            try:  # closing with 1000 would end the session; any other code keeps it resumable
                ws.close(code=4000, reason="reconnecting") if self._session_id else ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _dispatch(self, event: str, d: dict[str, Any]) -> None:
        if event == "READY":
            self._session_id, self._resume_url = d.get("session_id"), d.get("resume_gateway_url")
            user = d.get("user") or {}
            self.bot_id, self.bot_name = str(user.get("id") or ""), str(user.get("username") or "Aura")
            self._listening("Listening: DMs to your bot, @mentions, and every channel it can see.")
        elif event == "RESUMED":
            self._listening("Listening: DMs to your bot, @mentions, and every channel it can see.")
        elif event == "GUILD_CREATE":
            server = str(d.get("name") or "your server")
            self._servers[str(d.get("id"))] = server
            for c in [*(d.get("channels") or []), *(d.get("threads") or [])]:
                self._channels[str(c.get("id"))] = (str(c.get("name") or "?"), server)
        elif event in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE") and d.get("guild_id"):
            self._channels[str(d.get("id"))] = (str(d.get("name") or "?"), self._servers.get(str(d["guild_id"]), "your server"))
        elif event == "MESSAGE_CREATE":
            self._message(d)

    def _message(self, d: dict[str, Any]) -> None:
        author = d.get("author") or {}
        if not author or author.get("bot") or str(author.get("id")) == self.bot_id:
            return  # the bot's own posts (Aura's sends), and other bots
        if d.get("type", 0) not in TEXT_MESSAGES:
            return
        if d.get("guild_id"):
            name, server = self._channels.get(str(d.get("channel_id")), ("", self._servers.get(str(d["guild_id"]), "your server")))
            place = f"#{name} in {server}" if name else f"a channel in {server}"
            mentioned = any(str(m.get("id")) == self.bot_id for m in d.get("mentions") or [])
            kind = "mention" if mentioned else "channel"
        else:
            place, kind = "a direct message", "dm"
        who = (d.get("member") or {}).get("nick") or author.get("global_name") or author.get("username") or "someone"
        text = str(d.get("content") or "")
        if self.bot_id:
            text = re.sub(rf"<@!?{self.bot_id}>", f"@{self.bot_name}", text)
        files = len(d.get("attachments") or [])
        if files:
            text += f" ({files} attachment{'s' if files != 1 else ''})"
        self.inbox.add("Discord", place, str(who), text, kind)


# ── Slack: Socket Mode, as the app's Aura bot user ──────────────────────────────────────────

SLACK_API = "https://slack.com/api/"
SLACK_KEPT = (None, "thread_broadcast", "file_share")  # message subtypes worth a notification
_LIVE_ERRORS = {
    "invalid_auth": "Slack says that token isn't valid. Copy it again from your Slack app's page.",
    "not_authed": "Slack says that token isn't valid. Copy it again from your Slack app's page.",
    "token_revoked": "That token was revoked. Make a new one on your Slack app's page.",
    "not_allowed_token_type": "That's the wrong kind of token: the bot token starts with xoxb-, the app-level "
                              "token with xapp-.",
    "missing_scope": "The app-level token needs the connections:write scope. Generate a new one with it.",
    "token_expired": "That Access Token has expired (rotating tokens last 12 hours). Paste its Refresh Token and "
                     "your app's Client Secret so Aura can renew it.",
    "invalid_refresh_token": "Slack didn't accept the Refresh Token. Paste the whole Refresh Token that was shown "
                             "with this Access Token (each works only once). If you no longer have it, click Generate "
                             "Token and Scopes again and paste the new pair.",
    "bad_client_secret": "That Client Secret isn't this app's. Copy it from the app's Basic Information page.",
    "invalid_client_id": "The Client ID Aura has isn't this app's. Sign in to Slack with this app's Client ID first "
                         "(step 2 above).",
}  # fmt: skip


def explain_live(error: Any) -> str:
    code = str(error or "unknown error")
    return _LIVE_ERRORS.get(code, f"{code}. Check that Socket Mode is on: paste Aura's manifest into the app again.")


class SlackSocket(Listener):
    source = "Slack"

    def __init__(self, slack: Any, inbox: Inbox, connect: Callable[[str], Any] | None = None):
        super().__init__(inbox, connect)
        self.slack = slack
        self.chat: Any = None  # remote.RemoteChat: the owner talking to Aura from Slack (when switched on)
        self._names: dict[str, str] = {}
        self._places: dict[str, str] = {}
        self._seen: deque[tuple[Any, Any]] = deque(maxlen=500)  # an @mention arrives twice: message + app_mention
        slack.listener = self

    def ready(self) -> bool:
        s = self.slack.secrets
        return bool(s.get("slack_bot_token") and s.get("slack_app_token") and self.slack._tokens())

    def _bot_user(self) -> str:
        return str(self.slack.bot_profile().get("user_id") or "")

    def _open(self, app_token: str) -> dict[str, Any]:
        r = self.slack.http.post(SLACK_API + "apps.connections.open", headers={"Authorization": f"Bearer {app_token}"})
        return r.json() if r.content else {"ok": False, "error": f"HTTP {r.status_code}"}

    def _session(self, stop: threading.Event) -> None:
        from aura.connections import ConnectionsError

        if not self.slack.secrets.get("slack_app_token") or not self.slack.secrets.get("slack_bot_token"):
            raise Fatal("Live messages aren't set up.")
        try:
            body = self._open(self.slack.app_token())  # a rotating token is renewed first if it's due
            if body.get("error") in ("token_expired", "invalid_auth") and self.slack.rotating:
                body = self._open(self.slack.renew_app_token())  # expired early (e.g. the Mac slept): once
        except ConnectionsError as e:
            raise Fatal(str(e)) from e
        if not body.get("ok") or not body.get("url"):
            if body.get("error") in _LIVE_ERRORS:
                raise Fatal(explain_live(body.get("error")))
            raise ConnectionError(f"Slack: {body.get('error')}")
        ws = self._ws = self.connect(str(body["url"]))
        try:
            while not stop.is_set():
                try:
                    msg = _recv_json(ws, 30)
                except TimeoutError:
                    continue  # quiet; the WebSocket's own pings keep it open
                if msg.get("envelope_id"):
                    ws.send(json.dumps({"envelope_id": msg["envelope_id"]}))  # acknowledge first, or Slack retries
                kind = msg.get("type")
                if kind == "hello":
                    from aura.connections.slack import app_id, two_apps

                    socket_app = app_id((msg.get("connection_info") or {}).get("app_id"))
                    bot_app = self.slack.bot_profile().get("app_id")
                    if bot_app and socket_app and bot_app != socket_app:
                        raise Fatal(two_apps(bot_app, socket_app))  # the events would be for another app's bot
                    self._listening("Listening: DMs to Aura, @Aura mentions, and the channels you've invited it to.")
                elif kind == "disconnect":
                    if msg.get("reason") == "link_disabled":
                        raise Fatal("Socket Mode was turned off in your Slack app. Paste Aura's manifest into the "
                                    "app again, then press Try Again.")  # fmt: skip
                    return  # a refresh: reconnect with a new URL
                elif kind == "events_api":
                    self._event((msg.get("payload") or {}).get("event") or {})
        finally:
            self._ws = None
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _event(self, e: dict[str, Any]) -> None:
        if e.get("type") not in ("message", "app_mention") or e.get("subtype") not in SLACK_KEPT:
            return  # edits, deletions, joins, bot posts
        bot_user = self._bot_user()
        user = e.get("user")
        if e.get("bot_id") or not user or user == bot_user:
            return
        key = (e.get("channel"), e.get("ts"))
        if key in self._seen:
            return
        self._seen.append(key)
        raw = str(e.get("text") or "")
        if self.slack.sent_recently(e.get("channel"), user, html.unescape(raw)):
            return  # what Aura itself just posted as the user
        if e.get("channel_type") == "im":
            place, kind = "a direct message", "dm"
        else:
            place = "a group DM" if e.get("channel_type") == "mpim" else self._place(str(e.get("channel")))
            kind = "mention" if e.get("type") == "app_mention" or (bot_user and f"<@{bot_user}" in raw) else "channel"
        files = len(e.get("files") or [])
        text = self._plain(raw, bot_user) + (f" ({files} file{'s' if files != 1 else ''})" if files else "")
        ask = self._ask(e, user, place, kind, text)
        if ask is not None:  # the owner talking to Aura: a turn in the Mac conversation, answered in Slack
            self.chat.submit(ask)  # the Mac panel shows it from the mirror feed, not as an inbox card
            return
        self.inbox.add("Slack", place, self._name(str(user)), text, kind)

    def _ask(self, e: dict[str, Any], user: str, place: str, kind: str, text: str) -> Any:
        """The owner's message to Aura (a DM, an @mention, or one in their chat channel), when the
        user has switched Slack chat on. Anyone else's message is never an instruction."""
        from aura.connections.remote import Ask

        if self.chat is None or user != (self.slack._tokens() or {}).get("user_id"):
            return None
        settings = self.chat.settings()
        in_chat_channel = place == f"#{settings['channel']}"
        if not settings["enabled"] or not (kind in ("dm", "mention") or in_chat_channel):
            return None
        request = re.sub(r"^@Aura[\s,:]*", "", text).strip() or text
        thread = e.get("thread_ts") or (e.get("ts") if kind == "mention" and not in_chat_channel else None)
        return Ask(channel=str(e.get("channel")), ts=str(e.get("ts")), thread_ts=thread, text=request, place=place)

    def _get(self, method: str, **params: Any) -> dict[str, Any]:
        token = self.slack.secrets.get("slack_bot_token")
        try:
            r = self.slack.http.get(SLACK_API + method, params=params, headers={"Authorization": f"Bearer {token}"})
            body = r.json() if r.content else {}
        except (httpx.HTTPError, ValueError):
            return {}
        return body if isinstance(body, dict) and body.get("ok") else {}

    def _name(self, user: str) -> str:
        if user not in self._names:
            u = self._get("users.info", user=user).get("user") or {}
            profile = u.get("profile") or {}
            self._names[user] = profile.get("display_name") or u.get("real_name") or u.get("name") or user
        return self._names[user]

    def _place(self, channel: str) -> str:
        if channel not in self._places:
            name = (self._get("conversations.info", channel=channel).get("channel") or {}).get("name")
            self._places[channel] = f"#{name}" if name else "a channel"
        return self._places[channel]

    def _plain(self, text: str, bot_user: str) -> str:
        """Slack's markup as plain text: <@U1> → @name, <#C1|general> → #general, <url|label>."""

        def swap(m: re.Match[str]) -> str:
            inner = m.group(1)
            target, _, label = inner.partition("|")
            if target.startswith("@"):
                uid = target[1:]
                return "@Aura" if uid == bot_user else f"@{label or self._name(uid)}"
            if target.startswith("#"):
                return f"#{label or 'channel'}"
            if target.startswith("!"):
                return f"@{target[1:].split('^')[0]}"
            return f"{label} ({target})" if label else target

        return html.unescape(re.sub(r"<([^<>]+)>", swap, text))


# ── all of them ─────────────────────────────────────────────────────────────────────────────


class LiveMessages:
    """The listeners, started with the server (never in tests) and kept in step with the
    connections: a listener runs exactly when what it needs is set up."""

    def __init__(self, inbox: Inbox | None = None, *listeners: Listener):
        self.inbox = inbox or Inbox()
        self.listeners = {listener.source.lower(): listener for listener in listeners}
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._started = True
        self.sync()

    def sync(self) -> None:
        if not self._started:
            return
        with self._lock:
            for listener in self.listeners.values():
                if listener.ready() and not listener.running:
                    listener.start()
                elif not listener.ready() and listener.running:
                    listener.stop()

    def restart(self, connection_id: str) -> None:
        """New tokens: drop the old connection and session, then connect again."""
        listener = self.listeners.get(connection_id)
        if listener is not None:
            listener.stop()
        self.sync()

    def stop(self) -> None:
        self._started = False
        for listener in self.listeners.values():
            listener.stop()
