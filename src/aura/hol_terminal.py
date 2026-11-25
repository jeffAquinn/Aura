"""A disposable, Vault-mounted shell for the HOL native terminal.

The Docker daemon is used by Aura Core only.  The container receives no Docker socket, no source
checkout mount, no network, no capabilities, and a read-only image root.  Its only writable host
surface is the user's Aura_Vault bind mount at ``/vault``.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from aura.audit import AuditLog
from aura.masking import Masker, PATTERNS_ONLY
from aura.tools.sandbox import DEFAULT_IMAGE, Sandbox, SandboxUnavailable, find_docker

PREFIX = "aura-hol-"
MAX_COMMAND_CHARS = 12_000
MAX_OUTPUT_LINES = 2_000
IDLE_TIMEOUT_SECONDS = 30 * 60


class HolTerminalError(ValueError):
    pass


@dataclass
class Terminal:
    id: str
    name: str
    docker: str
    process: subprocess.Popen[str]
    output: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_OUTPUT_LINES))
    output_start: int = 0  # absolute cursor of output[0]; deque eviction never rewinds a client
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    stopped: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    masker: Masker = field(default_factory=lambda: PATTERNS_ONLY, repr=False)


class HolTerminal:
    """Owns live container processes; all state-changing calls are server-local and authenticated."""

    def __init__(self, vault_dir: Path, sandbox: Sandbox, audit: AuditLog, masker: Masker | None = None):
        self.vault_dir = vault_dir.expanduser().resolve()
        self.sandbox, self.audit = sandbox, audit
        self.masker = masker or PATTERNS_ONLY
        self._terminals: dict[str, Terminal] = {}
        self._lock = threading.RLock()

    def argv(self, docker: str, name: str) -> list[str]:
        """The complete, fixed Docker invocation; commands never become Docker arguments."""
        if not self.vault_dir.is_dir() or self.vault_dir.is_symlink():
            raise HolTerminalError("Aura_Vault is unavailable or unsafe.")
        user = f"{os.getuid()}:{os.getgid()}"
        return [
            docker, "run", "--rm", "-i", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            "--memory", "1g", "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "64",
            "--ulimit", "nofile=256:256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", user, "--workdir", "/vault/Aura", "--env", "HOME=/tmp",
            "--mount", f"type=bind,source={self.vault_dir},target=/vault",
            DEFAULT_IMAGE, "/bin/sh", "-i",
        ]

    def start(self, session: str) -> dict:
        with self._lock:
            docker = find_docker()
            try:
                self.sandbox.ensure_image(docker)
            except SandboxUnavailable as e:
                raise HolTerminalError(str(e)) from e
            terminal_id = uuid.uuid4().hex[:12]
            name = f"{PREFIX}{terminal_id}"
            try:
                process = subprocess.Popen(self.argv(docker, name), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
            except OSError as e:
                raise HolTerminalError(f"Could not start the HOL container: {e}") from e
            if process.stdin is None or process.stdout is None or process.stderr is None:
                self._stop_process(docker, name, process)
                raise HolTerminalError("The HOL container did not expose terminal streams.")
            terminal = Terminal(terminal_id, name, docker, process, masker=self.masker)
            self._terminals[terminal_id] = terminal
            for stream in (process.stdout, process.stderr):
                threading.Thread(target=self._drain, args=(terminal, stream), daemon=True).start()
            threading.Thread(target=self._idle_watchdog, args=(terminal,), daemon=True).start()
            self._write(terminal, "printf '\\nAura HOL container: /vault is the only writable host mount.\\n'")
            self.audit.record("hol_terminal_started", container=name, vault=str(self.vault_dir), session=session)
            return self.status(terminal_id, 0)

    def send(self, terminal_id: str, command: str) -> dict:
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_CHARS or "\0" in command:
            raise HolTerminalError("Enter a command of 1-12000 non-null characters.")
        terminal = self._get(terminal_id)
        with terminal.lock:
            if terminal.stopped or terminal.process.poll() is not None:
                raise HolTerminalError("This HOL terminal has stopped.")
            self._write(terminal, command)
            terminal.last_activity = time.monotonic()
        # Never record the command text: a person may paste a credential into their own terminal.
        self.audit.record("hol_terminal_command", container=terminal.name, characters=len(command))
        return self.status(terminal_id, 0)

    def status(self, terminal_id: str, after: int) -> dict:
        terminal = self._get(terminal_id)
        if after < 0:
            raise HolTerminalError("Invalid terminal output cursor.")
        with terminal.lock:
            output = list(terminal.output)
            start = max(after, terminal.output_start)
            end = terminal.output_start + len(output)
            running = terminal.process.poll() is None and not terminal.stopped
            return {"id": terminal.id, "running": running,
                    "output": output[start - terminal.output_start:] if start < end else [], "next": end}

    def stop(self, terminal_id: str) -> dict:
        terminal = self._get(terminal_id)
        with terminal.lock:
            if terminal.stopped:
                return self.status(terminal_id, 0)
            terminal.stopped = True
        self._stop_process(terminal.docker, terminal.name, terminal.process)
        self.audit.record("hol_terminal_stopped", container=terminal.name)
        return self.status(terminal_id, 0)

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._terminals)
        for terminal_id in ids:
            try:
                self.stop(terminal_id)
            except HolTerminalError:
                continue

    def _get(self, terminal_id: str) -> Terminal:
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise HolTerminalError("No such HOL terminal.")
        return terminal

    @staticmethod
    def _stop_process(docker: str, name: str, process: subprocess.Popen[str]) -> None:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
        finally:
            try:
                subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass

    @staticmethod
    def _write(terminal: Terminal, command: str) -> None:
        try:
            assert terminal.process.stdin is not None
            terminal.process.stdin.write(command.rstrip("\n") + "\n")
            terminal.process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise HolTerminalError("The HOL terminal has stopped.") from e

    @classmethod
    def _drain(cls, terminal: Terminal, stream) -> None:  # noqa: ANN001
        for line in iter(stream.readline, ""):
            cls._append(terminal, line)

    @staticmethod
    def _append(terminal: Terminal, line: str) -> None:
        with terminal.lock:
            if len(terminal.output) == MAX_OUTPUT_LINES:
                terminal.output_start += 1
            terminal.output.append(terminal.masker.mask(line.rstrip("\n")))

    def _idle_watchdog(self, terminal: Terminal) -> None:
        """The container cannot stay resident indefinitely if the UI or server loses track of it."""
        while terminal.process.poll() is None and not terminal.stopped:
            with terminal.lock:
                idle = time.monotonic() - terminal.last_activity
            if idle >= IDLE_TIMEOUT_SECONDS:
                self.stop(terminal.id)
                return
            time.sleep(min(5, IDLE_TIMEOUT_SECONDS - idle))
