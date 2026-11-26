"""Vault-only Human-Out-of-Loop session registry and restore points.

HOL sessions are approved Aura skills with a small declarative ``[hol]`` block.  They are not
shell scripts: a shell running with the user's macOS authority could ``cd`` out of the Vault and
would make the promise that HOL never changes the checkout impossible to keep.  The native client
uses these records to prepare a bounded task prompt in its terminal-style UI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import tomllib
import uuid
from io import BytesIO
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog
from aura.config import Settings
from aura.skills import SkillManager

MAX_SAVEPOINTS = 3
SAVEPOINT_ID = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{8}$")
BUILTIN_NAME = "aura_session_review"
BUILTIN_MANIFEST = '''name = "aura_session_review"
version = "1.1.0"
purpose = "Prepare a bounded, phase-based Aura development review inside the Vault copy."
inputs = {}
dependencies = []

[permissions]
network = false
vault = "none"

[hol]
runner = "aura_session_review"
prompt = "Run a bounded senior-developer review. Work only in the Vault Aura copy; never modify the source checkout. At the start of each user request, classify the work and publish a short phase plan. Use only the relevant upstream Aura skills: wayfinder for an ambiguous or multi-step objective; grill_with_docs for document or repository context; domain_modeling for terminology or durable boundaries; diagnosing_bugs for a defect or performance regression; synthesize or pdf_text only when the requested evidence is in Vault notes or a PDF; to_tickets and implement_spec only after a plan is approved; handoff or retro only at a session boundary; unslop only for user-requested prose review. Do not run every skill and never claim an Aura skill ran unless its result is supplied in this conversation: this isolated Developer has no host skill tools. If a missing skill result is needed, name the exact skill, inputs, and decision it will inform, then wait for the user or Aura to provide it; do not infer, paraphrase, or reconstruct the missing result. Before edits, confirm scope, savepoint, and acceptance evidence. Implement one small vertical slice, preserve per-file approval, verify with focused tests or a build, and report changed files, evidence, and residual risk."
'''
BUILTIN_SKILL = '''def run(args):
    return "This declarative HOL session is prepared by Aura's native terminal."
'''
BUILTIN_TEST = '''def test_declarative_session():
    from skill import run
    assert "HOL" in run({})
'''


class HolError(ValueError):
    pass


@dataclass(frozen=True)
class Savepoint:
    id: str
    created: str
    label: str


class HolManager:
    """Maintains only Vault-local session metadata and snapshots of its Aura project copy."""

    def __init__(self, settings: Settings, skills: SkillManager, audit: AuditLog):
        self.settings, self.skills, self.audit = settings, skills, audit
        self.root = settings.project_sandbox_dir
        # Savepoint contents are user project files, so keep them under Aura_Vault as well.
        # Application Support retains no HOL project copy and the source checkout is read-only.
        self.savepoints_dir = settings.vault_dir / ".aura-hol" / "savepoints"
        self.index_path = self.savepoints_dir / "index.json"
        # FastAPI handlers may arrive concurrently. Snapshot rotation is one logical transaction:
        # a second request must not observe or prune a first request's half-finished staging tree.
        self._lock = threading.RLock()

    def ensure_builtin_candidate(self) -> None:
        """Offer the standard session to the Workshop once, without bypassing its approval."""
        with self._lock:
            live = self.settings.skills_dir / BUILTIN_NAME
            inbox = self.settings.skills_inbox / BUILTIN_NAME
            # A stock, older built-in needs a new Workshop candidate so users can review the
            # workflow update.  Never replace an approved live skill, and never overwrite an
            # Inbox skill that could have been edited by the user.
            if live.exists() and not self._is_stock_builtin(live):
                return
            if inbox.exists() and not self._is_stock_builtin(inbox):
                return
            if live.exists() and self._is_current_builtin(live):
                return
            if inbox.exists():
                shutil.rmtree(inbox)
            inbox.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".hol-session-", dir=inbox.parent))
            try:
                (staging / "skill.toml").write_text(BUILTIN_MANIFEST, encoding="utf-8")
                (staging / "skill.py").write_text(BUILTIN_SKILL, encoding="utf-8")
                (staging / "test_skill.py").write_text(BUILTIN_TEST, encoding="utf-8")
                staging.replace(inbox)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            self.audit.record("hol_session_offered", skill=BUILTIN_NAME, path=str(inbox))

    @staticmethod
    def _is_current_builtin(folder: Path) -> bool:
        """Whether a folder is exactly the current generated built-in session."""
        try:
            return ((folder / "skill.toml").read_text(encoding="utf-8") == BUILTIN_MANIFEST
                    and (folder / "skill.py").read_text(encoding="utf-8") == BUILTIN_SKILL
                    and (folder / "test_skill.py").read_text(encoding="utf-8") == BUILTIN_TEST)
        except OSError:
            return False

    @staticmethod
    def _is_stock_builtin(folder: Path) -> bool:
        """Recognise only the prior generated session, never a user-authored session."""
        try:
            manifest = tomllib.loads((folder / "skill.toml").read_text(encoding="utf-8"))
            return (manifest.get("name") == BUILTIN_NAME
                    and manifest.get("version") == "1.0.0"
                    and manifest.get("hol", {}).get("runner") == "aura_session_review"
                    and (folder / "skill.py").read_text(encoding="utf-8") == BUILTIN_SKILL
                    and (folder / "test_skill.py").read_text(encoding="utf-8") == BUILTIN_TEST)
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            return False

    def sessions(self) -> list[dict[str, Any]]:
        """Only an approved, enabled, unflagged skill can become a runnable HOL session."""
        out: list[dict[str, Any]] = []
        for skill in self.skills.scan():
            hol = (skill.manifest or {}).get("hol")
            if not skill.loaded or not isinstance(hol, dict):
                continue
            out.append({"name": skill.name, "purpose": (skill.manifest or {}).get("purpose", ""),
                        "runner": hol["runner"], "prompt": hol["prompt"]})
        return out

    def public(self, *, model: dict[str, str], effort: str | None) -> dict[str, Any]:
        return {"vault_project": str(self._project()), "sessions": self.sessions(),
                "savepoints": [asdict(s) for s in self._load_savepoints()],
                "defaults": {"model": model, "effort": effort},
                "notice": "HOL runs only approved declarative sessions. It never launches an unrestricted host shell."}

    def start(self, name: str, *, model: dict[str, str], effort: str | None) -> dict[str, Any]:
        session = next((s for s in self.sessions() if s["name"] == name), None)
        if session is None:
            raise HolError("This HOL session is not approved and enabled in the Workshop.")
        self.audit.record("hol_session_started", skill=name, vault_project=str(self._project()),
                          model=model, effort=effort)
        return {"session": name, "working_directory": str(self._project()), "model": model,
                "effort": effort, "prompt": session["prompt"],
                "output": ["Approved session started in a disposable, no-network container.",
                           "Only Aura_Vault is mounted; the source checkout is not visible.",
                           "Container shell working directory: /vault/Aura."]}

    def create_savepoint(self, label: str) -> dict[str, Any]:
        with self._lock:
            return self._create_savepoint(label)

    def _create_savepoint(self, label: str) -> dict[str, Any]:
        source = self._project()
        self.savepoints_dir.mkdir(parents=True, exist_ok=True)
        # This directory is Aura-owned. Clear an interrupted copy or an index-orphan before
        # creating the next point so the on-disk retention bound is real, not merely cosmetic.
        points = self._normalise_savepoints()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        record = Savepoint(id=f"{stamp}-{uuid.uuid4().hex[:8]}", created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           label=label.strip()[:120] or "Before HOL changes")
        target = self.savepoints_dir / record.id
        if target.exists():
            raise HolError("A savepoint is already being created; try again.")
        staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self.savepoints_dir))
        try:
            # A Vault copy containing links could make an apparently local restore write elsewhere.
            self._copy_tree(source, staging / "Aura")
            (staging / "Aura").replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        points.append(record)
        while len(points) > MAX_SAVEPOINTS:
            old = points.pop(0)
            shutil.rmtree(self.savepoints_dir / old.id, ignore_errors=True)
        self._save_savepoints(points)
        self.audit.record("hol_savepoint_created", id=record.id, label=record.label, vault_project=str(source))
        return asdict(record)

    def restore_savepoint(self, savepoint_id: str) -> dict[str, Any]:
        with self._lock:
            point = next((p for p in self._load_savepoints() if p.id == savepoint_id), None)
            if point is None:
                raise HolError("No such HOL savepoint.")
            source = self.savepoints_dir / point.id
            self._replace_project(source)
            self.audit.record("hol_savepoint_restored", id=point.id, vault_project=str(self._project()))
            return asdict(point)

    def restore_original(self) -> None:
        """Replace only the Vault project with the source checkout's clean Git HEAD tree."""
        with self._lock:
            project = self._project()
            try:
                archive = subprocess.run(
                    ["git", "-C", str(self.settings.project_dir), "archive", "--format=tar", "HEAD"],
                    capture_output=True, check=True, timeout=20,
                ).stdout
            except (OSError, subprocess.SubprocessError) as exc:
                raise HolError("Aura could not read the source checkout's clean Git HEAD.") from exc
            parent = project.parent
            staging = Path(tempfile.mkdtemp(prefix=".original-", dir=parent))
            extracted = staging / project.name
            try:
                extracted.mkdir()
                with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tf:
                    for member in tf.getmembers():
                        path = Path(member.name)
                        if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                            raise HolError("Git HEAD contained an unsafe path.")
                        target = extracted / path
                        if not target.is_relative_to(extracted):
                            raise HolError("Git HEAD contained an unsafe path.")
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            handle = tf.extractfile(member)
                            if handle is None:
                                raise HolError("Git HEAD contained an unreadable file.")
                            target.write_bytes(handle.read())
                self._replace_project(extracted, permitted_parent=staging)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            self.audit.record("hol_original_restored", vault_project=str(project), git_ref="HEAD")

    def _project(self) -> Path:
        root = self.root.expanduser().resolve()
        vault = self.settings.vault_dir.expanduser().resolve()
        if root == vault or not root.is_relative_to(vault):
            raise HolError("The HOL project must be inside Aura_Vault.")
        if not root.is_dir() or root.is_symlink():
            raise HolError("The Vault Aura copy is unavailable or unsafe.")
        return root

    def _replace_project(self, source: Path, permitted_parent: Path | None = None) -> None:
        source = source.resolve()
        permitted_parent = permitted_parent or self.savepoints_dir
        if source.parent.resolve() != permitted_parent.resolve() or not source.is_dir() or source.is_symlink():
            raise HolError("The requested savepoint is unsafe.")
        project = self._project()
        parent = project.parent
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=parent))
        replacement = staging / project.name
        try:
            self._copy_tree(source, replacement)
            retired = parent / f".{project.name}.retired-{os.getpid()}"
            if retired.exists():
                shutil.rmtree(retired)
            project.replace(retired)
            try:
                replacement.replace(project)
            except Exception:
                retired.replace(project)
                raise
            shutil.rmtree(retired, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _copy_tree(source: Path, destination: Path) -> None:
        if source.is_symlink():
            raise HolError("Symbolic links are not allowed in a HOL project snapshot.")
        for current, dirs, files in os.walk(source, followlinks=False):
            current_path = Path(current)
            if current_path.is_symlink():
                raise HolError("Symbolic links are not allowed in a HOL project snapshot.")
            relative = current_path.relative_to(source)
            dest = destination / relative
            dest.mkdir(parents=True, exist_ok=True)
            for name in dirs + files:
                if (current_path / name).is_symlink():
                    raise HolError("Symbolic links are not allowed in a HOL project snapshot.")
            for name in files:
                shutil.copy2(current_path / name, dest / name)

    def _load_savepoints(self) -> list[Savepoint]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            points = [Savepoint(**item) for item in data]
            return [point for point in points if SAVEPOINT_ID.fullmatch(point.id)
                    and (self.savepoints_dir / point.id).is_dir()]
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def _normalise_savepoints(self) -> list[Savepoint]:
        """Keep the internal snapshot directory and its index in lockstep at three points."""
        points = self._load_savepoints()
        valid = {point.id for point in points}
        if self.savepoints_dir.is_dir():
            for child in self.savepoints_dir.iterdir():
                if child.is_dir() and child.name not in valid:
                    shutil.rmtree(child, ignore_errors=True)
        while len(points) > MAX_SAVEPOINTS:
            old = points.pop(0)
            shutil.rmtree(self.savepoints_dir / old.id, ignore_errors=True)
        self._save_savepoints(points)
        return points

    def _save_savepoints(self, points: list[Savepoint]) -> None:
        self.savepoints_dir.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(json.dumps([asdict(p) for p in points], indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.index_path)
