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
import sys
import threading
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
        if method in ("POST", "PUT") and content_type != "application/json":
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


class ApprovalsIn(BaseModel):
    approvals: list[bool]


def create_app(aura: Aura, token: str, keeper: Keeper | None = None) -> FastAPI:
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"token must be at least {MIN_TOKEN_CHARS} characters")
    keeper = keeper or ModelKeeper(aura.settings.base_url, aura.settings.model)
    app = FastAPI(title="Aura Server", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, token=token)
    turn = threading.Lock()  # one turn at a time: Aura has a single user and conversation
    app.state.turn = turn

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
        try:
            events = aura.resume(body.approvals)
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

    def persona_state() -> dict:
        persona = identity.load()
        return {**persona.public(), "history": identity.history_count(), "error": identity.error}

    @app.get("/v1/settings")
    def settings() -> dict:
        return {"voice": asdict(voice.load()), "persona": persona_state(), "voices_registered": len(voice.catalog)}

    @app.post("/v1/voices")
    def register_voices(body: VoicesIn) -> dict:
        return {"registered": voice.register([v.model_dump() for v in body.voices])}

    @app.put("/v1/voice")
    def set_voice(body: VoiceIn) -> Response:
        try:
            profile = voice.update(by="user", **body.model_dump())
        except VoiceError as e:
            return _error(422, str(e))
        return JSONResponse(asdict(profile))

    @app.get("/v1/persona")
    def get_persona() -> dict:
        return persona_state()

    @app.post("/v1/persona/reset")
    def reset_persona() -> dict:
        identity.reset(by="user")
        return persona_state()

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
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(aura, token),
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

    log.info("listening on 127.0.0.1:%d", sock.getsockname()[1])
    print(f"AURA_READY port={sock.getsockname()[1]}", flush=True)
    server.run(sockets=[sock])
