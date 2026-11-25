"""Vault-local candidate and live registries for recognized agents.

Agents are descriptive manifests, never imported executable runtimes.  A candidate enters the
Workstation inbox, passes the Governor's review, and only then becomes a recognized local record.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

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
    def __init__(self, live: Path, inbox: Path, audit: AuditLog):
        self.live, self.inbox, self.audit = live, inbox, audit

    def entries(self, root: Path) -> list[dict[str, Any]]:
        if not root.is_dir():
            return []
        result = []
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                result.append({**load_manifest(folder), "error": None})
            except AgentError as exc:
                result.append({"name": folder.name, "version": "", "purpose": "", "origin": "", "error": str(exc)})
        return result

    def installed(self) -> list[dict[str, Any]]:
        return self.entries(self.live)

    def pending(self) -> list[dict[str, Any]]:
        return self.entries(self.inbox)

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
