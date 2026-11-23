"""Aura Server: Aura's brain over local HTTP + Server-Sent Events, for the macOS app.

localhost is not a trust boundary (any web page can send requests to 127.0.0.1), so every
request must carry the per-launch token, address 127.0.0.1/localhost in its Host header (blocks
DNS rebinding), and POST JSON (forces a CORS preflight, which always fails: no CORS headers).

The app runs `uv run aura-server` and writes the token as the first line of stdin; the server
exits when stdin closes, i.e. when the app quits or crashes. Manual runs: set AURA_SERVER_TOKEN.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import anyio
import httpx
import openai
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from aura.brain import Aura, Changed, Event, Status, Text
from aura.config import Settings
from aura.governor.continuous import DEEP
from aura.developer.workflow import DeveloperError
from aura.governor.workshop import WorkshopError
from aura.connections import ConnectionsError
from aura.secrets import KEYS, SecretError
from aura.skills import SkillError
from aura.speech import Kokoro, SpeechUnavailable
from aura.tools.sandbox import find_docker
from aura.voice import VoiceError

KEEP_ALIVE = "30m"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MIN_TOKEN_CHARS = 32
log = logging.getLogger("aura.server")


# ── Smart-Warm ──────────────────────────────────────────────────────────────────────────────


class Keeper(Protocol):
    def loaded(self) -> bool | None: ...
    def warm_soon(self) -> None: ...


class ModelKeeper:
    """Keeps the model loaded while the user is around and lets it unload after 30 idle minutes.

    Uses Ollama's native API beside the OpenAI-compatible base URL. Every call to `warm`
    (re)starts the 30-minute timer. With a non-Ollama backend, every call is a harmless no-op."""

    def __init__(self, base_url: str, model: str, keep_alive: str = KEEP_ALIVE):
        root = base_url.rstrip("/")
        self.url = root.removesuffix("/v1")
        self.model = model
        self.keep_alive = keep_alive

    def loaded(self) -> bool | None:
        """Is the model in memory? None if the backend can't tell us (not Ollama, or down)."""
        try:
            r = httpx.get(f"{self.url}/api/ps", timeout=2)
            r.raise_for_status()
            names = [(m.get("name"), m.get("model")) for m in r.json().get("models", [])]
            return any(self.model in pair for pair in names)
        except (httpx.HTTPError, ValueError):
            return None

    def warm(self) -> bool:
        """Load the model if needed and restart its keep-alive timer."""
        try:
            r = httpx.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "keep_alive": self.keep_alive},
                timeout=180,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def warm_soon(self) -> None:
        threading.Thread(target=self.warm, daemon=True).start()


# ── security ────────────────────────────────────────────────────────────────────────────────


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class Guard:
    """ASGI middleware: Host check, bearer token, JSON-only POSTs, before any route runs."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    def problem(self, method: str, headers: dict[str, str]) -> JSONResponse | None:
        host = headers.get("host", "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return _error(421, "Requests must be addressed to 127.0.0.1.")
        if not hmac.compare_digest(headers.get("authorization", "").encode(), self.expected):
            return _error(401, "Missing or invalid token.")
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if method in ("POST", "PUT", "PATCH") and content_type != "application/json":
            return _error(415, "Send JSON (Content-Type: application/json).")
        return None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            if (response := self.problem(scope["method"], headers)) is not None:
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ── the streaming protocol ──────────────────────────────────────────────────────────────────


def sse(kind: str, data: dict | None = None) -> str:
    """One event: exactly one `event:` line and one `data:` line (JSON keeps it on one line)."""
    return f"event: {kind}\ndata: {json.dumps(data or {}, ensure_ascii=False, default=str)}\n\n"


def encode(ev: Event) -> str:
    if isinstance(ev, Text):
        return sse("text", {"text": ev.text})
    if isinstance(ev, Status):
        return sse("status", {"text": ev.text})
    if isinstance(ev, Changed):
        return sse(ev.kind, ev.data)  # "voice" or "persona"
    return sse("approval", {"actions": [asdict(a) for a in ev.actions]})


_END = object()


def stream_turn(
    events: Iterator[Event], turn: threading.Lock, keeper: Keeper, check_cold: bool
) -> StreamingResponse:
    """Stream a turn's events as SSE. Holds `turn` until the stream ends or the client leaves."""

    async def body() -> AsyncIterator[str]:
        released = False
        try:
            yield sse("thinking")
            if check_cold and await anyio.to_thread.run_sync(keeper.loaded) is False:
                yield sse("waking")
            try:
                # The agent loop is synchronous; step it in a worker thread, one event at a time.
                while (ev := await anyio.to_thread.run_sync(next, events, _END)) is not _END:
                    yield encode(ev)
            except openai.APIConnectionError:
                yield sse("error", {"message": "Aura can't reach its language model. Make sure Ollama is running, then try again."})
            except Exception as e:
                log.exception("turn failed: %s", e)  # the detail stays in server.log
                yield sse("error", {"message": "Something went wrong while Aura was working. Please try again."})
            turn.release()
            released = True
            yield sse("done")
        finally:
            if not released:  # the client went away mid-turn (e.g. Stop)
                try:
                    events.close()  # type: ignore[attr-defined]  # stop the agent run
                except (AttributeError, ValueError):
                    pass
                turn.release()
            keeper.warm_soon()  # the turn used the model: restart its 30-minute timer

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── the API ─────────────────────────────────────────────────────────────────────────────────


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class VoiceIn(BaseModel):
    voice: str | None = None
    speed: float | None = None
    pitch: float | None = None
    volume: float | None = None
    speak: bool | None = None


class VoiceInfo(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    language: str = Field(default="", max_length=20)
    quality: str = "default"
    gender: str = "unknown"
    novelty: bool = False


class VoicesIn(BaseModel):
    voices: list[VoiceInfo] = Field(max_length=1000)


class SpeechIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    voice: str = Field(min_length=1, max_length=100)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class ProposeIn(BaseModel):
    skill: str = Field(min_length=1, max_length=40)


class DecisionIn(BaseModel):
    approve: bool


class ToggleIn(BaseModel):
    enabled: bool


class EditedApproval(BaseModel):
    """Approve with edits (the Specification Approval card): only fields the tool allows."""

    approve: bool
    args: dict[str, str] = Field(max_length=4)


class ApprovalsIn(BaseModel):
    approvals: list[bool | EditedApproval]


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=512)


class CredentialsIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(default="", max_length=300)  # Slack (PKCE) has none


class ConnectIn(BaseModel):
    services: list[str] = Field(min_length=1, max_length=10)


class ChatSwitchIn(BaseModel):
    enabled: bool
    channel: str = Field(default="aura-chat", max_length=80)


class LiveIn(BaseModel):
    bot_token: str = Field(min_length=1, max_length=300)
    app_token: str = Field(min_length=1, max_length=300)
    refresh_token: str = Field(default="", max_length=300)  # a rotating app-level token's (xoxe.xapp-…)
    client_secret: str = Field(default="", max_length=300)


class BuildIn(BaseModel):
    skill: str = Field(min_length=2, max_length=40)
    specification: str = Field(min_length=1, max_length=12_000)
    engine: str = "claude"
    improve: bool = False


def create_app(aura: Aura, token: str, keeper: Keeper | None = None, voices: Kokoro | None = None) -> FastAPI:
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"token must be at least {MIN_TOKEN_CHARS} characters")
    keeper = keeper or ModelKeeper(aura.settings.base_url, aura.settings.model)
    app = FastAPI(title="Aura Server", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, token=token)
    turn = threading.Lock()  # one turn at a time: Aura has a single user and conversation
    app.state.turn = turn
    app.state.last_active = 0.0  # time.monotonic() of the last chat or approval

    def idle(quiet_for: float = 300) -> bool:
        """Low activity: no turn running, no audit in progress, and nothing asked for a while."""
        return (not turn.locked() and time.monotonic() - app.state.last_active >= quiet_for
                and not any(t.stage in ("staging", "auditing") for t in aura.toolbox.workshop.tasks.values()))  # fmt: skip

    app.state.idle = idle

    def reload_between_turns() -> None:
        with turn:  # waits for a running turn to end (a flagged skill already refuses to run)
            aura.reload_tools()

    aura.toolbox.continuous.on_change = reload_between_turns

    def busy() -> JSONResponse:
        return _error(409, "Aura is busy with another turn.")

    @app.get("/v1/health")
    def health() -> dict:
        s, tb = aura.settings, aura.toolbox
        return {
            "status": "ok",
            "model": s.model,
            "model_loaded": keeper.loaded(),
            "search_mode": tb.search.mode,
            "vault": str(tb.vault.root),
            "busy": turn.locked(),
            "awaiting_approval": aura.awaiting_approval,
        }

    @app.post("/v1/chat")
    def chat(body: ChatIn) -> Response:
        if not turn.acquire(blocking=False):
            return busy()
        app.state.last_active = time.monotonic()
        try:
            events = aura.stream(body.text)
        except RuntimeError as e:  # an approval is pending
            turn.release()
            return _error(409, str(e))
        return stream_turn(events, turn, keeper, check_cold=True)

    @app.post("/v1/approvals")
    def approvals(body: ApprovalsIn) -> Response:
        if not turn.acquire(blocking=False):
            return busy()
        app.state.last_active = time.monotonic()
        try:
            events = aura.resume([a.model_dump() if isinstance(a, EditedApproval) else a for a in body.approvals])
        except RuntimeError as e:  # nothing pending
            turn.release()
            return _error(409, str(e))
        except ValueError as e:  # wrong number of decisions; the approval stays pending
            turn.release()
            return _error(422, str(e))
        return stream_turn(events, turn, keeper, check_cold=False)

    @app.post("/v1/conversation/new")
    def new_conversation() -> Response:
        if not turn.acquire(blocking=False):
            return busy()
        try:
            aura.new_conversation()
        finally:
            turn.release()
        return Response(status_code=204)

    # ── voice and persona (the user acting directly: no approval needed) ──────────────────────

    voice, identity = aura.toolbox.voice, aura.toolbox.identity
    kokoro = voices or Kokoro()
    app.state.kokoro = kokoro

    def persona_state() -> dict:
        persona = identity.load()
        return {**persona.public(), "history": identity.history_count(), "error": identity.error}

    @app.get("/v1/settings")
    def settings() -> dict:
        return {"voice": asdict(voice.load()), "persona": persona_state(),
                "voices_registered": len(voice.system), "neural_voices": len(voice.neural)}

    @app.post("/v1/voices")
    def register_voices(body: VoicesIn) -> dict:
        return {"registered": voice.register([v.model_dump() for v in body.voices])}

    @app.get("/v1/voices")
    def list_voices() -> list[dict]:
        """Every voice Aura can speak with: its own neural voices first, then macOS voices."""
        return voice.listing()

    @app.post("/v1/speech")
    async def speak(body: SpeechIn) -> Response:
        """One sentence of audio in one of Aura's neural voices (WAV)."""
        if not kokoro.knows(body.voice):
            return _error(422, f"'{body.voice}' is not one of Aura's neural voices.")
        try:
            wav = await kokoro.synthesize_async(body.text, body.voice, body.speed)
        except SpeechUnavailable as e:
            return _error(503, str(e))
        return Response(wav, media_type="audio/wav")

    @app.put("/v1/voice")
    def set_voice(body: VoiceIn) -> Response:
        try:
            profile = voice.update(by="user", **body.model_dump())
        except VoiceError as e:
            return _error(422, str(e))
        if profile.speak and profile.voice:
            kokoro.warm(profile.voice)  # spoken replies just got turned on or re-voiced
        return JSONResponse(asdict(profile))

    @app.get("/v1/persona")
    def get_persona() -> dict:
        return persona_state()

    @app.post("/v1/persona/reset")
    def reset_persona() -> dict:
        identity.reset(by="user")
        return persona_state()

    # ── the Workshop: skills, audits, decisions (Sprint 5) ──────────────────────────────────

    workshop, skills = aura.toolbox.workshop, aura.toolbox.skills

    @app.get("/v1/skills")
    def get_skills() -> dict:
        return {"installed": [s.public() for s in skills.scan()], "inbox": workshop.inbox()}

    @app.patch("/v1/skills/{name}")
    def toggle_skill(name: str, body: ToggleIn) -> Response:
        if not turn.acquire(blocking=False):
            return busy()
        try:
            if not skills.set_enabled(name, body.enabled):
                return _error(404, f"'{name}' is not an installed skill.")
            aura.reload_tools()
        except SkillError as e:  # flagged by the Continuous Audit: red blocks, no override
            return _error(409, str(e))
        finally:
            turn.release()
        return JSONResponse(next(s.public() for s in skills.scan() if s.name == name))

    @app.delete("/v1/skills/{name}")
    def uninstall_skill(name: str) -> Response:
        if not any(s.name == name for s in skills.scan()):
            return _error(404, f"'{name}' is not an installed skill.")
        if not turn.acquire(blocking=False):  # uninstalling swaps the agent's tools between turns
            return busy()
        try:
            workshop.uninstall(name)
        except WorkshopError as e:
            return _error(409, str(e))
        finally:
            turn.release()
        return Response(status_code=204)

    @app.post("/v1/skills/{name}/acknowledge")
    def acknowledge(name: str) -> Response:
        """Keep a flagged skill (it stays disabled); stop alerting about it."""
        if not skills.acknowledge(name):
            return _error(404, f"'{name}' has no alert.")
        return JSONResponse(next(s.public() for s in skills.scan() if s.name == name))

    # ── keys (the Keychain) and the Developer (Sprint 6) ───────────────────────────────────────

    secrets, developer = aura.toolbox.secrets, aura.toolbox.developer

    @app.get("/v1/secrets")
    def list_secrets() -> list[dict]:
        return secrets.listing()  # names and masked hints only, never values

    def is_key(name: str) -> bool:  # Settings → Keys only; connections have their own endpoints
        return name in KEYS and KEYS[name].group == "keys"

    @app.put("/v1/secrets/{name}")
    def set_secret(name: str, body: SecretIn) -> Response:
        if not is_key(name):
            return _error(404, f"unknown key '{name}'")
        try:
            secrets.set(name, body.value)
        except SecretError as e:
            return _error(422, str(e))
        if name == "tavily_api_key":
            aura.toolbox.search.set_key(secrets.get(name))
        return JSONResponse(next(s for s in secrets.listing() if s["name"] == name))

    @app.delete("/v1/secrets/{name}")
    def delete_secret(name: str) -> Response:
        if not is_key(name):
            return _error(404, f"unknown key '{name}'")
        try:
            secrets.delete(name)
        except SecretError as e:
            return _error(404, str(e))
        if name == "tavily_api_key":
            aura.toolbox.search.set_key(aura.settings.tavily_api_key)  # back to .env's, if any
        return Response(status_code=204)

    # ── connections (Settings → Connections): credentials, sign-in, check. No agent tool here. ──

    connections = aura.toolbox.connections
    live = aura.toolbox.live

    # Talking to Aura from Slack (remote.py): the owner's messages, answered by a second Aura with
    # its own conversation, taking turns with the Mac panel under the same lock.
    from aura.brain import Aura as _Aura
    from aura.connections.remote import RemoteChat

    slack_connection = connections.all.get("slack")
    slack_listener = live.listeners.get("slack")
    remote = RemoteChat(
        make_aura=lambda: _Aura(settings=aura.settings, model=aura.llm, toolbox=aura.toolbox, primary=False),
        turn=turn, post=lambda channel, text, thread: slack_connection.post_as_bot(channel, text, thread),
        inbox=live.inbox, settings_path=aura.settings.data_dir / "slack_chat.json")  # fmt: skip
    app.state.remote = remote
    if slack_listener is not None:
        slack_listener.chat = remote

    def connection_call(connection_id: str, action, restart: bool = False) -> Response:  # noqa: ANN001
        try:
            result = action(connections.get(connection_id))
        except ConnectionsError as e:
            log.info("connection %s refused: %s", connection_id, e)  # reasons only: never a token
            live.sync()
            return _error(404 if "no connection called" in str(e) else 409, str(e))
        except SecretError as e:
            log.info("connection %s refused: %s", connection_id, e)
            return _error(422, str(e))
        # Live messages follow the connection: new tokens reconnect, a disconnect stops listening.
        live.restart(connection_id) if restart else live.sync()
        if isinstance(result, dict) and "live" in result:
            result = {**result, "live": connections.get(connection_id).status()["live"]}  # as it is now
        return JSONResponse(result)

    @app.get("/v1/connections")
    def list_connections() -> list[dict]:
        return connections.public()  # states, accounts and masked hints: never a secret or a token

    @app.get("/v1/connections/{connection_id}")
    def get_connection(connection_id: str) -> Response:
        return connection_call(connection_id, lambda c: c.status())

    @app.put("/v1/connections/{connection_id}/credentials")
    def set_credentials(connection_id: str, body: CredentialsIn) -> Response:
        def save(c):  # noqa: ANN001, ANN202
            c.set_credentials(body.client_id, body.client_secret)
            return c.status()

        return connection_call(connection_id, save, restart=True)

    @app.put("/v1/connections/{connection_id}/live")
    def set_live(connection_id: str, body: LiveIn) -> Response:
        """Slack's live messages: the bot token and the app-level token, checked with Slack."""

        def save(c):  # noqa: ANN001, ANN202
            if not hasattr(c, "set_live"):
                raise ConnectionsError(f"{c.label}'s live messages need no extra tokens.")
            c.set_live(body.bot_token, body.app_token, body.refresh_token, body.client_secret)
            return c.status()

        return connection_call(connection_id, save, restart=True)

    @app.put("/v1/connections/{connection_id}/chat")
    def set_chat(connection_id: str, body: ChatSwitchIn) -> Response:
        """Switch talking to Aura from Slack on or off (and the channel where every message of the
        owner's is for Aura). Switching on needs the Aura bot to be able to answer (chat:write)."""

        def switch(c):  # noqa: ANN001, ANN202
            if c.id != "slack":
                raise ConnectionsError(f"Talking to Aura works from Slack only (for now), not {c.label}.")
            if body.enabled and not c.live_configured:
                raise ConnectionsError("Set up live messages first: Aura hears you through them.")
            if body.enabled and not c.bot_can_post():
                raise ConnectionsError("The Aura bot can't post yet: Slack hasn't given it chat:write. Paste Aura's "
                                       "manifest into your Slack app if you haven't, then open Install App and click "
                                       "Reinstall to (your workspace's name) → Allow. Slack only gives the bot a new "
                                       "permission when the app is reinstalled. Then switch this on.")  # fmt: skip
            try:
                remote.configure(body.enabled, body.channel)
            except ValueError as e:
                raise ConnectionsError(str(e)) from e
            aura.toolbox.audit.record("slack_chat_switched", enabled=body.enabled, channel=remote.settings()["channel"])
            return c.status()

        return connection_call(connection_id, switch)

    @app.delete("/v1/connections/{connection_id}/live")
    def remove_live(connection_id: str) -> Response:
        def remove(c):  # noqa: ANN001, ANN202
            if hasattr(c, "remove_live"):
                c.remove_live()
            return c.status()

        return connection_call(connection_id, remove)

    @app.get("/v1/messages")
    def messages(after: int = 0, wait: float = 0) -> dict:
        """Live messages newer than `after`. With `wait` (seconds, up to 25) it waits for one: the
        app long-polls this to show each message as it arrives."""
        items = live.inbox.wait(max(0, after), min(max(wait, 0.0), 25.0))
        return {"messages": [m.public() for m in items], "latest": live.inbox.latest}

    @app.delete("/v1/connections/{connection_id}/credentials")
    def remove_credentials(connection_id: str) -> Response:
        def remove(c):  # noqa: ANN001, ANN202
            c.remove_credentials()
            return c.status()

        return connection_call(connection_id, remove)

    @app.post("/v1/connections/{connection_id}/connect")
    def connect(connection_id: str, body: ConnectIn) -> Response:
        """Starts a sign-in; the app opens the returned URL in the user's browser."""
        return connection_call(connection_id, lambda c: {"auth_url": c.begin(body.services), **c.status()})

    @app.post("/v1/connections/{connection_id}/check")
    def check_connection(connection_id: str) -> Response:
        return connection_call(connection_id, lambda c: c.check())

    @app.post("/v1/connections/{connection_id}/disconnect")
    def disconnect(connection_id: str) -> Response:
        def drop(c):  # noqa: ANN001, ANN202
            c.disconnect()
            return c.status()

        return connection_call(connection_id, drop)

    @app.get("/v1/developer")
    def developer_state() -> dict:
        builds = sorted(developer.builds.values(), key=lambda b: b.created, reverse=True)
        return {"engines": developer.engines_status(), "builds": [b.public(workshop) for b in builds],
                "budget_usd": developer.budget_usd, "repair_rounds": developer.repair_rounds,
                "timeout_minutes": int(developer.timeout // 60)}  # fmt: skip

    @app.post("/v1/builds", status_code=202)
    def start_build(body: BuildIn) -> Response:
        """From the Workshop's New Skill sheet: the user's click, with the specification and the
        limits in view, is the approval."""
        try:
            build = developer.start(body.skill, body.specification, body.engine, body.improve)
        except DeveloperError as e:
            return _error(409, str(e))
        aura.toolbox.audit.record("approval", tool="develop_skill", args={"name": body.skill, "engine": body.engine},
                                  decision="approved in the Workshop")  # fmt: skip
        return JSONResponse(build.public(workshop), status_code=202)

    @app.get("/v1/builds/{build_id}")
    def get_build(build_id: str) -> Response:
        build = developer.builds.get(build_id)
        if build is None:
            return _error(404, "No such build.")
        return JSONResponse(build.public(workshop))

    @app.post("/v1/builds/{build_id}/stop")
    def stop_build(build_id: str) -> Response:
        try:
            return JSONResponse(developer.stop(build_id).public(workshop))
        except DeveloperError as e:
            return _error(404, str(e))

    @app.get("/v1/alerts")
    def alerts() -> dict:
        """Cheap enough for the menu bar to poll: Continuous Audit alerts and its clock, and the
        Developer's builds that are running or ready for the user's review."""
        builds = [b.public(workshop) for b in developer.builds.values()]
        current = [{k: b[k] for k in ("id", "skill", "stage", "message")} for b in builds
                   if b["stage"] in ("queued", "developing", "auditing", "repairing", "ready")]  # fmt: skip
        return {"alerts": aura.toolbox.continuous.alerts(), "continuous_audit": aura.toolbox.continuous.state(),
                "builds": current}  # fmt: skip

    @app.post("/v1/audit", status_code=202)
    def audit_now() -> Response:
        """Run the Continuous Audit's deep pass now (the Workshop's "Check now")."""
        continuous = aura.toolbox.continuous
        if continuous.state()["running"]:
            return _error(409, "A check is already running.")
        threading.Thread(target=continuous.run, args=(DEEP,), daemon=True).start()
        return JSONResponse({**continuous.state(), "running": True}, status_code=202)

    @app.post("/v1/tasks", status_code=202)
    def propose(body: ProposeIn) -> Response:
        try:
            task = workshop.propose(body.skill)
        except WorkshopError as e:
            return _error(409, str(e))
        return JSONResponse(task.public(), status_code=202)

    @app.get("/v1/tasks")
    def tasks() -> list[dict]:
        return [t.public() for t in sorted(workshop.tasks.values(), key=lambda t: t.created, reverse=True)]

    @app.get("/v1/tasks/{task_id}")
    def task(task_id: str) -> Response:
        t = workshop.tasks.get(task_id)
        if t is None:
            return _error(404, "No such task.")
        return JSONResponse({**t.public(), "diff": workshop.diff(task_id)})

    @app.post("/v1/tasks/{task_id}/decision")
    def decide(task_id: str, body: DecisionIn) -> Response:
        if not turn.acquire(blocking=False):  # deploying swaps the agent's tools between turns
            return busy()
        try:
            t = workshop.decide(task_id, body.approve)
        except WorkshopError as e:
            return _error(409, str(e))
        finally:
            turn.release()
        return JSONResponse(t.public())

    @app.get("/v1/status")
    def status() -> dict:
        rss = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()
        try:
            docker = subprocess.run([find_docker(), "info"], capture_output=True, timeout=5).returncode == 0
        except Exception:  # noqa: BLE001
            docker = False
        installed = skills.scan()
        return {
            "model": aura.settings.model,
            "model_loaded": keeper.loaded(),
            "server_memory_mb": round(int(rss or 0) / 1024),
            "docker": docker,
            "skills": {"enabled": sum(s.loaded for s in installed), "installed": len(installed)},
            "tasks_running": sum(t.stage in ("staging", "auditing") for t in workshop.tasks.values()),
            "alerts": aura.toolbox.continuous.alerts(),
            "continuous_audit": aura.toolbox.continuous.state(),
        }

    @app.post("/v1/warmup", status_code=202)
    def warmup() -> dict:
        keeper.warm_soon()
        return {"status": "warming"}

    return app


# ── process entry point ─────────────────────────────────────────────────────────────────────


def _log_to(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("aura").addHandler(handler)
    logging.getLogger("aura").setLevel(logging.INFO)


def main() -> None:
    token = os.environ.get("AURA_SERVER_TOKEN")
    launched_by_app = token is None
    if launched_by_app:
        token = sys.stdin.readline().strip()
    if len(token) < MIN_TOKEN_CHARS:
        sys.exit(
            f"aura-server: needs a token of at least {MIN_TOKEN_CHARS} characters on the first "
            "line of stdin (or in AURA_SERVER_TOKEN)."
        )
    settings = Settings.from_env()
    _log_to(settings.audit_log.parent / "server.log")
    try:
        aura = Aura(settings)
    except OSError as e:
        sys.exit(f"aura-server: couldn't start: {e}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", int(os.environ.get("AURA_SERVER_PORT", "0"))))
    sock.listen(64)  # accept connections (queued) before announcing readiness
    app = create_app(aura, token)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,  # request bodies are private conversation
            lifespan="off",
            timeout_graceful_shutdown=3,
        )
    )
    if launched_by_app:

        def exit_with_app() -> None:
            sys.stdin.read()  # returns at EOF: the app quit or crashed
            log.info("stdin closed; shutting down")
            server.should_exit = True

        threading.Thread(target=exit_with_app, daemon=True).start()

    profile = aura.toolbox.voice.load()
    if profile.speak and profile.voice:
        app.state.kokoro.warm(profile.voice)  # ready before the first reply is spoken
    aura.toolbox.continuous.start(idle=app.state.idle)  # quick pass now; deep pass daily, when idle
    aura.toolbox.live.start()  # Slack and Discord live messages, for whatever is set up
    log.info("listening on 127.0.0.1:%d", sock.getsockname()[1])
    print(f"AURA_READY port={sock.getsockname()[1]}", flush=True)
    server.run(sockets=[sock])
