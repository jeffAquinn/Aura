"""Aura's own llama-server, run like Ollama runs its models (docs/model-settings.md §11).

Aura starts llama-server for the llama.cpp model a turn needs, and stops it (unloading the model)
when another model is chosen, after the idle time (``AURA_MODEL_KEEP_ALIVE``, as for Ollama), or
when Aura quits. The person never runs a script.

- Models are the ``.gguf`` files in ``AURA_LLAMACPP_MODELS_DIR`` (default ``~/models``); a model's
  name is its file name without ``.gguf``. Aura serves only those files.
- The program is ``AURA_LLAMA_SERVER`` (default: Homebrew's), never one a model or a skill names.
- It listens on the ``AURA_LLAMACPP_URL`` port, on 127.0.0.1 only, with a random API key made
  for this Aura process and passed in the environment (``ps`` shows the command line, not the
  environment), so no other program or web page on the Mac can use it.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit

import json

import httpx

from aura.model_routing import ModelChoice, llamacpp_root, valid_model_name
from aura.model_settings import ModelSettingsStore, budget

if TYPE_CHECKING:
    from aura.config import Settings

log = logging.getLogger("aura.llama_server")

API_KEY = secrets.token_urlsafe(32)  # one per Aura process; never written anywhere
LOAD_TIMEOUT = 300.0  # seconds: a large model's first load from disk
_PART = re.compile(r"-(\d{5})-of-\d{5}$")  # a split GGUF: only its first part is a model


_LISTED: dict[tuple[str, str], tuple[float, list[str]]] = {}  # (folder, program) -> (when, names)
LIST_FOR = 5.0  # seconds: health polls and a turn's routing share one scan of the models folder


def llamacpp_models(settings: Settings) -> list[str]:
    """The llama.cpp models Aura can run: the model files, when llama-server is installed.
    Aura starts llama-server for one when a turn needs it."""
    if not os.access(settings.llama_server, os.X_OK):
        return []
    key = (str(settings.llamacpp_models_dir), settings.llama_server)
    when, names = _LISTED.get(key, (0.0, []))
    if time.monotonic() - when > LIST_FOR:
        names = sorted(gguf_models(settings))
        _LISTED[key] = (time.monotonic(), names)
    return names


class LocalModelError(RuntimeError):
    """Aura can't run a llama.cpp model right now; the message says why and what to do."""


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
DEFAULT_IDLE = 600.0


def keep_alive_seconds(value: str | int) -> float | None:
    """Ollama's keep-alive ("10m", "1h30m", "300ms", "600") in seconds; negative: never unload.
    A value Aura can't read falls back to ten minutes rather than stopping Aura from starting."""
    text = str(value).strip().lower()
    try:
        number = float(text)
        return None if number < 0 else number
    except ValueError:
        pass
    if text.startswith("-"):
        return None
    parts = _DURATION.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        log.warning("couldn't read AURA_MODEL_KEEP_ALIVE=%r; using %.0f seconds", value, DEFAULT_IDLE)
        return DEFAULT_IDLE
    return sum(float(n) * _UNITS[u] for n, u in parts)


def gguf_models(settings: Settings) -> dict[str, Path]:
    """The llama.cpp models on this Mac: each ``.gguf`` in the models folder, by name."""
    root = settings.llamacpp_models_dir
    if not root.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*.gguf")):
        name = path.stem
        if "mmproj" in name.lower():  # a vision projector, not a model to chat with
            continue
        if part := _PART.search(name):
            if part.group(1) != "00001":
                continue
            name = name[: part.start()]
        if valid_model_name(name) and path.is_file():
            found.setdefault(name, path)
    return found


class LlamaServer:
    """One llama-server process at a time, serving one model (the Mac's memory allows one)."""

    def __init__(self, settings: Settings, audit: Any = None, busy: Callable[[], bool] = lambda: False,
                 watch_every: float = 15.0, before_load: Callable[[], None] = lambda: None):
        self.settings = settings
        self.audit = audit
        self.busy = busy  # a turn is running: never unload under it
        # Called just before a model loads: frees the memory other local models hold (the server
        # unloads Ollama's), so the two never compete for a 24 GB Mac's GPU memory.
        self.before_load = before_load
        self.root = llamacpp_root(settings)
        parts = urlsplit(self.root)
        self.host, self.port = parts.hostname or "127.0.0.1", parts.port or 80
        self.binary = settings.llama_server
        self.idle_after = keep_alive_seconds(settings.model_keep_alive)
        self.pid_file = settings.data_dir / "llama-server.pid"
        self.log_path = settings.audit_log.parent / "llama-server.log"
        self.model: str | None = None
        self._proc: subprocess.Popen | None = None
        self._ready = False
        self._last_used = time.monotonic()
        self._lock = threading.RLock()
        self._watch_every = watch_every
        self._watcher: threading.Thread | None = None
        self._closed = threading.Event()
        self._wanted: str | None = None  # the model the latest request wants; a load for another stops
        self._served_window: int | None = None  # the -c the running server was started with
        self.reap_stale()

    # ── what health and routing ask ─────────────────────────────────────────────────────────

    def installed(self) -> bool:
        return os.access(self.binary, os.X_OK)

    def models(self) -> list[str]:
        return llamacpp_models(self.settings)

    def loaded(self, model: str) -> bool:
        """Is this model loaded and answering? Never waits for a load in progress."""
        proc = self._proc
        return proc is not None and proc.poll() is None and self.model == model and self._ready

    def loading(self, model: str) -> bool:
        """Is this model being loaded right now?"""
        proc = self._proc
        return proc is not None and proc.poll() is None and self.model == model and not self._ready

    def touch(self) -> None:
        """A turn used the model: restart its idle time."""
        self._last_used = time.monotonic()

    # ── starting and stopping ───────────────────────────────────────────────────────────────

    def prepare(self, choice: ModelChoice, cancel: threading.Event | None = None) -> None:
        """Before a turn (or when the chat box picks a model): load a llama.cpp model, or unload
        llama-server's model when the turn uses another one. A load still in progress for an
        earlier pick gives way at once instead of finishing first."""
        self._wanted = choice.model if choice.provider == "llamacpp" else None
        if choice.provider == "llamacpp":
            self.ensure(choice.model, cancel)
        elif self._proc is not None:
            self.stop("another model was chosen")

    def argv(self, path: Path, name: str, window: int | None = None) -> list[str]:
        return [
            str(self.binary), "-m", str(path), "--alias", name,
            "--host", self.host, "--port", str(self.port),
            "--jinja",  # the model's chat template: Aura's tool calls need it
            "-c", str(window or self.window(name)),  # Aura's memory compaction matches the window
            "--parallel", "1",  # one conversation: the whole window serves the turn
            "-ngl", "99",  # every layer on the GPU
            "-fa", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",  # a smaller KV cache
            "--cors-origins", "localhost",  # no web page on another origin
        ]

    def window(self, model: str) -> int:
        """The window to serve `model` with: its own context budget, or the llama.cpp default."""
        choice = ModelChoice("llamacpp", model)
        return budget(self.settings, choice, ModelSettingsStore(self.settings).get(choice))

    def ensure(self, model: str, cancel: threading.Event | None = None) -> None:
        """Serve `model`, starting (or switching) llama-server if needed; returns once it answers.
        Gives up early, stopping the load, when Aura quits, the turn is stopped (`cancel`), or a
        later request wants another model."""
        self._wanted = model
        with self._lock:
            self.touch()
            window = self.window(model)
            if self.loaded(model) and self._served_window == window:  # the usual answer
                return
            path = gguf_models(self.settings).get(model)
            if path is None:
                raise LocalModelError(f"Aura can't find the llama.cpp model '{model}' in {self.settings.llamacpp_models_dir}.")
            if not self.installed():
                raise LocalModelError("llama.cpp isn't installed. Install it with `brew install llama.cpp`, then try again.")
            self._stop_locked("its context budget changed" if self.model == model else "another model was chosen")
            if self._port_in_use():
                raise LocalModelError(
                    f"Another program is using {self.host}:{self.port}, so Aura can't start llama-server there. "
                    "Quit it (for example a llama-server started by hand), then try again.")
            try:
                self.before_load()
            except Exception:  # noqa: BLE001 — freeing memory first helps; it never blocks the load
                log.exception("couldn't free memory before loading %s", model)
            started = time.monotonic()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("ab") as out:
                self._proc = subprocess.Popen(self.argv(path, model, window), stdin=subprocess.DEVNULL, stdout=out,
                                              stderr=subprocess.STDOUT, env={**os.environ, "LLAMA_API_KEY": API_KEY})
            self.model, self._ready = model, False
            self._served_window = window
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            # The owner too: a second Aura on this data folder must not take this one for a crash.
            self.pid_file.write_text(json.dumps({"pid": self._proc.pid, "owner": os.getpid()}))
            log.info("llama-server starting for %s (pid %d)", model, self._proc.pid)
            # Ready only while Aura's own process runs: a program that took the port after the
            # check above must not be taken for it (llama-server would have exited).
            while not (self._healthy() and self._proc.poll() is None):
                if self._closed.is_set() or (cancel is not None and cancel.is_set()) or self._wanted != model:
                    self._stop_locked("loading was stopped", record=False)
                    raise LocalModelError(f"Loading '{model}' was stopped.")
                if self._proc.poll() is not None:
                    self._stop_locked("it stopped while loading", record=False)
                    raise LocalModelError(f"llama-server stopped while loading '{model}'. See {self.log_path}.")
                if time.monotonic() - started > LOAD_TIMEOUT:
                    self._stop_locked("it took too long to load", record=False)
                    raise LocalModelError(f"'{model}' took more than {LOAD_TIMEOUT:.0f} seconds to load. See {self.log_path}.")
                time.sleep(0.25)
            self._ready = True
            self.touch()
            self._record("local_model_loaded", model=model, provider="llamacpp", pid=self._proc.pid,
                         seconds=round(time.monotonic() - started, 1))
            self._watch()

    def stop(self, reason: str = "Aura quit") -> None:
        self._wanted = None  # a load in progress gives way
        with self._lock:
            self._stop_locked(reason)

    def close(self) -> None:
        """Aura is quitting: stop the server and the idle watcher (a load in progress gives way)."""
        self._closed.set()
        self.stop("Aura quit")

    def _stop_locked(self, reason: str, record: bool = True) -> None:
        proc, model = self._proc, self.model
        if proc is None:
            return
        if proc.poll() is not None and record:  # it had stopped by itself: say so, not `reason`
            reason = f"llama-server exited by itself (code {proc.returncode})"
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self._proc, self.model, self._ready = None, None, False
        self.pid_file.unlink(missing_ok=True)
        log.info("llama-server stopped (%s)", reason)
        if record:
            self._record("local_model_unloaded", model=model, provider="llamacpp", reason=reason)

    # ── the idle timer ──────────────────────────────────────────────────────────────────────

    def check_idle(self) -> bool:
        """Unload a model that has been idle past the keep-alive time. True if it did."""
        if self.idle_after is None or self._proc is None or self.busy():
            return False
        if time.monotonic() - self._last_used < self.idle_after:
            return False
        if not self._lock.acquire(blocking=False):  # a load or a stop is in progress
            return False
        try:
            if self._proc is None or self.busy():
                return False
            self._stop_locked(f"idle for {self.settings.model_keep_alive}")
            return True
        finally:
            self._lock.release()

    def _watch(self) -> None:
        if self._watcher is not None and self._watcher.is_alive():
            return

        def run() -> None:
            while not self._closed.wait(self._watch_every):
                try:
                    self.check_idle()
                except Exception:  # noqa: BLE001 — the watcher must outlive one failed stop
                    log.exception("idle check failed")

        self._watcher = threading.Thread(target=run, name="aura-llama-idle", daemon=True)
        self._watcher.start()

    # ── helpers ─────────────────────────────────────────────────────────────────────────────

    def _healthy(self) -> bool:
        try:
            return httpx.get(f"{self.root}/health", timeout=2,
                             headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200
        except httpx.HTTPError:
            return False

    def _port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((self.host, self.port)) == 0

    def reap_stale(self) -> None:
        """A llama-server left by an Aura that didn't quit cleanly: stop it, if it really is one and
        the Aura that started it is gone (another Aura on this data folder may still be using it)."""
        try:
            saved = json.loads(self.pid_file.read_text())
            pid, owner = int(saved["pid"]), int(saved["owner"])
            os.kill(pid, 0)  # still running? (no: nothing to stop, and no `ps` to wait for)
        except (OSError, ValueError, KeyError, TypeError):
            self.pid_file.unlink(missing_ok=True)
            return
        if owner != os.getpid() and _running(owner):
            return  # its Aura still runs: not ours to stop
        try:
            command = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True,
                                     text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            command = ""
        if "llama-server" in command and f"--port {self.port}" in command:
            try:
                os.kill(pid, signal.SIGTERM)
                log.info("stopped a llama-server left from an earlier run (pid %d)", pid)
            except OSError:
                pass
            deadline = time.monotonic() + 10  # let it free the port before a load checks it
            while _running(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
        self.pid_file.unlink(missing_ok=True)

    def _record(self, event: str, **fields: Any) -> None:
        if self.audit is not None:
            self.audit.record(event, **fields)


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, under another user
    except OSError:
        return False
    return True
