"""Vault-local candidate and live registries for recognized agents.

Agents are descriptive manifests, never imported executable runtimes.  A candidate enters the
Workstation inbox, passes the Governor's review, and only then becomes a recognized local record.
"""

from __future__ import annotations

import json
import re
import tomllib
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from aura.audit import AuditLog


class AgentError(ValueError):
    pass


NAME = re.compile(r"^[a-z][a-z0-9_]{1,38}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_FILES = {"agent.toml", "README.md", "memory.md"}


def load_manifest(folder: Path) -> dict[str, Any]:
    path = folder / "agent.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentError(f"invalid agent manifest: {exc}") from exc
    if set(data) - {"agent", "permissions", "recognition"}:
        raise AgentError("agent manifest has unknown top-level sections")
    agent, permissions, recognition = (data.get("agent"), data.get("permissions"), data.get("recognition"))
    if not all(isinstance(v, dict) for v in (agent, permissions, recognition)):
        raise AgentError("agent, permissions, and recognition sections are required")
    name, version, purpose, origin = (agent.get("name"), agent.get("version"), agent.get("purpose"), agent.get("origin"))
    if name != folder.name or not isinstance(name, str) or not NAME.fullmatch(name):
        raise AgentError("agent name must match its lowercase folder name")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise AgentError("agent version must be semantic (for example 1.0.0)")
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 500:
        raise AgentError("agent purpose is required and must be at most 500 characters")
    if origin not in {"created", "imported"}:
        raise AgentError("agent origin must be 'created' or 'imported'")
    if permissions != {"vault": "none", "network": False, "execution": False}:
        raise AgentError("agent candidates cannot request vault, network, or execution access")
    for field in ("provenance", "intent_delta", "memory"): 
        if not isinstance(recognition.get(field), str) or not recognition[field].strip() or len(recognition[field]) > 1_000:
            raise AgentError(f"recognition.{field} is required and must be at most 1000 characters")
    links = [p.name for p in folder.iterdir() if p.is_symlink()]
    if links:
        raise AgentError(f"agent package contains symbolic links: {', '.join(sorted(links))}")
    unexpected = [p.name for p in folder.iterdir() if p.name not in ALLOWED_FILES]
    if unexpected:
        raise AgentError(f"agent package contains disallowed files: {', '.join(sorted(unexpected))}")
    return {"name": name, "version": version, "purpose": purpose, "origin": origin,
            "recognition": recognition, "permissions": permissions}


class AgentRegistry:
    REVIEW_DAYS = 90
    REASONS = {"audit_failure", "suspected_breach", "expiry", "user_retirement", "superseded"}

    def __init__(self, live: Path, inbox: Path, audit: AuditLog,
                 clock: Callable[[], datetime] | None = None):
        self.live, self.inbox, self.audit = live, inbox, audit
        self.workstation = live.parent
        self.retired = self.workstation / "Retired Agents"
        self.lifecycle_log = self.workstation / "Agent_Lifecycle.jsonl"
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        """The injectable clock keeps expiry evidence deterministic in tests."""
        return self._clock()

    def _events(self, name: str | None = None) -> list[dict[str, Any]]:
        if not self.lifecycle_log.is_file():
            return []
        result: list[dict[str, Any]] = []
        for line in self.lifecycle_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a corrupt history line is evidence, not authority
            if isinstance(entry, dict) and (name is None or entry.get("agent") == name):
                result.append(entry)
        return result

    def _record_lifecycle(self, name: str, state: str, reason: str, *, review_by: str | None = None) -> None:
        if reason not in self.REASONS and reason != "approved":
            raise AgentError("invalid lifecycle reason")
        entry = {"ts": self._now().isoformat(timespec="seconds"), "agent": name,
                 "state": state, "reason": reason}
        if review_by:
            entry["review_by"] = review_by
        self.lifecycle_log.parent.mkdir(parents=True, exist_ok=True)
        with self.lifecycle_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.audit.record("agent_lifecycle", **entry)

    def lifecycle(self, name: str) -> dict[str, str]:
        events = self._events(name)
        if not events:
            return {"state": "active", "review_by": ""}
        latest = events[-1]
        state, review_by = str(latest.get("state", "active")), str(latest.get("review_by", ""))
        if state == "active" and review_by:
            try:
                if datetime.fromisoformat(review_by) <= self._now():
                    return {"state": "expired", "review_by": review_by}
            except ValueError:
                return {"state": "suspended", "review_by": review_by}
        return {"state": state, "review_by": review_by}

    def entries(self, root: Path) -> list[dict[str, Any]]:
        if not root.is_dir():
            return []
        result = []
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                state = ({"state": "candidate", "review_by": ""} if root == self.inbox
                         else self.lifecycle(folder.name))
                result.append({**load_manifest(folder), **state, "error": None})
            except AgentError as exc:
                result.append({"name": folder.name, "version": "", "purpose": "", "origin": "", "error": str(exc)})
        return result

    def installed(self) -> list[dict[str, Any]]:
        return self.entries(self.live)

    def pending(self) -> list[dict[str, Any]]:
        return self.entries(self.inbox)

    def retired_entries(self) -> list[dict[str, Any]]:
        return self.entries(self.retired)

    def _health_problem(self, entries: list[dict[str, Any]]) -> str | None:
        """Return evidence that prevents a health claim, if any.

        An absent directory is a valid first-run empty registry: directories are created lazily.
        A path that exists but is not a directory, an invalid manifest, or malformed lifecycle
        history is different: the registry is present but cannot be trusted as a complete record.
        """
        if self.live.exists() and not self.live.is_dir():
            return "the recognized-agent registry is not a directory"
        invalid = next((entry.get("error") for entry in entries if entry.get("error")), None)
        if invalid:
            return f"an agent manifest is invalid: {invalid}"
        if self.lifecycle_log.exists() and not self.lifecycle_log.is_file():
            return "the lifecycle history is not a file"
        if not self.lifecycle_log.is_file():
            return None
        try:
            lines = self.lifecycle_log.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return f"the lifecycle history cannot be read: {exc}"
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return f"the lifecycle history is malformed at line {line_number}"
            if not isinstance(event, dict) or not all(isinstance(event.get(field), str)
                                                    for field in ("ts", "agent", "state", "reason")):
                return f"the lifecycle history is incomplete at line {line_number}"
        return None

    def health(self) -> dict[str, Any]:
        """Governance evidence only; it makes no claim about runtime availability."""
        counts: dict[str, int] = {"active": 0, "suspended": 0, "expired": 0, "due": 0}
        try:
            entries = self.installed()
        except (AgentError, OSError) as exc:
            return {"state": "unknown", "agents": counts,
                    "detail": f"The Workstation registry cannot be read: {exc}"}
        if problem := self._health_problem(entries):
            return {"state": "unknown", "agents": counts,
                    "detail": f"The Workstation registry cannot be trusted: {problem}."}
        soon = self._now() + timedelta(days=14)
        for entry in entries:
            state = str(entry.get("state", "active"))
            if state not in {"active", "suspended", "expired"}:
                return {"state": "unknown", "agents": counts,
                        "detail": f"The Workstation registry has an unknown lifecycle state: {state}."}
            counts[state] += 1
            review_by = str(entry.get("review_by", ""))
            if state == "active" and not review_by:
                return {"state": "unknown", "agents": counts,
                        "detail": f"Recognized agent '{entry['name']}' has no review deadline."}
            if not review_by:
                continue
            try:
                deadline = datetime.fromisoformat(review_by)
                if deadline.tzinfo is None:
                    raise ValueError("deadline has no timezone")
            except ValueError:
                return {"state": "unknown", "agents": counts,
                        "detail": f"Recognized agent '{entry['name']}' has an invalid review deadline."}
            if state == "active" and deadline <= soon:
                counts["due"] += 1
        attention = counts["suspended"] + counts["expired"] + counts["due"]
        return {"state": "attention" if attention else "healthy", "agents": counts,
                "detail": (f"{attention} agent record(s) need review" if attention else "No agent lifecycle action is due.")}

    def create(self, name: str, purpose: str, *, origin: str, provenance: str, intent_delta: str = "Unmeasured: newly created candidate.", memory: str = "No memory imported.") -> dict[str, Any]:
        name, purpose = name.strip(), purpose.strip()
        if not NAME.fullmatch(name):
            raise AgentError("agent name must be lowercase letters, digits, and underscores")
        if origin not in {"created", "imported"}:
            raise AgentError("agent origin must be created or imported")
        folder = self.inbox / name
        if folder.exists() or (self.live / name).exists():
            raise AgentError(f"an agent called '{name}' already exists")
        folder.mkdir(parents=True)
        quoted = lambda value: json.dumps(value, ensure_ascii=False)
        manifest = "\n".join([
            "[agent]", f"name = {quoted(name)}", 'version = "1.0.0"', f"purpose = {quoted(purpose)}", f"origin = {quoted(origin)}", "",
            "[permissions]", 'vault = "none"', "network = false", "execution = false", "",
            "[recognition]", f"provenance = {quoted(provenance)}", f"intent_delta = {quoted(intent_delta)}", f"memory = {quoted(memory)}", "",
        ])
        try:
            (folder / "agent.toml").write_text(manifest, encoding="utf-8")
            entry = load_manifest(folder)
        except Exception:
            for child in folder.iterdir() if folder.exists() else ():
                child.unlink()
            if folder.exists():
                folder.rmdir()
            raise
        self.audit.record("agent_candidate_created", agent=name, origin=origin)
        return entry

    def recognize(self, name: str) -> None:
        """Start the bounded recognition period after an approved audit."""
        review_by = (self._now() + timedelta(days=self.REVIEW_DAYS)).isoformat(timespec="seconds")
        self._record_lifecycle(name, "active", "approved", review_by=review_by)

    def suspend(self, name: str, reason: str) -> dict[str, Any]:
        if reason not in self.REASONS - {"user_retirement", "superseded"}:
            raise AgentError("suspension requires an audit failure, suspected breach, or expiry")
        if not (self.live / name).is_dir():
            raise AgentError(f"'{name}' is not a recognized agent")
        self._record_lifecycle(name, "suspended", reason)
        return next(a for a in self.installed() if a["name"] == name)

    def prepare_revalidation(self, name: str) -> dict[str, Any]:
        """Return a suspended or expired record to quarantine for a new full audit."""
        source, target = self.live / name, self.inbox / name
        state = self.lifecycle(name)["state"]
        if not source.is_dir():
            raise AgentError(f"'{name}' is not a recognized agent")
        if state not in {"suspended", "expired"}:
            raise AgentError("only suspended or expired agents require revalidation")
        if target.exists():
            raise AgentError(f"'{name}' is already in the Agent Inbox")
        shutil.copytree(source, target, symlinks=False)
        self.audit.record("agent_revalidation_proposed", agent=name, prior_state=state)
        return {**load_manifest(target), "state": "candidate", "review_by": ""}

    def retire(self, name: str, reason: str) -> dict[str, Any]:
        """Purge operational recognition while retaining a sanitized manifest archive and lineage."""
        if reason not in {"user_retirement", "superseded"}:
            raise AgentError("retirement requires user_retirement or superseded")
        source, target = self.live / name, self.retired / name
        if not source.is_dir():
            raise AgentError(f"'{name}' is not a recognized agent")
        if target.exists():
            raise AgentError(f"a retired archive already exists for '{name}'")
        self.retired.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        self._record_lifecycle(name, "retired", reason)
        return {**load_manifest(target), **self.lifecycle(name), "error": None}
