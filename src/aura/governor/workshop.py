"""The Workshop: shadow-project staging, the Governor's audit, and Approve & Deploy.

A proposed skill is copied into a throwaway git worktree (a "shadow project") and audited there.
Nothing reaches the live repository until every gate passes and the user approves on the Audit
Card. Deploy commits only the skill's own folder; Aura's core is never written.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog
from aura.config import Settings
from aura.governor import AuditReport, Finding
from aura.governor.provenance import check_package, resolve
from aura.governor.scan import scan_skill
from aura.skills import SkillError, SkillManager, content_hash, dockerfile, image_for, load_manifest
from aura.tools.sandbox import Sandbox, SandboxUnavailable

# Aura's core (the Integrity Rule): a staged change touching any of these is rejected outright.
PROTECTED = [
    "src/aura/constitution.md", "src/aura/brain.py", "src/aura/persona.py", "src/aura/identity.py",
    "src/aura/server.py", "src/aura/middleware.py", "src/aura/skills.py", "src/aura/governor/",
    "src/aura/tools/", "src/aura/config.py", "tests/", "pyproject.toml", "uv.lock",
]  # fmt: skip


class WorkshopError(RuntimeError):
    pass


@dataclass
class Task:
    id: str
    skill: str
    stage: str = "staging"  # staging -> auditing -> awaiting -> deployed | rejected | failed
    message: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    report: AuditReport | None = None
    worktree: Path | None = None
    branch: str = ""
    base: str = ""
    digest: str = ""

    def public(self) -> dict[str, Any]:
        r = self.report
        return {"id": self.id, "skill": self.skill, "stage": self.stage, "message": self.message,
                "created": self.created, "report": r.to_dict() if r else None}  # fmt: skip


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise WorkshopError(f"git {args[0]} failed: {out.stderr.strip()[-300:]}")
    return out.stdout.strip()


class Workshop:
    def __init__(self, settings: Settings, manager: SkillManager, sandbox: Sandbox, audit: AuditLog,
                 run_suite: bool = True):  # fmt: skip
        self.settings = settings
        self.manager = manager
        self.sandbox = sandbox
        self.audit = audit
        self.run_suite = run_suite
        self.on_deployed: Callable[[], None] = lambda: None
        self.tasks: dict[str, Task] = {}
        self._one_at_a_time = threading.Lock()

    # ── the inbox: skills waiting to be proposed ───────────────────────────────────────────

    def inbox(self) -> list[dict[str, Any]]:
        inbox = self.settings.skills_inbox
        if not inbox.is_dir():
            return []
        items = []
        for folder in sorted(p for p in inbox.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                m = load_manifest(folder)
                items.append({"name": folder.name, "version": m["version"], "purpose": m["purpose"], "error": None})
            except (SkillError, OSError, SyntaxError) as e:
                items.append({"name": folder.name, "version": "", "purpose": "", "error": str(e)})
        return items

    # ── propose -> stage -> audit ──────────────────────────────────────────────────────────

    def propose(self, name: str, wait: bool = False) -> Task:
        source = self.settings.skills_inbox / name
        if not source.is_dir():
            raise WorkshopError(f"there is no skill called '{name}' in the Skills Inbox")
        if any(t.skill == name and t.stage in ("staging", "auditing", "awaiting") for t in self.tasks.values()):
            raise WorkshopError(f"'{name}' is already being reviewed")
        task = Task(id=uuid.uuid4().hex[:8], skill=name)
        self.tasks[task.id] = task
        self.audit.record("skill_proposed", skill=name, task=task.id)
        if wait:
            self._run(task)
        else:
            threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return task

    def _run(self, task: Task) -> None:
        with self._one_at_a_time:
            try:
                self._stage(task)
                task.stage, task.message = "auditing", "Running the Security Governor's checks…"
                task.report = self._audit(task)
                task.stage = "awaiting"
                task.message = ("Blocked: see the findings." if task.report.blocked
                                else "Ready for your review.")  # fmt: skip
                self.audit.record("skill_audited", skill=task.skill, task=task.id, risk=task.report.risk,
                                  blocked=task.report.blocked)  # fmt: skip
            except Exception as e:  # noqa: BLE001: any failure ends the task safely
                task.stage, task.message = "failed", f"The audit couldn't finish: {e}"
                self._cleanup(task)

    def _stage(self, task: Task) -> None:
        repo, name = self.settings.project_dir, task.skill
        task.base = git(repo, "rev-parse", "HEAD")
        task.branch = f"aura/skill-{name}-{task.id}"
        task.worktree = self.settings.staging_dir / task.id
        task.worktree.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-q", "-b", task.branch, str(task.worktree), task.base)
        dest = task.worktree / "skills" / name
        if dest.exists():
            shutil.rmtree(dest)  # an update replaces the previous version
        shutil.copytree(self.settings.skills_inbox / name, dest, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))  # fmt: skip
        git(task.worktree, "add", "-A", f"skills/{name}")
        git(task.worktree, "commit", "-q", "--allow-empty", "-m", f"Stage skill {name} for audit")

    def _audit(self, task: Task) -> AuditReport:
        wt, name = task.worktree, task.skill
        assert wt is not None
        report = AuditReport(skill=name)
        add = report.findings.append

        changed = [p for p in git(wt, "diff", "--name-only", task.base, "HEAD").splitlines() if p]
        outside = [p for p in changed if not p.startswith(f"skills/{name}/")]
        if outside:
            add(Finding("write-scope", "reject", "changes outside the skill's own folder", ", ".join(outside[:10])))
            return report
        add(Finding("write-scope", "pass", f"only skills/{name}/ changed ({len(changed)} files)"))
        protected = [p for p in git(wt, "diff", "--name-only", task.base, "HEAD", "--", *PROTECTED).splitlines() if p]
        if protected:
            add(Finding("protected", "reject", "touches Aura's protected core", ", ".join(protected)))
            return report
        add(Finding("protected", "pass", "the constitution, core and Governor are untouched"))

        folder = wt / "skills" / name
        try:
            manifest = load_manifest(folder)
        except (SkillError, SyntaxError) as e:
            add(Finding("manifest", "reject", f"invalid skill: {e}"))
            return report
        report.version, report.purpose = manifest["version"], manifest["purpose"]
        add(Finding("manifest", "pass", f"valid manifest; vault access: {manifest['permissions']['vault']}; network: none"))

        report.findings += scan_skill(folder)

        try:
            pins = resolve(manifest["dependencies"])
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            add(Finding("provenance", "red", "dependencies could not be resolved from ready-built packages", str(e)[:300]))
            return report
        if not pins:
            add(Finding("provenance", "pass", "no third-party packages"))
        for pkg, version in pins:
            try:
                report.findings += check_package(pkg, version).findings
            except Exception as e:  # noqa: BLE001: unknown can only make the verdict worse
                add(Finding("provenance", "red", f"{pkg} {version} could not be verified", str(e)[:200]))
        if report.risk == "red":
            return report  # never build or run code the rules have already rejected

        task.digest = content_hash(folder)
        image = image_for(name, task.digest)
        try:
            if not self.sandbox.image_exists(image):
                self.sandbox.build_image(image, dockerfile(pins))
        except SandboxUnavailable as e:
            add(Finding("tests", "red", "the skill's sandbox image couldn't be built", str(e)[:300]))
            return report
        report.image = image
        report.tests = self.manager.run_tests(folder, image, vault=manifest["permissions"]["vault"] == "read")
        if self.run_suite:
            report.suite = self._suite(wt)
        return report

    SUITE = ("pytest", "-q", "-p", "no:cacheprovider", "-m", "not live", "tests", "--deselect", "tests/test_live.py")

    def _suite(self, worktree: Path) -> dict[str, Any]:
        """Aura's existing offline test suite, run in the shadow project. Offline means no `live`
        tests: the gate mustn't depend on the network, Docker or the model."""
        out = subprocess.run(
            ["uv", "run", "--frozen", "--project", str(worktree), *self.SUITE],
            cwd=worktree, capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        return self.parse_suite(out.returncode, out.stdout, out.stderr)

    @staticmethod
    def parse_suite(returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
        """Fail closed: pytest's exit code decides. A run that crashed, was cut off or printed no
        summary counts as failed, never as "0 failed"."""
        lines = stdout.strip().splitlines()
        summary = next((ln for ln in reversed(lines) if re.search(r"\d+ (passed|failed|error)", ln)), "")
        count = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}", summary)) else 0  # noqa: E731
        failed = count("failed") + count("error")
        if returncode != 0:
            failed = max(failed, 1)
            if not summary:
                summary = "the test suite did not complete: " + ((lines[-1] if lines else "") or stderr.strip()[-200:])
        return {"passed": count("passed"), "failed": failed, "summary": summary, "exit_code": returncode}

    # ── the user's decision ────────────────────────────────────────────────────────────────

    def decide(self, task_id: str, approve: bool) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise WorkshopError("no such task")
        if task.stage != "awaiting":
            raise WorkshopError(f"this task is {task.stage}, not awaiting a decision")
        if not approve:
            self._cleanup(task)
            task.stage, task.message = "rejected", "Rejected. Nothing was installed."
            self.audit.record("skill_rejected", skill=task.skill, task=task.id)
            return task
        assert task.report is not None and task.worktree is not None
        if task.report.blocked:
            raise WorkshopError("blocked skills can't be deployed")
        self._deploy(task)
        return task

    def _deploy(self, task: Task) -> None:
        name, repo = task.skill, self.settings.project_dir
        assert task.worktree is not None and task.report is not None
        live = self.settings.skills_dir / name
        if live.exists():
            shutil.rmtree(live)
        shutil.copytree(task.worktree / "skills" / name, live)
        git(repo, "add", "-A", f"skills/{name}")
        message = (f"skill: add {name} {task.report.version} (approved via the Audit Card)\n\n"
                   f"Risk: {task.report.risk}. Audit task {task.id}.")  # fmt: skip
        git(repo, "commit", "-q", "-m", message, "--", f"skills/{name}")
        self.manager.approve(name, task.digest, task.report.version, task.report.image)
        self._cleanup(task)
        self.on_deployed()  # hot reload: the new tool is available on the next turn
        checks = self.manager.run_tests(live, task.report.image, vault=True)
        ok = all(c["status"] == "passed" for c in checks)
        task.stage = "deployed"
        task.message = f"Deployed. The skill's tests pass in the live registry ({len(checks)}/{len(checks)})." if ok else (
            "Deployed, but its tests failed in the live registry; it has been disabled.")
        if not ok:
            self.manager.set_enabled(name, False)
            self.on_deployed()
        self.audit.record("skill_deployed", skill=name, task=task.id, verified=ok)

    def _cleanup(self, task: Task) -> None:
        repo = self.settings.project_dir
        if task.worktree and task.worktree.exists():
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(task.worktree)],
                           capture_output=True)  # fmt: skip
        if task.branch:
            subprocess.run(["git", "-C", str(repo), "branch", "-D", task.branch], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)

    # ── the side-by-side diff ──────────────────────────────────────────────────────────────

    def diff(self, task_id: str) -> list[dict[str, Any]]:
        task = self.tasks.get(task_id)
        if task is None or task.worktree is None or not task.worktree.exists():
            return []
        new_root = task.worktree / "skills" / task.skill
        old_root = self.settings.skills_dir / task.skill
        rels = sorted({p.relative_to(new_root).as_posix() for p in new_root.rglob("*") if p.is_file()}
                      | ({p.relative_to(old_root).as_posix() for p in old_root.rglob("*") if p.is_file()}
                         if old_root.exists() else set()))  # fmt: skip
        return [self._file_diff(rel, old_root / rel, new_root / rel) for rel in rels if "__pycache__" not in rel]

    @staticmethod
    def _file_diff(rel: str, old: Path, new: Path) -> dict[str, Any]:
        def lines(p: Path) -> list[str] | None:
            if not p.exists():
                return []
            data = p.read_bytes()
            return None if b"\0" in data[:4096] else data.decode("utf-8", errors="replace").splitlines()

        a, b = lines(old), lines(new)
        if a is None or b is None:
            return {"file": rel, "status": "binary", "rows": []}
        rows = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                rows += [{"left": a[i], "right": b[j], "kind": "same"} for i, j in zip(range(i1, i2), range(j1, j2))]
            elif op == "delete":
                rows += [{"left": a[i], "right": None, "kind": "removed"} for i in range(i1, i2)]
            elif op == "insert":
                rows += [{"left": None, "right": b[j], "kind": "added"} for j in range(j1, j2)]
            else:  # replace: pair lines up, then the leftovers
                left, right = a[i1:i2], b[j1:j2]
                for k in range(max(len(left), len(right))):
                    rows.append({"left": left[k] if k < len(left) else None,
                                 "right": right[k] if k < len(right) else None, "kind": "changed"})  # fmt: skip
        status = "added" if not old.exists() else "removed" if not new.exists() else "modified"
        return {"file": rel, "status": status, "rows": rows}
