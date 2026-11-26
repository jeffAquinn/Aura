"""The Skill Registry: Aura's library of approved, sandboxed capabilities.

Each skill is one folder in skills/: skill.toml (manifest), skill.py (def run(args) -> str) and
test_skill.py. Skill code is never imported on the host: the host only reads, hashes and scans
it. It runs only inside its own sandbox image. A skill loads only if the user approved this exact
content (by hash); approvals live in Aura's data folder, outside the repository, so nothing written
into skills/ can approve itself.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from aura.audit import AuditLog
from aura.capabilities import Capability, skill_capability
from aura.tools.sandbox import Sandbox, SandboxUnavailable, format_result

NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.+!-]+$")
MAX_BYTES = 256 * 1024
BASE_IMAGE = "aura-sandbox:1"

# Runs inside the skill's container: call run() with the given arguments.
CALL = """
import json, sys
sys.path.insert(0, "/skill")
from skill import run
out = run(json.loads({args!r}))
print(out if isinstance(out, str) else json.dumps(out))
"""
# Runs inside the skill's container: every test_* function in test_skill.py, as JSON results.
TESTS = """
import importlib, json, sys, traceback
sys.path.insert(0, "/skill")
mod = importlib.import_module("test_skill")
results = []
for name in sorted(n for n in dir(mod) if n.startswith("test_")):
    try:
        getattr(mod, name)()
        results.append({"name": name, "status": "passed", "detail": ""})
    except Exception:
        results.append({"name": name, "status": "failed", "detail": traceback.format_exc(limit=3)[-800:]})
print("AURA_TESTS " + json.dumps(results))
"""


class SkillError(ValueError):
    pass


def load_manifest(folder: Path) -> dict[str, Any]:
    """Validate a skill folder's structure and manifest; raise SkillError explaining any problem."""
    if not (folder / "skill.toml").is_file():
        raise SkillError("missing skill.toml")
    for required in ("skill.py", "test_skill.py"):
        if not (folder / required).is_file():
            raise SkillError(f"missing {required}")
    files = [p for p in folder.rglob("*") if "__pycache__" not in p.parts]
    if any(p.is_symlink() for p in files):
        raise SkillError("skills may not contain symbolic links")
    if sum(p.stat().st_size for p in files if p.is_file()) > MAX_BYTES:
        raise SkillError(f"the skill is larger than {MAX_BYTES // 1024} KB")
    try:
        m = tomllib.loads((folder / "skill.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise SkillError(f"skill.toml is not valid TOML: {e}") from e

    name = m.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise SkillError("name must be lowercase letters, digits and underscores (2-40 characters)")
    if name != folder.name:
        raise SkillError(f"name '{name}' must match the folder name '{folder.name}'")
    if not isinstance(m.get("version"), str) or not re.match(r"^\d+\.\d+\.\d+$", m["version"]):
        raise SkillError("version must look like 1.0.0")
    if not isinstance(m.get("purpose"), str) or not 10 <= len(m["purpose"]) <= 300:
        raise SkillError("purpose must be a sentence of 10-300 characters")
    inputs = m.get("inputs", {})
    if not isinstance(inputs, dict) or len(inputs) > 8 or not all(
        isinstance(k, str) and k.isidentifier() and isinstance(v, str) for k, v in inputs.items()
    ):
        raise SkillError("inputs must be up to 8 'name = \"description\"' entries")
    perms = m.get("permissions", {})
    if perms.get("network", False) is not False:
        raise SkillError("network access is not allowed for skills (Sprint 5 policy)")
    if perms.get("vault", "none") not in ("read", "none"):
        raise SkillError("vault permission must be 'read' or 'none'")
    deps = m.get("dependencies", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) and PIN.match(d) for d in deps):
        raise SkillError("every dependency must be pinned exactly, like 'pypdf==7.1.1'")
    tree = ast.parse((folder / "skill.py").read_text(encoding="utf-8"))
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        raise SkillError("skill.py must define run(args)")
    # A HOL session is deliberately declarative.  It may select one of Aura's fixed runners and
    # provide a task brief, but it cannot smuggle a shell command or a host path through a skill
    # review.  This keeps the source checkout outside the HOL's authority.
    hol = m.get("hol")
    if hol is not None:
        if not isinstance(hol, dict):
            raise SkillError("[hol] must be a table")
        if set(hol) != {"runner", "prompt"}:
            raise SkillError("[hol] permits only runner and prompt")
        if hol.get("runner") != "aura_session_review":
            raise SkillError("[hol].runner must be 'aura_session_review'")
        prompt = hol.get("prompt")
        if not isinstance(prompt, str) or not 10 <= len(prompt) <= 12_000:
            raise SkillError("[hol].prompt must be 10-12000 characters")
    return {"name": name, "version": m["version"], "purpose": m["purpose"], "inputs": inputs,
            "permissions": {"network": False, "vault": perms.get("vault", "none")}, "dependencies": deps,
            "hol": hol}  # fmt: skip


def content_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def image_for(name: str, digest: str) -> str:
    """Legacy per-skill image tag for approvals created before shared dependency bundles."""
    return f"aura-skill-{name.replace('_', '-')}:{digest[:12]}"


def dependency_image_for(pins: list[tuple[str, str]]) -> str:
    """The reusable image for one exact, resolved dependency set.

    Skills are mounted read-only at run time, so their code does not belong in an image.  This tag
    is based solely on the resolved packages, allowing two approved skills with the same library
    set to share one image.  It is deliberately separate from the skill content hash: editing a
    skill without changing its dependencies does not create another package copy.
    """
    normalized = "\n".join(f"{name}=={version}" for name, version in sorted(set(pins)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"aura-deps:{digest}"


def dockerfile(pins: list[tuple[str, str]]) -> str:
    """An offline runtime image with exact, ready-built packages.

    One deterministically ordered install layer per package lets Docker share the package layer
    between dependency bundles.  ``--no-deps`` is safe because ``pins`` contains the complete
    transitive resolution from the Governor, and keeps pip from changing that reviewed set.
    """
    lines = [f"FROM {BASE_IMAGE}"]
    for name, version in sorted(set(pins)):
        lines.append(f"RUN pip install --no-cache-dir --only-binary=:all: --no-deps {name}=={version}")
    return "\n".join(lines) + "\n"


@dataclass
class Skill:
    name: str
    path: Path
    status: str  # "approved" | "changed" (edited since approval) | "unapproved" | "invalid"
    manifest: dict[str, Any] | None = None
    digest: str = ""
    enabled: bool = False  # the user's switch
    error: str | None = None
    flag: dict[str, Any] | None = None  # set by the Continuous Audit when the skill turns red

    @property
    def loaded(self) -> bool:
        """Available to Aura: approved, unchanged, switched on, and not flagged."""
        return self.status == "approved" and self.enabled and not self.flag

    def public(self) -> dict[str, Any]:
        m = self.manifest or {}
        return {"name": self.name, "status": self.status, "enabled": self.enabled, "loaded": self.loaded,
                "error": self.error, "flag": self.flag,
                "version": m.get("version", ""), "purpose": m.get("purpose", ""),
                "permissions": m.get("permissions", {}), "dependencies": m.get("dependencies", [])}  # fmt: skip


class SkillManager:
    def __init__(self, skills_dir: Path, data_dir: Path, sandbox: Sandbox, audit: AuditLog):
        self.dir = skills_dir
        self.approvals_path = data_dir / "skills.json"
        self.sandbox = sandbox
        self.audit = audit
        self._lock = threading.RLock()  # the server and the Continuous Audit both update approvals

    # ── approvals (outside the repository) ─────────────────────────────────────────────────

    def approvals(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.approvals_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, approvals: dict[str, dict[str, Any]]) -> None:
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.approvals_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.approvals_path)  # atomic: never a half-written approvals file

    def _update(self, name: str, change: Callable[[dict[str, Any]], None]) -> bool:
        with self._lock:
            approvals = self.approvals()
            if name not in approvals:
                return False
            change(approvals[name])
            self._save(approvals)
            return True

    def approve(self, name: str, digest: str, version: str, image: str,
                packages: list[tuple[str, str]] | None = None) -> None:  # fmt: skip
        """Record the user's approval of this exact content, and the exact packages in its image
        (direct and indirect), which the Continuous Audit re-checks."""
        with self._lock:
            approvals = self.approvals()
            approvals[name] = {"digest": digest, "version": version, "image": image, "enabled": True,
                               "packages": [list(p) for p in packages or []],
                               "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}  # fmt: skip
            self._save(approvals)
        self.audit.record("skill_approved", skill=name, version=version, digest=digest[:12])

    def set_enabled(self, name: str, enabled: bool) -> bool:
        if enabled and (self.approvals().get(name) or {}).get("flag"):
            raise SkillError(f"'{name}' was flagged by the Continuous Audit and can't be switched on")
        if not self._update(name, lambda a: a.update(enabled=enabled)):
            return False
        self.audit.record("skill_enabled" if enabled else "skill_disabled", skill=name)
        return True

    def flag(self, name: str, kind: str, findings: list[dict[str, Any]]) -> None:
        """Red at a re-check: unloaded at once (red blocks, no override) until it's cleared or removed."""
        since = datetime.now(timezone.utc).isoformat(timespec="seconds")

        def change(a: dict[str, Any]) -> None:
            old = a.get("flag") or {}
            a["flag"] = {"since": old.get("since", since), "kind": kind, "findings": findings,
                         "acknowledged": old.get("acknowledged", False) and old.get("findings") == findings}  # fmt: skip

        self._update(name, change)

    def clear_flag(self, name: str) -> None:
        self._update(name, lambda a: a.pop("flag", None))

    def acknowledge(self, name: str) -> bool:
        """The user chose to keep a flagged skill (disabled): stop alerting about it."""
        if not (self.approvals().get(name) or {}).get("flag"):
            return False
        return self._update(name, lambda a: a["flag"].update(acknowledged=True))

    def forget(self, name: str) -> None:
        with self._lock:
            approvals = self.approvals()
            if approvals.pop(name, None) is not None:
                self._save(approvals)

    def image_is_referenced_elsewhere(self, image: str, excluding: str) -> bool:
        """Whether another approved skill still needs this exact dependency image."""
        return any(
            skill != excluding and approval.get("image") == image
            for skill, approval in self.approvals().items()
        )

    # ── discovery ──────────────────────────────────────────────────────────────────────────

    def scan(self) -> list[Skill]:
        approvals = self.approvals()
        skills = []
        for folder in sorted(p for p in self.dir.glob("*") if p.is_dir() and not p.name.startswith((".", "_"))):
            try:
                manifest = load_manifest(folder)
            except (SkillError, OSError, SyntaxError) as e:
                skills.append(Skill(folder.name, folder, "invalid", error=str(e)))
                continue
            digest = content_hash(folder)
            approved = approvals.get(folder.name)
            if approved is None:
                status = "unapproved"
            elif approved["digest"] != digest:
                status = "changed"  # edited after approval: not loaded until re-audited
            else:
                status = "approved"
            skills.append(Skill(folder.name, folder, status, manifest, digest,
                                enabled=status == "approved" and approved.get("enabled", True),
                                flag=(approved or {}).get("flag")))  # fmt: skip
        return skills

    def loaded(self) -> list[Skill]:
        return [s for s in self.scan() if s.loaded]

    def tools(self, emit_status: Callable[[str], None] = lambda s: None,
              loaded: list[Skill] | None = None) -> list[BaseTool]:  # fmt: skip
        return [self._tool(s, emit_status) for s in (self.loaded() if loaded is None else loaded)]

    def capabilities(self, loaded: list[Skill] | None = None) -> list[Capability]:
        """The Capability Registry rows of the skills `tools()` offers, from their manifests."""
        return [skill_capability(s.name, s.manifest or {}) for s in (self.loaded() if loaded is None else loaded)]

    # ── running inside the sandbox ─────────────────────────────────────────────────────────

    def _tool(self, skill: Skill, emit_status: Callable[[str], None]) -> BaseTool:
        m = skill.manifest or {}
        fields = {k: (str, Field(description=v)) for k, v in m["inputs"].items()}
        Args = create_model(f"{skill.name}_args", **fields)  # noqa: N806
        image = self.approvals()[skill.name]["image"]

        def call(**kwargs: Any) -> str:
            if (self.approvals().get(skill.name) or {}).get("flag"):  # flagged mid-turn: never run
                return f"The {skill.name} skill was disabled by the Continuous Audit (a new security problem). It didn't run."
            try:
                result = self.sandbox.run(CALL.format(args=json.dumps(kwargs)), image=image,
                                          mounts=((skill.path, "/skill"),), vault=m["permissions"]["vault"] == "read")  # fmt: skip
            except SandboxUnavailable as e:
                return f"The skill couldn't run: {e}"
            self.audit.record("skill_run", skill=skill.name, exit_code=result.exit_code)
            return format_result(result, self.sandbox.timeout)

        return StructuredTool.from_function(
            func=call, name=f"skill_{skill.name}", args_schema=Args,
            description=f"{m['purpose']} (an installed skill; runs in the sandbox)",
        )  # fmt: skip

    def run_tests(self, folder: Path, image: str, vault: bool) -> list[dict[str, str]]:
        """The skill's own tests, inside its own image."""
        result = self.sandbox.run(TESTS, image=image, mounts=((folder, "/skill"),), vault=vault)
        for line in result.stdout.splitlines():
            if line.startswith("AURA_TESTS "):
                return json.loads(line[len("AURA_TESTS "):])
        detail = (result.stderr or result.stdout or "no output")[-800:]
        return [{"name": "test_skill.py", "status": "failed", "detail": "timed out" if result.timed_out else detail}]
