"""Session-gated Developer conversations for the Human-Out-of-Loop terminal.

The HOL shell is intentionally offline and credential-free.  Claude Code and Codex therefore
never run in that shell: each message is executed by Aura's existing ``DevContainer``, whose
vendor-only egress and ephemeral credential mounts are already the account-backed Developer
boundary.  The container receives a disposable copy of Aura_Vault/Aura; changes are collected
for explicit per-file decisions before anything reaches the Vault.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aura.audit import AuditLog
from aura.developer import vault_tasks as vt
from aura.developer.engines import Engine, EngineEvent
from aura.developer.workflow import Developer, DeveloperError
from aura.masking import Masker, PATTERNS_ONLY
from aura.tools.vault import Vault

MAX_FILES = 10_000
MAX_BYTES = 512 * 1024 * 1024
MAX_TRANSCRIPT = 24_000
MAX_MESSAGE = 8_000


class HolAgentError(ValueError):
    pass


@dataclass
class AgentSession:
    terminal_id: str
    skill: str
    engine: str
    prompt: str
    root: Path
    work: Path
    copy: vt.Copy
    transcript: list[tuple[str, str]] = field(default_factory=list)
    pending: list[vt.Change] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event, repr=False)


class HolAgent:
    """Owns the Developer-side half of an HOL session; never exposes engine credentials."""

    def __init__(self, developer: Developer, vault: Vault, audit: AuditLog, masker: Masker | None = None):
        self.developer, self.vault, self.audit = developer, vault, audit
        self.masker = masker or PATTERNS_ONLY
        self.sessions: dict[str, AgentSession] = {}
        self._lock = threading.RLock()

    def engines(self) -> list[dict]:
        # ``problem`` already includes Docker readiness and account sign-in state.  It is public
        # status only; neither token values nor paths are returned.
        return self.developer.engines_status()

    def start(self, terminal_id: str, skill: str, prompt: str, engine_name: str, project: Path) -> list[str]:
        if engine_name not in self.developer.engines:
            raise HolAgentError("Choose Claude Code or Codex.")
        engine = self.developer.engines[engine_name]
        if problem := (self.developer.container.problem() or engine.problem(self.developer.secrets)):
            raise HolAgentError(f"{engine.label} isn't ready: {problem}")
        if self.developer.running() or self.developer._busy.locked():
            raise HolAgentError("Claude Code or Codex is busy with another Aura job; wait for it to finish.")
        project = project.resolve()
        if not project.is_dir() or not project.is_relative_to(self.vault.root):
            raise HolAgentError("The HOL project is unavailable or outside Aura_Vault.")
        root = self.developer.settings.dev_dir / ("h-" + uuid.uuid4().hex[:8])
        try:
            work, copy = self._prepare(project, root / "work")
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        session = AgentSession(terminal_id, skill, engine_name, prompt, root, work, copy)
        with self._lock:
            self.sessions[terminal_id] = session
        self.audit.record("hol_agent_started", terminal=terminal_id, skill=skill, engine=engine_name,
                          files=len(copy.files), bytes=copy.bytes)
        return [f"{engine.label} is ready in Aura's isolated Developer container.",
                "The terminal shell has no credentials. Send a message to begin; file changes remain pending for review."]

    def send(self, terminal_id: str, message: str) -> dict:
        message = message.strip()
        if not message or len(message) > MAX_MESSAGE or "\0" in message:
            raise HolAgentError(f"Enter a message of 1-{MAX_MESSAGE} non-null characters.")
        session = self._session(terminal_id)
        if session.pending:
            raise HolAgentError("Review or deny the pending file changes before sending another message.")
        if drift := vt.baseline_drift(self.vault, session.copy):
            self._refresh_workspace(session)
            self.audit.record("hol_agent_vault_drift", terminal=terminal_id, changes=len(drift))
            summary = "; ".join(drift[:3])
            notice = ("Aura_Vault changed outside this HOL session (" + summary
                      + "). The isolated workspace was refreshed; review the Vault and send again.")
            # Each Developer invocation is ephemeral, but it receives this bounded transcript on
            # the next turn.  It must know its workspace changed rather than infer from stale chat.
            session.transcript.append(("Aura", notice))
            raise HolAgentError(notice)
        engine = self.developer.engines[session.engine]
        if problem := (self.developer.container.problem() or engine.problem(self.developer.secrets)):
            raise HolAgentError(f"{engine.label} isn't ready: {problem}")
        if self.developer.running() or not self.developer._busy.acquire(blocking=False):
            raise HolAgentError("Claude Code or Codex is busy with another Aura job; wait for it to finish.")
        try:
            session.stop.clear()
            status: list[str] = []
            spoken: list[str] = []

            def on_event(event: EngineEvent) -> None:
                if event.kind == "status" and event.text:
                    status.append(event.text)
                elif event.kind == "message" and event.text:
                    spoken.append(event.text)

            task = self._turn_prompt(session, message)
            outcome = self.developer.container.run(engine, session.work, session.root, task, 0.0,
                                                   self.developer.timeout, on_event, session.stop)
            if outcome.stopped or session.stop.is_set():
                raise HolAgentError("The HOL assistant was stopped.")
            if outcome.timed_out:
                raise HolAgentError(f"{engine.label} timed out before completing this message.")
            if outcome.error:
                raise HolAgentError(self.masker.mask(outcome.error) or f"{engine.label} couldn't run.")
            answer = self.masker.mask(outcome.result if outcome.result not in ("", "done") else (spoken[-1] if spoken else ""))
            answer = answer.strip() or f"{engine.label} completed the turn."
            session.transcript.extend([("User", message), (engine.label, answer)])
            session.pending = vt.collect(self.vault, session.copy, session.work)
            self.audit.record("hol_agent_message", terminal=terminal_id, engine=engine.name,
                              characters=len(message), changes=len(session.pending))
            output = [f"{engine.label}: {answer}"]
            if session.pending:
                output.append(f"{len(session.pending)} file change(s) are waiting for your HOL review.")
            elif status:
                output.append(f"{engine.label}: {status[-1]}")
            return {"output": output, "changes": [self._change(change) for change in session.pending]}
        finally:
            self.developer._busy.release()

    def decide(self, terminal_id: str, allowed: list[bool]) -> dict:
        session = self._session(terminal_id)
        if len(allowed) != len(session.pending):
            raise HolAgentError("Choose allow or deny for every pending file change.")
        lines = vt.apply(self.vault, session.copy, session.work, session.pending, allowed)
        self.audit.record("hol_agent_changes_decided", terminal=terminal_id, allowed=sum(allowed),
                          denied=len(allowed) - sum(allowed))
        session.pending = []
        # Recopy from the Vault after the decision.  This drops denied edits and gives the next
        # isolated run a fresh, hash-checked baseline while keeping only the bounded transcript.
        self._refresh_workspace(session)
        return {"output": lines, "changes": []}

    def _refresh_workspace(self, session: AgentSession) -> None:
        """Replace the disposable copy only after a complete fresh copy is ready."""
        project = self.vault.resolve(session.copy.folder)
        fresh = session.root / ("work-" + uuid.uuid4().hex[:8])
        work, copy = self._prepare(project, fresh)
        previous = session.work
        session.work, session.copy = work, copy
        shutil.rmtree(previous, ignore_errors=True)

    def stop(self, terminal_id: str) -> None:
        with self._lock:
            session = self.sessions.pop(terminal_id, None)
        if session is None:
            return
        session.stop.set()
        shutil.rmtree(session.root, ignore_errors=True)
        self.audit.record("hol_agent_stopped", terminal=terminal_id, engine=session.engine)

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self.sessions)
        for terminal_id in ids:
            self.stop(terminal_id)

    def _session(self, terminal_id: str) -> AgentSession:
        with self._lock:
            session = self.sessions.get(terminal_id)
        if session is None:
            raise HolAgentError("This HOL session has no selected connected assistant.")
        return session

    def _prepare(self, source: Path, work: Path) -> tuple[Path, vt.Copy]:
        files, folders = vt._walk(source)
        size = sum(path.stat().st_size for path in files)
        if len(files) > MAX_FILES or size > MAX_BYTES:
            raise HolAgentError(f"The approved HOL project is too large for a bounded Developer session "
                                f"({len(files)} files, {size // (1024 * 1024)} MB; limit {MAX_FILES} files, "
                                f"{MAX_BYTES // (1024 * 1024)} MB).")
        work.mkdir(parents=True, exist_ok=True)
        copy = vt.Copy("Aura", {}, set(), 0)
        for folder in folders:
            rel = folder.relative_to(source)
            (work / rel).mkdir(parents=True, exist_ok=True)
            copy.folders.add(rel.as_posix())
        for path in files:
            rel = path.relative_to(source)
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            copy.files[rel.as_posix()] = vt._sha(target)
            copy.bytes += path.stat().st_size
        return work, copy

    def _turn_prompt(self, session: AgentSession, message: str) -> str:
        history = "\n".join(f"{who}: {text}" for who, text in session.transcript)
        history = history[-MAX_TRANSCRIPT:]
        return ("You are the selected connected assistant in Aura's Human Out of the Loop session. "
                "You are in a disposable copy of Aura_Vault/Aura and cannot access the host or source checkout. "
                "Work only on the user's requested session. Any changed file will require their individual approval "
                "before it can reach Aura_Vault. Do not use the network except the account transport provided by Aura.\n\n"
                f"Approved session instructions:\n{session.prompt}\n\n"
                f"Prior terminal conversation (bounded):\n{history or '(none)'}\n\n"
                f"User's new terminal message:\n{message}\n\n"
                "Reply with a concise progress/result summary and make only necessary workspace changes.")

    @staticmethod
    def _change(change: vt.Change) -> dict:
        return {"path": change.path, "kind": change.kind, "description": change.describe(),
                "added": change.added, "removed": change.removed}
