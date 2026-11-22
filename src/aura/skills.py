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
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from aura.audit import AuditLog
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
    return {"name": name, "version": m["version"], "purpose": m["purpose"], "inputs": inputs,
            "permissions": {"network": False, "vault": perms.get("vault", "none")}, "dependencies": deps}  # fmt: skip


def content_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def image_for(name: str, digest: str) -> str:
    return f"aura-skill-{name.replace('_', '-')}:{digest[:12]}"


def dockerfile(pins: list[tuple[str, str]]) -> str:
    """The skill's image: the base sandbox plus its exact resolved packages, ready-built only
    (--only-binary: no package's own setup code ever runs)."""
    lines = [f"FROM {BASE_IMAGE}"]
    if pins:
        packages = " ".join(f"{n}=={v}" for n, v in pins)
        lines.append(f"RUN pip install --no-cache-dir --only-binary=:all: --no-deps {packages}")
    return "\n".join(lines) + "\n"


@dataclass
class Skill:
    name: str
    path: Path
    status: str  # "approved" | "changed" (edited since approval) | "unapproved" | "invalid"
    manifest: dict[str, Any] | None = None
    digest: str = ""
    enabled: bool = False
    error: str | None = None

    def public(self) -> dict[str, Any]:
        m = self.manifest or {}
        return {"name": self.name, "status": self.status, "enabled": self.enabled, "error": self.error,
                "version": m.get("version", ""), "purpose": m.get("purpose", ""),
                "permissions": m.get("permissions", {}), "dependencies": m.get("dependencies", [])}  # fmt: skip


class SkillManager:
    def __init__(self, skills_dir: Path, data_dir: Path, sandbox: Sandbox, audit: AuditLog):
        self.dir = skills_dir
        self.approvals_path = data_dir / "skills.json"
        self.sandbox = sandbox
        self.audit = audit

    # ── approvals (outside the repository) ─────────────────────────────────────────────────

    def approvals(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.approvals_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, approvals: dict[str, dict[str, Any]]) -> None:
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        self.approvals_path.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")

    def approve(self, name: str, digest: str, version: str, image: str) -> None:
        approvals = self.approvals()
        approvals[name] = {"digest": digest, "version": version, "image": image, "enabled": True,
                           "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}  # fmt: skip
        self._save(approvals)
        self.audit.record("skill_approved", skill=name, version=version, digest=digest[:12])

    def set_enabled(self, name: str, enabled: bool) -> bool:
        approvals = self.approvals()
        if name not in approvals:
            return False
        approvals[name]["enabled"] = enabled
        self._save(approvals)
        self.audit.record("skill_enabled" if enabled else "skill_disabled", skill=name)
        return True

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
                                enabled=status == "approved" and approved.get("enabled", True)))  # fmt: skip
        return skills

    def tools(self, emit_status: Callable[[str], None] = lambda s: None) -> list[BaseTool]:
        return [self._tool(s, emit_status) for s in self.scan() if s.status == "approved" and s.enabled]

    # ── running inside the sandbox ─────────────────────────────────────────────────────────

    def _tool(self, skill: Skill, emit_status: Callable[[str], None]) -> BaseTool:
        m = skill.manifest or {}
        fields = {k: (str, Field(description=v)) for k, v in m["inputs"].items()}
        Args = create_model(f"{skill.name}_args", **fields)  # noqa: N806
        image = self.approvals()[skill.name]["image"]

        def call(**kwargs: Any) -> str:
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
