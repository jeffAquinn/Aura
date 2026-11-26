"""Aura Server: Aura's brain over local HTTP + Server-Sent Events, for the macOS app.

localhost is not a trust boundary (any web page can send requests to 127.0.0.1), so every
request must carry the per-launch token, address 127.0.0.1/localhost in its Host header (blocks
DNS rebinding), and POST JSON (forces a CORS preflight, which always fails: no CORS headers).

The app runs `uv run aura-server` and writes the token as the first line of stdin; the server
exits when stdin closes, i.e. when the app quits or crashes. Manual runs: set AURA_SERVER_TOKEN.
"""

from __future__ import annotations

import asyncio
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
from itertools import chain
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import anyio
import httpx
import openai
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from aura.brain import Aura, Changed, Event, StaleApproval, Status, Text
from aura.agents import AgentError
from aura.lifecycle import Lifecycle, Turn
from aura.masking import PATTERNS_ONLY, Masker
from aura.config import Settings
from aura.model_routing import ROLES, ModelChoice, ModelRouting, SleepMode, infer_role
from aura.governor.continuous import DEEP
from aura.developer.workflow import DeveloperError
from aura.governor.workshop import WorkshopError
from aura.hol import HolError, HolManager
from aura.hol_terminal import HolTerminal, HolTerminalError
from aura.connections import ConnectionsError
from aura.secrets import KEYS, SecretError
from aura.skills import SkillError
from aura.speech import Kokoro, SpeechUnavailable
from aura.tools.sandbox import cleanup_aura_docker, find_docker
from aura.tools import vault_map
from aura.voice import VoiceError

KEEP_ALIVE = "5m"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MIN_TOKEN_CHARS = 32
log = logging.getLogger("aura.server")


# ── Smart-Warm ──────────────────────────────────────────────────────────────────────────────


class Keeper(Protocol):
    def loaded(self) -> bool | None: ...
    def warm_soon(self) -> None: ...


class ModelKeeper:
    """Keeps a model briefly after a real turn, then lets Ollama release its memory.

    Uses Ollama's native API beside the OpenAI-compatible base URL. Aura never calls ``warm`` at
    launch or when its panel opens: a 26B model can reserve most of a small Mac's unified memory.
    Every call after a completed turn restarts the configurable idle timer. With a non-Ollama
    backend, every call is a harmless no-op."""

    def __init__(self, base_url: str, model: str, keep_alive: str | int = KEEP_ALIVE):
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
                # An empty non-streaming request is Ollama's documented pre-load operation.
                json={"model": self.model, "prompt": "", "stream": False, "keep_alive": self.keep_alive},
                timeout=180,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def warm_soon(self) -> None:
        threading.Thread(target=self.warm, daemon=True).start()


class ActiveModelKeeper:
    """Apply Ollama's memory lifecycle only when Aura's selected role is local."""

    def __init__(self, aura: Aura):
        self.aura = aura

    def _local(self) -> ModelKeeper | None:
        choice = self.aura.active_model
        if choice.provider != "ollama":
            return None
        s = self.aura.settings
        return ModelKeeper(s.base_url, choice.model, s.model_keep_alive)

    def loaded(self) -> bool | None:
        local = self._local()
        return local.loaded() if local else None

    def warm_soon(self) -> None:
        local = self._local()
        if local:
            local.warm_soon()


# ── local Ollama model management ────────────────────────────────────────────────────────────


class OllamaModelError(RuntimeError):
    """Ollama cannot safely list or download a local model right now."""


class CloudModelError(RuntimeError):
    """A cloud provider did not return a model catalog Aura can safely use."""


class CloudModelAvailability:
    """Remember a cloud model's temporary account-capacity failure without storing credentials.

    Provider model catalogs say which IDs an account can see; they do not expose account credit,
    subscription token windows, or per-model rate limits.  A real capacity refusal is therefore
    the only trustworthy signal.  Remembering it avoids repeatedly selecting a model that just
    refused a turn, while the short expiry lets Auto recover after the provider's reset window.
    """

    _DEFAULT_RETRY_SECONDS = 60 * 60
    _MAX_RETRY_SECONDS = 12 * 60 * 60

    def __init__(self, settings: Settings):
        self.path = settings.data_dir / "cloud_model_availability.json"
        self._lock = threading.RLock()

    def unavailable_models(self) -> set[str]:
        with self._lock:
            entries = self._load()
            now = time.time()
            active = {model for model, until in entries.items() if until > now}
            if len(active) != len(entries):
                self._save({model: until for model, until in entries.items() if until > now})
            return active

    def record_capacity_refusal(self, choice: ModelChoice, error: Exception) -> int:
        """Mark a model unavailable through the provider's retry window and return its TTL."""
        retry = self._retry_after(error)
        with self._lock:
            entries = self._load()
            entries[choice.model] = time.time() + retry
            self._save(entries)
        return retry

    def _load(self) -> dict[str, float]:
        try:
            raw = json.loads(self.path.read_text())
            models = raw.get("anthropic", {}) if isinstance(raw, dict) else {}
            if not isinstance(models, dict):
                return {}
            return {
                ModelChoice("anthropic", str(model)).validate().model: float(until)
                for model, until in models.items()
                if isinstance(until, (int, float)) and float(until) > 0
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self, entries: dict[str, float]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"anthropic": entries}, sort_keys=True) + "\n")
            temporary.replace(self.path)
        except OSError:
            # The cache is an availability improvement, never a reason to block a chat turn.
            log.warning("could not save cloud model availability", exc_info=True)

    def _retry_after(self, error: Exception) -> int:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", {}) if response is not None else {}
        try:
            value = float(headers.get("retry-after", self._DEFAULT_RETRY_SECONDS))
        except (AttributeError, TypeError, ValueError):
            value = self._DEFAULT_RETRY_SECONDS
        return max(60, min(round(value), self._MAX_RETRY_SECONDS))


def is_cloud_capacity_refusal(error: Exception) -> bool:
    """True only for provider responses that mean the account cannot use a cloud model now."""
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    if status in (402, 429):
        return True
    detail = str(error).lower()
    return any(phrase in detail for phrase in (
        "credit balance", "insufficient credit", "billing", "quota", "rate limit",
        "rate_limit", "usage limit", "spend limit", "reached your limit",
    ))


def available_anthropic_models(api_key: str) -> list[str]:
    """Read the account-visible Claude catalog from Anthropic's documented Models API.

    This is called only by the person pressing Sync in Settings. The API key is sent directly to
    api.anthropic.com over TLS, is never included in a response, and the stored result contains
    only public model identifiers.
    """
    url = "https://api.anthropic.com/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "accept": "application/json"}
    models: set[str] = set()
    after_id: str | None = None
    try:
        # The API is paginated. A small hard limit prevents an unexpected remote response from
        # tying up Aura's local server indefinitely.
        for _ in range(10):
            params: dict[str, str | int] = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            response = httpx.get(url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise CloudModelError("Anthropic returned an unexpected model list.")
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    # Reuse the strict model-name validation before this remote value reaches UI.
                    models.add(ModelChoice("anthropic", row["id"]).validate().model)
            if not isinstance(payload, dict) or not payload.get("has_more"):
                break
            next_id = payload.get("last_id")
            if not isinstance(next_id, str) or not next_id:
                raise CloudModelError("Anthropic returned an incomplete paginated model list.")
            after_id = next_id
        else:
            raise CloudModelError("Anthropic returned too many model-list pages.")
    except (httpx.HTTPError, ValueError, TypeError) as e:
        raise CloudModelError(f"Aura could not sync cloud models from Anthropic: {e}") from e
    if not models:
        raise CloudModelError("Anthropic did not return any usable cloud models for this API key.")
    return sorted(models)


def ollama_root(settings: Settings) -> str:
    """Return the native Ollama endpoint only for an explicitly local configured backend."""
    root = settings.base_url.rstrip("/").removesuffix("/v1")
    parsed = urlsplit(root)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise OllamaModelError("Local model downloads require Aura to use a local Ollama endpoint.")
    return root


def installed_ollama_models(settings: Settings) -> list[str]:
    """Names reported by Ollama; a failure leaves the picker usable with saved preferences."""
    try:
        listed = httpx.get(f"{ollama_root(settings)}/api/tags", timeout=2).json().get("models", [])
        return sorted({str(item.get("name")) for item in listed if item.get("name")})
    except (OllamaModelError, httpx.HTTPError, ValueError, TypeError):
        return []


def usable_ollama_models(settings: Settings) -> set[str]:
    """Installed local models only when the Ollama runtime can answer a readiness probe.

    ``/api/tags`` proves a model is on disk, not that the service can currently accept a turn.
    Automatic routing must distinguish those states so it can fail before opening an SSE turn
    rather than showing a permanently waking composer.
    """
    installed = set(installed_ollama_models(settings))
    if not installed:
        return set()
    try:
        response = httpx.get(f"{ollama_root(settings)}/api/ps", timeout=2)
        response.raise_for_status()
        if not isinstance(response.json(), dict):
            return set()
    except (OllamaModelError, httpx.HTTPError, ValueError, TypeError):
        return set()
    return installed


def pull_ollama_model(settings: Settings, model: str) -> None:
    """Ask the local daemon to fetch a named model. No URL, host path, or shell is accepted."""
    try:
        response = httpx.post(f"{ollama_root(settings)}/api/pull", json={"name": model, "stream": False}, timeout=3600)
        response.raise_for_status()
        payload = response.json()
    except (OllamaModelError, httpx.HTTPError, ValueError) as e:
        raise OllamaModelError(f"Ollama could not download '{model}': {e}") from e
    if not isinstance(payload, dict) or payload.get("error") or payload.get("status") != "success":
        detail = payload.get("error") if isinstance(payload, dict) else "unexpected response"
        raise OllamaModelError(f"Ollama could not download '{model}': {detail or 'download did not finish'}.")


def automatic_model_choice(
    routing: ModelRouting, role: str, installed: set[str], anthropic_key_set: bool,
    unavailable_cloud_models: set[str] | None = None,
) -> tuple[ModelChoice, str | None]:
    """Choose an installed, user-configured model for Aura's automatic picker.

    The requested task role always wins when it can run.  A missing local model falls back only
    to another *configured local* general-purpose role; Aura never turns a local request into a
    cloud request just because a local model is unavailable.  This gives automatic mode a useful
    answer immediately instead of deferring a missing-model failure to a long streaming request.
    """
    requested = routing.choice(role)
    unavailable_cloud_models = unavailable_cloud_models or set()
    if requested.provider == "anthropic" and anthropic_key_set and requested.model not in unavailable_cloud_models:
        return requested, None
    if requested.provider == "ollama" and requested.model in installed:
        return requested, None

    # Chat is the least surprising degradation for a specialised task; Startup is the final
    # local fallback the person deliberately configured.  Do not inspect another specialised
    # role or promote the task to cloud service without an explicit role choice.
    for fallback_role in ("chat", "startup"):
        if fallback_role == role:
            continue
        fallback = routing.choice(fallback_role)
        if fallback.provider == "ollama" and fallback.model in installed:
            if requested.provider == "ollama":
                reason = f"the {role.replace('_', ' ')} model '{requested.model}' is not installed"
            elif not anthropic_key_set:
                reason = f"the {role.replace('_', ' ')} cloud model has no API key"
            else:
                reason = f"the {role.replace('_', ' ')} cloud model recently reached its account limit"
            return fallback, f"Auto selected local model '{fallback.model}' because {reason}."

    if requested.provider == "anthropic":
        if requested.model in unavailable_cloud_models:
            raise ValueError(
                f"Auto mode cannot use the {role.replace('_', ' ')} cloud model '{requested.model}' because it "
                "recently reached its account limit. Choose an installed local model or try again later."
            )
        raise ValueError(
            f"Auto mode cannot use the {role.replace('_', ' ')} cloud model without an Anthropic API key. "
            "Add a key in Settings or choose an installed local model."
        )
    available = ", ".join(sorted(installed)) or "none"
    raise ValueError(
        f"Auto mode cannot use the {role.replace('_', ' ')} model '{requested.model}' because it is not installed. "
        f"Installed local models: {available}. Choose or download a local model in Settings."
    )


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
    return sse("approval", {"actions": [asdict(a) for a in ev.actions], "approval_id": ev.approval_id})


_END = object()


KEEPALIVE = 20.0  # seconds: a comment line while a long tool works (the app gives up after 300 s of silence)


def stream_turn(
    events: Iterator[Event], lifecycle: Lifecycle, turn: Turn, keeper: Keeper, check_cold: bool,
    masker: Masker = PATTERNS_ONLY, selected_model: ModelChoice | None = None,
    cloud_availability: CloudModelAvailability | None = None,
) -> StreamingResponse:
    """Stream a turn's events as SSE. The turn ends (and its lock is released) exactly once: when
    the run ends, when it is stopped (POST /v1/stop: a `stopped` event, then `done`), or when the
    client leaves. In every case the step in progress is allowed to end before the run is closed,
    so no worker is left running behind a released turn."""

    async def body() -> AsyncIterator[str]:
        pending: asyncio.Future | None = None
        state, error = None, None
        texts = masker.stream()  # a secret split across two chunks is still caught
        try:
            if selected_model is not None:
                # The model choice is an ordinary preference, not a secret.  Sending it before
                # `thinking` lets the Mac label an automatic turn immediately, without a poll.
                yield sse("model", selected_model.public())
            yield sse("thinking", {"turn_id": turn.id})
            if check_cold and await anyio.to_thread.run_sync(keeper.loaded) is False:
                yield sse("waking")
            try:
                # The agent loop is synchronous; step it in a worker thread, one event at a time.
                # A step can take minutes (Claude Code at work): keep the connection alive meanwhile.
                while not turn.cancelled:
                    pending = asyncio.ensure_future(anyio.to_thread.run_sync(next, events, _END))
                    while not pending.done():
                        await asyncio.wait({pending}, timeout=KEEPALIVE)
                        if not pending.done():
                            yield ": working\n\n"
                    ev, pending = pending.result(), None
                    if ev is _END:
                        break
                    if isinstance(ev, Text):
                        turn.chars += len(ev.text)
                        if safe := texts.feed(ev.text):
                            yield sse("text", {"text": safe})
                        continue
                    if rest := texts.flush():
                        yield sse("text", {"text": rest})
                    if isinstance(ev, Status):
                        turn.tools += 1
                    yield encode(ev)
                if rest := texts.flush():
                    yield sse("text", {"text": rest})
                if turn.cancelled:
                    state = "stopped"
                    yield sse("stopped", {"turn_id": turn.id, "tools": turn.tools, "partial": bool(turn.tools or turn.chars)})
            except openai.APIConnectionError:
                state, error = "failed", "the language model was unreachable"
                yield sse("error", {"message": "Aura can't reach its language model. Make sure Ollama is running, then try again."})
            except Exception as e:
                log.exception("turn failed: %s", e)  # the detail stays in server.log
                state, error = "failed", f"{type(e).__name__}: {masker.mask(str(e))}"
                if (selected_model is not None and selected_model.provider == "anthropic"
                        and cloud_availability is not None and is_cloud_capacity_refusal(e)):
                    retry = cloud_availability.record_capacity_refusal(selected_model, e)
                    minutes = max(1, round(retry / 60))
                    yield sse("error", {"message": (
                        f"{selected_model.model} has no account capacity right now. Aura will skip it in Auto "
                        f"for about {minutes} minutes and use an available local model instead."
                    )})
                else:
                    yield sse("error", {"message": "Something went wrong while Aura was working. Please try again."})
            await _close(events, pending)
            lifecycle.end(turn, state, error)
            yield sse("done")
        finally:
            if not turn.released.is_set():  # the client went away mid-turn (e.g. Stop): same as a stop
                turn.cancel.set()
                lifecycle.on_cancel()
                with anyio.CancelScope(shield=True):  # let the step end before closing the run
                    await _close(events, pending)
                lifecycle.end(turn, "abandoned")
            keeper.warm_soon()  # the turn used the model: restart its 30-minute timer

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _close(events: Iterator[Event], pending: asyncio.Future | None) -> None:
    """The cleanup boundary: wait for the step in progress, then close the agent run."""
    if pending is not None and not pending.done():
        await asyncio.wait({pending})
    try:
        events.close()  # type: ignore[attr-defined]  # stop the agent run
    except (AttributeError, ValueError, RuntimeError):
        pass


# ── the API ─────────────────────────────────────────────────────────────────────────────────


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    developer: Literal["claude", "codex"] | None = None  # chosen in the chat box: hand this to that Developer
    # A user-selected model overrides role routing for this one Aura chat turn only.
    model: ModelChoiceIn | None = None


class ModelChoiceIn(BaseModel):
    provider: Literal["ollama", "anthropic"]
    model: str = Field(min_length=1, max_length=200)


class ModelRoutingIn(BaseModel):
    roles: dict[str, ModelChoiceIn]


class CodingEngineIn(BaseModel):
    engine: Literal["claude", "codex"]


class LocalModelPullIn(BaseModel):
    """A user-confirmed request to Ollama's local model manager."""
    model: str = Field(min_length=1, max_length=200)


class SleepModeIn(BaseModel):
    enabled: bool = False
    model: str = Field(min_length=1, max_length=200)


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


class AgentCandidateIn(BaseModel):
    name: str = Field(min_length=1, max_length=39)
    purpose: str = Field(min_length=1, max_length=500)
    provenance: str = Field(min_length=1, max_length=1_000)
    intent_delta: str = Field(default="Unmeasured: candidate has not been evaluated.", min_length=1, max_length=1_000)
    memory: str = Field(default="No memory imported.", min_length=1, max_length=1_000)


class AgentLifecycleIn(BaseModel):
    reason: Literal["audit_failure", "suspected_breach", "expiry", "user_retirement", "superseded"]


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
    approval_id: str | None = Field(default=None, max_length=64)  # binds the answer to the card it answers


class StopIn(BaseModel):
    turn_id: str | None = Field(default=None, max_length=64)  # None: whatever turn is running


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


class HolStartIn(BaseModel):
    model: ModelChoiceIn | None = None
    effort: Literal["none", "low", "medium", "high"] | None = None


class HolSavepointIn(BaseModel):
    label: str = Field(default="Before HOL changes", max_length=120)


class HolRestoreIn(BaseModel):
    confirmation: str = Field(max_length=32)


class HolCommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=12_000)


def create_app(aura: Aura, token: str, keeper: Keeper | None = None, voices: Kokoro | None = None) -> FastAPI:
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"token must be at least {MIN_TOKEN_CHARS} characters")
    routing = ModelRouting(aura.settings)
    cloud_availability = CloudModelAvailability(aura.settings)
    startup_problem: str | None = None
    startup_choice: ModelChoice | None = None

    def installed_for_automatic_routing() -> set[str]:
        # An injected model is an offline/test embedding whose local model name is deliberately
        # not an Ollama installation.  Treat its saved local role choices as usable so the test
        # double is never replaced with a real network client.
        if aura._injected_model:
            return {choice.model for role in ROLES if (choice := routing.choice(role)).provider == "ollama"}
        return usable_ollama_models(aura.settings)

    try:
        startup_choice, _ = automatic_model_choice(
            routing,
            "startup",
            installed_for_automatic_routing(),
            aura.toolbox.secrets.get("anthropic_api_key") is not None,
            cloud_availability.unavailable_models(),
        )
        aura.select_model(startup_choice)
    except ValueError as e:
        # An unavailable startup preference must not make Aura unusable. Keep its local default,
        # tell the settings panel why it was skipped, and re-evaluate it on a later automatic turn.
        startup_problem = str(e)
    supplied_keeper = keeper is not None
    keeper = keeper or ActiveModelKeeper(aura)
    app = FastAPI(title="Aura Server", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, token=token)
    turn = threading.Lock()  # one turn at a time: Aura has a single user and conversation
    app.state.turn = turn
    app.state.model_pull = threading.Lock()  # one disk-heavy Ollama pull at a time
    lifecycle = Lifecycle(aura.toolbox.state, turn, on_cancel=aura.toolbox.developer.stop_tasks)
    app.state.lifecycle = lifecycle
    masker = aura.toolbox.masker
    app.state.last_active = 0.0  # time.monotonic() of the last chat or approval

    def set_sleep_mode(previous: SleepMode, current: SleepMode) -> None:
        """Apply a user's resident-model preference without changing Ollama's global defaults."""
        normal = aura.settings.model_keep_alive
        if previous.enabled and (not current.enabled or previous.model != current.model):
            # Disabling Sleep Mode returns the old model to Aura's ordinary idle lifecycle.
            ModelKeeper(aura.settings.base_url, previous.model, normal).warm_soon()
        if current.enabled:
            # A negative keep_alive is Ollama's per-request "keep resident" setting.
            ModelKeeper(aura.settings.base_url, current.model, -1).warm_soon()

    def warm_startup_models() -> None:
        """Preload a verified local startup choice; Sleep Mode may additionally keep one resident."""
        if startup_choice is not None and startup_choice.provider == "ollama":
            ModelKeeper(aura.settings.base_url, startup_choice.model, aura.settings.model_keep_alive).warm()
        sleep = routing.sleep_mode()
        if sleep.enabled and sleep.model in installed_ollama_models(aura.settings):
            ModelKeeper(aura.settings.base_url, sleep.model, -1).warm()

    # Do this in the server's background, not on the SwiftUI main thread. Test and injected
    # models deliberately skip it: only the installed app should reserve real model memory.
    if not supplied_keeper and not aura._injected_model:
        threading.Thread(target=warm_startup_models, name="aura-startup-model", daemon=True).start()

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

    def lifecycle_state() -> dict:
        return {**lifecycle.status(), "recoverable": len(aura.toolbox.developer.recovery()),
                "recovery_note": aura.recovery_note}  # fmt: skip

    @app.get("/v1/health")
    def health() -> dict:
        s, tb = aura.settings, aura.toolbox
        loaded = keeper.loaded()
        # A local daemon that cannot answer its non-generating status endpoint is unavailable.
        # Cloud providers have no free, provider-neutral readiness probe, so unknown stays honest.
        model_state = ("unavailable" if startup_problem or (aura.active_model.provider == "ollama" and loaded is None)
                       else "loaded" if loaded is True else "idle" if loaded is False else "unknown")
        optional = connections.public()
        connection_attention = [c["label"] for c in optional
                                if c.get("problem") or (c.get("live") or {}).get("state") == "problem"]
        agent_health = workshop.agents.health()
        governance_attention = bool(connection_attention or agent_health["state"] in {"attention", "unknown"}
                                    or tb.continuous.alerts())
        return {
            "status": "ok",
            "model": aura.active_model.model,
            "model_loaded": loaded,
            "search_mode": tb.search.mode,
            "vault": str(tb.vault.root),
            "busy": turn.locked(),
            "awaiting_approval": aura.awaiting_approval,
            "runtime_health": {"state": model_state, "provider": aura.active_model.provider,
                               "model": aura.active_model.model,
                               "detail": (startup_problem or ("The local model service is unavailable." if model_state == "unavailable"
                                          else "Model loaded." if model_state == "loaded" else "Model will load on the next turn." if model_state == "idle"
                                          else "Provider readiness cannot be checked without a model request."))},
            "governance_health": {"state": "attention" if governance_attention else "healthy",
                                  "agent": agent_health, "optional_connection_attention": connection_attention},
            **lifecycle_state(),
        }

    @app.post("/v1/chat")
    def chat(body: ChatIn) -> Response:
        t = lifecycle.begin("chat", "mac", aura.thread_id)
        if t is None:
            return busy()
        app.state.last_active = time.monotonic()
        try:
            route_notice: str | None = None
            developer = body.developer
            if body.model is not None:
                if body.developer is not None:
                    raise ValueError("Choose either an Aura chat model or a Developer, not both.")
                chosen = ModelChoice(body.model.provider, body.model.model).validate()
                if chosen.provider == "ollama" and chosen.model not in installed_ollama_models(aura.settings):
                    raise ValueError(f"Download the local model before using it: {chosen.model}.")
                aura.select_model(chosen)
            else:
                role = infer_role(body.text, body.developer)
                # A code request is delegated through the user's preferred Developer harness.
                # Aura still uses the chat model only to formulate the approval-gated task; it
                # never substitutes an Anthropic chat model for Claude Code or Codex.
                model_role = "chat" if role == "coding" else role
                if role == "coding" and developer is None:
                    developer = routing.coding_engine()
                chosen, route_notice = automatic_model_choice(
                    routing,
                    model_role,
                    installed_for_automatic_routing(),
                    aura.toolbox.secrets.get("anthropic_api_key") is not None,
                    cloud_availability.unavailable_models(),
                )
                aura.select_model(chosen)
                if chosen.provider == "ollama" and not aura._injected_model:
                    # Preload only a model proven to exist locally.  It happens off the request
                    # thread so the app can still render the turn and offer Stop immediately.
                    ModelKeeper(aura.settings.base_url, chosen.model, aura.settings.model_keep_alive).warm_soon()
            events = aura.stream(body.text, developer=developer, turn_id=t.id, cancel=t.cancel)
            if route_notice:
                events = chain((Status(route_notice),), events)
        except (RuntimeError, ValueError) as e:  # an approval is pending, or a selected model is not ready
            lifecycle.end(t, "refused", str(e))
            return _error(409, str(e))
        return stream_turn(events, lifecycle, t, keeper, check_cold=True, masker=masker,
                           selected_model=chosen if body.model is None else None,
                           cloud_availability=cloud_availability)

    @app.post("/v1/approvals")
    def approvals(body: ApprovalsIn) -> Response:
        t = lifecycle.begin("approval", "mac", aura.thread_id)
        if t is None:
            return busy()
        app.state.last_active = time.monotonic()
        try:
            events = aura.resume([a.model_dump() if isinstance(a, EditedApproval) else a for a in body.approvals],
                                 approval_id=body.approval_id, turn_id=t.id, cancel=t.cancel)  # fmt: skip
        except StaleApproval as e:  # an old card, an answer already given, or permissions changed
            lifecycle.end(t, "refused", str(e))
            return JSONResponse({"error": str(e), "stale": True, "pending": aura.pending()}, status_code=409)
        except RuntimeError as e:  # nothing pending
            lifecycle.end(t, "refused", str(e))
            return _error(409, str(e))
        except ValueError as e:  # wrong number of decisions; the approval stays pending
            lifecycle.end(t, "refused", str(e))
            return _error(422, str(e))
        return stream_turn(events, lifecycle, t, keeper, check_cold=False, masker=masker)

    @app.post("/v1/stop")
    def stop(body: StopIn) -> dict:
        """Stop the running turn (the Mac panel's or Slack's) and wait until it has been cleaned
        up: the step in progress ends, a Developer's container is stopped, the turn is released.
        Idempotent: stopping nothing, or a turn that already ended, changes nothing."""
        return lifecycle.stop(body.turn_id)

    @app.get("/v1/approvals/pending")
    def pending_approval() -> dict:
        """The approval waiting now, e.g. after a restart, so the app can show its card again."""
        return {"pending": aura.pending()}

    @app.get("/v1/conversation")
    def conversation() -> dict:
        """The current conversation (it survives restarts), what's waiting, and what a restart cut short."""
        return {"conversation": aura.thread_id, "messages": aura.transcript(), "pending": aura.pending(),
                "recovery_note": aura.recovery_note}  # fmt: skip

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
                "voices_registered": len(voice.system), "neural_voices": len(voice.neural),
                "model_routing": routing.public(), "startup_model_problem": startup_problem}

    @app.get("/v1/models")
    def models() -> dict:
        """Model picker data. A failed local discovery is harmless: the saved name remains valid."""
        local = installed_ollama_models(aura.settings)
        if aura.settings.model not in local:
            local.insert(0, aura.settings.model)
        return {"roles": routing.public(), "role_labels": {role: role.replace("_", " ") for role in ROLES},
                "coding_engine": routing.coding_engine(),
                "ollama_models": local, "anthropic_models": list(routing.anthropic_models()),
                "anthropic_key_set": aura.toolbox.secrets.get("anthropic_api_key") is not None,
                "sleep_mode": routing.sleep_mode().public(),
                "startup_model_problem": startup_problem}

    @app.post("/v1/models/pull")
    def pull_local_model(body: LocalModelPullIn) -> Response:
        """Download one explicitly selected Ollama model, then refresh the local catalog.

        This is deliberately not an agent tool.  It runs only after the person uses Settings and
        confirms the download, and it accepts a model *name*, never a URL or local path.
        """
        try:
            choice = ModelChoice("ollama", body.model).validate()
        except ValueError as e:
            return _error(422, str(e))
        if not app.state.model_pull.acquire(blocking=False):
            return _error(409, "A local model download is already in progress.")
        try:
            pull_ollama_model(aura.settings, choice.model)
        except OllamaModelError as e:
            return _error(503, str(e))
        finally:
            app.state.model_pull.release()
        return JSONResponse(models())

    @app.post("/v1/models/sync-cloud")
    def sync_cloud_models() -> Response:
        """Refresh cloud-model suggestions from Anthropic after the user explicitly asks.

        This is intentionally separate from loading Settings: opening the panel never contacts a
        cloud provider, and a missing/expired API key cannot make the rest of Settings unusable.
        """
        api_key = aura.toolbox.secrets.get("anthropic_api_key")
        if not api_key:
            return _error(422, "Add an Anthropic API key in Settings → Keys before syncing cloud models.")
        try:
            routing.replace_anthropic_models(available_anthropic_models(api_key))
        except (CloudModelError, ValueError) as e:
            return _error(503, str(e))
        return JSONResponse(models())

    @app.put("/v1/models")
    def update_models(body: ModelRoutingIn) -> dict:
        try:
            choices = {role: ModelChoice(item.provider, item.model) for role, item in body.roles.items()}
            routing.replace(choices)
        except ValueError as e:
            return _error(422, str(e))
        return models()

    @app.put("/v1/models/coding-engine")
    def update_coding_engine(body: CodingEngineIn) -> Response:
        try:
            routing.replace_coding_engine(body.engine)
        except ValueError as e:
            return _error(422, str(e))
        return JSONResponse(models())

    @app.put("/v1/models/sleep")
    def update_sleep_mode(body: SleepModeIn) -> Response:
        try:
            proposed = SleepMode(body.enabled, body.model).validate()
        except ValueError as e:
            return _error(422, str(e))
        if proposed.enabled and proposed.model not in installed_ollama_models(aura.settings):
            return _error(422, f"Download the local model before enabling Sleep Mode: {proposed.model}.")
        previous = routing.sleep_mode()
        routing.replace_sleep_mode(proposed)
        set_sleep_mode(previous, proposed)
        return JSONResponse(models())

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
    hol = HolManager(aura.settings, skills, aura.toolbox.audit)
    hol_terminal = HolTerminal(aura.settings.vault_dir, aura.toolbox.sandbox, aura.toolbox.audit,
                               masker=aura.toolbox.masker)

    def hol_choice(body: HolStartIn) -> tuple[dict[str, str], str | None]:
        default = routing.choice("chat")
        chosen = (ModelChoice(body.model.provider, body.model.model).validate()
                  if body.model is not None else default)
        return chosen.public(), body.effort if body.effort is not None else aura.settings.reasoning_effort

    @app.get("/v1/hol")
    def hol_overview() -> Response:
        try:
            # Opening HOL offers its standard session to the Vault Inbox. It remains non-runnable
            # until the user has reviewed and deployed it through Workshop.
            hol.ensure_builtin_candidate()
            return JSONResponse(hol.public(model=routing.choice("chat").public(), effort=aura.settings.reasoning_effort))
        except HolError as e:
            return _error(409, str(e))

    @app.post("/v1/hol/{name}/start")
    def start_hol(name: str, body: HolStartIn) -> Response:
        try:
            model, effort = hol_choice(body)
            prepared = hol.start(name, model=model, effort=effort)
            return JSONResponse({**prepared, "terminal": hol_terminal.start(name)})
        except (HolError, HolTerminalError, ValueError) as e:
            return _error(409, str(e))

    @app.get("/v1/hol/terminals/{terminal_id}")
    def hol_terminal_status(terminal_id: str, after: int = 0) -> Response:
        try:
            return JSONResponse(hol_terminal.status(terminal_id, after))
        except HolTerminalError as e:
            return _error(404, str(e))

    @app.post("/v1/hol/terminals/{terminal_id}/command")
    def hol_terminal_command(terminal_id: str, body: HolCommandIn) -> Response:
        try:
            return JSONResponse(hol_terminal.send(terminal_id, body.command))
        except HolTerminalError as e:
            return _error(409, str(e))

    @app.post("/v1/hol/terminals/{terminal_id}/stop")
    def stop_hol_terminal(terminal_id: str) -> Response:
        try:
            return JSONResponse(hol_terminal.stop(terminal_id))
        except HolTerminalError as e:
            return _error(404, str(e))

    @app.post("/v1/hol/savepoints", status_code=201)
    def create_hol_savepoint(body: HolSavepointIn) -> Response:
        try:
            return JSONResponse(hol.create_savepoint(body.label), status_code=201)
        except HolError as e:
            return _error(409, str(e))

    @app.post("/v1/hol/savepoints/{savepoint_id}/restore")
    def restore_hol_savepoint(savepoint_id: str) -> Response:
        try:
            hol_terminal.stop_all()  # no container may write while its mount is atomically replaced
            return JSONResponse(hol.restore_savepoint(savepoint_id))
        except HolError as e:
            return _error(409, str(e))

    @app.post("/v1/hol/restore-original")
    def restore_hol_original(body: HolRestoreIn) -> Response:
        if body.confirmation != "RESTORE":
            return _error(422, "Type RESTORE to replace the Vault Aura copy with clean Git HEAD.")
        try:
            hol_terminal.stop_all()
            hol.restore_original()
        except HolError as e:
            return _error(409, str(e))
        return JSONResponse({"restored": True, "vault_project": str(aura.settings.project_sandbox_dir)})

    @app.get("/v1/skills")
    def get_skills() -> dict:
        return {"installed": [s.public() for s in skills.scan()], "inbox": workshop.inbox()}

    @app.get("/v1/agents")
    def get_agents() -> dict:
        """Local Workstation records only; no external agent is connected or executed here."""
        return {"installed": workshop.agents.installed(), "inbox": workshop.agent_inbox(),
                "retired": workshop.agents.retired_entries()}

    def candidate(body: AgentCandidateIn, origin: str) -> Response:
        try:
            entry = workshop.agents.create(body.name, body.purpose, origin=origin,
                                           provenance=body.provenance, intent_delta=body.intent_delta,
                                           memory=body.memory)
        except AgentError as e:
            return _error(422, str(e))
        return JSONResponse(entry, status_code=201)

    @app.post("/v1/agents/candidates", status_code=201)
    def create_agent(body: AgentCandidateIn) -> Response:
        return candidate(body, "created")

    @app.post("/v1/agents/imports", status_code=201)
    def import_agent(body: AgentCandidateIn) -> Response:
        """Import declared metadata into quarantine; executable state is never accepted."""
        return candidate(body, "imported")

    @app.post("/v1/agents/{name}/suspend")
    def suspend_agent(name: str, body: AgentLifecycleIn) -> Response:
        """Immediately withdraw recognition; the manifest and decision history remain intact."""
        try:
            return JSONResponse(workshop.agents.suspend(name, body.reason))
        except AgentError as e:
            return _error(409, str(e))

    @app.post("/v1/agents/{name}/revalidate", status_code=202)
    def revalidate_agent(name: str) -> Response:
        """A suspended/expired record must pass a fresh audit before recognition resumes."""
        try:
            workshop.agents.prepare_revalidation(name)
            task = workshop.propose_agent(name)
        except (AgentError, WorkshopError) as e:
            return _error(409, str(e))
        return JSONResponse(task.public(), status_code=202)

    @app.post("/v1/agents/{name}/retire")
    def retire_agent(name: str, body: AgentLifecycleIn) -> Response:
        """Purge the live recognition record while retaining a non-operational archive."""
        try:
            return JSONResponse(workshop.agents.retire(name, body.reason))
        except AgentError as e:
            return _error(409, str(e))

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
        inbox=live.inbox, settings_path=aura.settings.data_dir / "slack_chat.json", lifecycle=lifecycle,
        masker=masker)  # fmt: skip
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
                "timeout_minutes": int(developer.timeout // 60), "recovery": developer.recovery()}  # fmt: skip

    @app.post("/v1/developer/{engine}/check")
    def check_developer(engine: str) -> Response:
        """Explicit, no-write account check for Claude Code or Codex."""
        try:
            return JSONResponse(developer.check_connection(engine))
        except DeveloperError as e:
            return _error(409, str(e))

    @app.get("/v1/recovery")
    def recovery() -> dict:
        """Builds and vault jobs a restart interrupted (the Workshop's recovery section)."""
        return {"items": developer.recovery()}

    @app.post("/v1/recovery/{item_id}/dismiss")
    def dismiss(item_id: str) -> Response:
        try:
            developer.dismiss(item_id)
        except DeveloperError as e:
            return _error(404, str(e))
        return JSONResponse({"items": developer.recovery()})

    @app.post("/v1/builds/{build_id}/retry", status_code=202)
    def retry_build(build_id: str) -> Response:
        """The user's click on an interrupted build's Retry: the same specification, engine and limits."""
        try:
            build = developer.retry(build_id)
        except DeveloperError as e:
            return _error(409, str(e))
        aura.toolbox.audit.record("approval", tool="develop_skill", args={"name": build.skill, "engine": build.engine},
                                  decision="retried in the Workshop", retry_of=build_id)  # fmt: skip
        return JSONResponse(build.public(workshop), status_code=202)

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

    @app.post("/v1/agents/{name}/audit", status_code=202)
    def audit_agent(name: str) -> Response:
        try:
            task = workshop.propose_agent(name)
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
            developer.task_decided(task_id, t.stage)
        except WorkshopError as e:
            return _error(409, str(e))
        finally:
            turn.release()
        return JSONResponse(t.public())

    @app.post("/v1/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> Response:
        try:
            return JSONResponse(workshop.cancel(task_id).public())
        except WorkshopError as e:
            return _error(409, str(e))

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
            **lifecycle_state(),
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
        vault_map.seed(aura.toolbox.vault)  # the Codex of Emergence, laid out once
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

    aura.toolbox.continuous.start(idle=app.state.idle)  # quick pass now; deep pass daily, when idle
    aura.toolbox.live.start()  # Slack and Discord live messages, for whatever is set up
    log.info("listening on 127.0.0.1:%d", sock.getsockname()[1])
    print(f"AURA_READY port={sock.getsockname()[1]}", flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        # Closing the app closes stdin. Before the server process exits, stop active jobs and
        # reclaim every Docker resource in Aura's own namespace.
        aura.toolbox.developer.stop_tasks()
        aura.toolbox.live.stop()
        cleanup_aura_docker(aura.toolbox.audit)
