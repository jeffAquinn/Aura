"""Append-only JSONL audit log of everything Aura does that matters."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from aura.masking import PATTERNS_ONLY, Masker

log = logging.getLogger(__name__)
T = TypeVar("T")


# The Observer's kinds (docs/observer.md), decided here, next to the code that names the events,
# so the app never keeps its own list. tests/test_observer.py runs every recorded name through it.
ALERTS = {"skill_flagged", "recursion_halted", "skill_rejected", "approval_unrecoverable", "sandbox_cleanup_failed",
          "path_blocked", "agent_audit_failed", "capabilities_not_published", "hol_agent_vault_drift",
          "continuous_audit_failed", "connection_sign_in_failed"}  # fmt: skip
VAULT = {"deleted", "trashed", "scratch_swept", "file_written", "folder_created", "generated_file_kept", "project_sandbox_seeded",
         "project_sandbox_workflow_seeded"}
KINDS = (("containers", ("container_", "sandbox_", "docker_", "developer_image_", "hol_terminal_started",
                         "hol_terminal_stopped")), ("egress", ("egress_", "search_")),
         ("vault", ("vault_",)), ("connections", ("connection_", "secret_", "slack_", "developer_connection")),
         ("actions", ("approval", "code_run", "skill_", "agent_", "build_", "developer_run", "hol_", "continuous_",
                      "persona_", "voice_", "capabilities_", "local_model_")))  # fmt: skip


def category(event: str) -> str:
    """Which part of Aura an audit event belongs to: alerts, containers, egress, vault,
    connections, actions, or other."""
    if event in ALERTS:
        return "alerts"
    if event in VAULT:
        return "vault"
    return next((kind for kind, prefixes in KINDS if event.startswith(prefixes)), "other")


def described(entry: dict[str, Any]) -> dict[str, Any]:
    """An entry as the Observer is given it: as written, plus its kind (never written to the log)."""
    return {**entry, "category": category(str(entry.get("event", "")))}


class AuditLog:
    def __init__(self, path: Path, masker: Masker = PATTERNS_ONLY):
        self.path = path
        self.masker = masker  # the Toolbox swaps in one that also knows the Keychain's values
        # Tool calls may run in parallel threads. Reentrant: a listener runs under it (below).
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def listen(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """Also hand every entry, masked as written, to `listener` (the Observer's live feed). It
        runs under the log's lock, so an entry is never in the file but not yet on the feed (see
        `snapshot`): it must be quick. A failing listener is logged and never stops the record."""
        self._listeners.append(listener)

    def record(self, event: str, **fields: Any) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        entry.update(self.masker.mask_value(fields))  # never a credential, even one pasted into a message
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            for listener in list(self._listeners):
                try:
                    listener(json.loads(line))  # exactly what was written
                except Exception:
                    log.exception("an audit listener failed")

    def recent(self, n: int) -> list[dict[str, Any]]:
        """The newest `n` entries, oldest first (a line that isn't valid JSON is skipped)."""
        with self._lock:
            return self._tail(n)

    def snapshot(self, n: int, mark: Callable[[], T]) -> tuple[list[dict[str, Any]], T]:
        """`recent(n)` and `mark()` taken together: no entry is recorded between them. The
        Observer's history uses it with the feed's position, so it neither misses nor repeats one."""
        with self._lock:
            return self._tail(n), mark()

    def _tail(self, n: int, block: int = 64 * 1024) -> list[dict[str, Any]]:
        """Read back from the end only as far as `n` lines need: the log is never rotated."""
        if n <= 0 or not self.path.exists():
            return []
        with self.path.open("rb") as f:
            end = f.seek(0, 2)
            data, pos = b"", end
            while pos > 0 and data.count(b"\n") <= n:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
        lines = [line for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()]
        if pos > 0:
            lines = lines[1:]  # the first may be cut part-way
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
