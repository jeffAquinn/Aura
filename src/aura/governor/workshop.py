"""The Workshop: Vault-local staging, the Governor's audit, and Approve & Deploy.

A proposed skill is copied into a throwaway shadow folder inside the Vault and audited there.
Nothing reaches the live Vault registry until every gate passes and the user approves on the Audit
Card. The source checkout is read-only: Aura never writes its files, Git state, or history.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog
from aura.agents import AgentError, AgentRegistry, load_manifest as load_agent_manifest
from aura.config import Settings
from aura.governor import AuditReport, Finding
from aura.governor.provenance import check_package, resolve
from aura.governor.scan import scan_skill
from aura.skills import SkillError, SkillManager, content_hash, dependency_image_for, dockerfile, load_manifest
from aura.tools.sandbox import Sandbox, SandboxUnavailable

# Aura's core (the Integrity Rule): a staged change touching any of these is rejected outright.
PROTECTED = [
    "src/aura/constitution.md", "src/aura/brain.py", "src/aura/persona.py", "src/aura/identity.py",
    "src/aura/server.py", "src/aura/middleware.py", "src/aura/skills.py", "src/aura/governor/",
    "src/aura/tools/", "src/aura/config.py", "tests/", "pyproject.toml", "uv.lock",
]  # fmt: skip


class WorkshopError(RuntimeError):
    pass


class AuditCancelled(WorkshopError):
    """Raised only when the user asks a running Workshop audit to stop."""


# The regression gate must never make Aura unusable.  A no-output timeout catches a wedged test;
# an absolute deadline also catches pathological filesystem work that keeps printing pytest dots.
# Offline Aura tests normally finish in seconds, so two minutes leaves generous headroom while
# still protecting the user's Mac and temporary storage.
SUITE_NO_PROGRESS_SECONDS = 30
SUITE_MAX_SECONDS = 120


@dataclass
class Task:
    id: str
    skill: str
    kind: str = "skill"  # skill | agent; retained as `skill` for older clients' labels
    stage: str = "staging"  # staging -> auditing -> awaiting -> deployed | rejected | failed
    message: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    report: AuditReport | None = None
    worktree: Path | None = None
    branch: str = ""
    base: str = ""
    digest: str = ""
    pins: list[tuple[str, str]] = field(default_factory=list)  # every package in the image
    source: Path | None = None  # where the skill comes from (default: the Skills Inbox)
    sandbox_skill: Path | None = None  # short-lived visible Docker mount used only for skill tests
    origin: dict[str, Any] | None = None  # set for Developer builds: engine, round, cost, specification
    progress: int = 0  # 0–100: a phase estimate, not a claim that an uninterruptible process stopped
    phase: str = "Waiting to start"
    error: str = ""
    cancel_requested: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        r = self.report
        return {"id": self.id, "skill": self.skill, "kind": self.kind, "stage": self.stage, "message": self.message,
                "created": self.created, "report": r.to_dict() if r else None, "origin": self.origin,
                "progress": self.progress, "phase": self.phase, "error": self.error or None}  # fmt: skip


class Workshop:
    def __init__(self, settings: Settings, manager: SkillManager, sandbox: Sandbox, audit: AuditLog,
                 run_suite: bool = True):  # fmt: skip
        self.settings = settings
        self.manager = manager
        self.sandbox = sandbox
        self.audit = audit
        self.agents = AgentRegistry(settings.agents_dir, settings.agents_inbox, audit)
        self.run_suite = run_suite
        self.on_deployed: Callable[[], None] = lambda: None
        self.tasks: dict[str, Task] = {}
        self._one_at_a_time = threading.Lock()
        self._cleanup_orphaned_staging()

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

    def agent_inbox(self) -> list[dict[str, Any]]:
        """Manifest-only candidates awaiting the same Governor process as skills."""
        return self.agents.pending()

    # ── propose -> stage -> audit ──────────────────────────────────────────────────────────

    def propose(self, name: str, wait: bool = False, source: Path | None = None,
                origin: dict[str, Any] | None = None) -> Task:  # fmt: skip
        """Stage and audit a skill: from the Skills Inbox, or (Developer builds) from `source`."""
        if source is None:
            source = self.settings.skills_inbox / name
            if not source.is_dir():
                raise WorkshopError(f"there is no skill called '{name}' in the Skills Inbox")
        elif not source.is_dir():
            raise WorkshopError(f"there is no skill at {source}")
        if any(t.skill == name and t.stage in ("staging", "auditing", "awaiting") for t in self.tasks.values()):
            raise WorkshopError(f"'{name}' is already being reviewed")
        task = Task(id=uuid.uuid4().hex[:8], skill=name, source=source, origin=origin)
        self.tasks[task.id] = task
        self.audit.record("skill_proposed", skill=name, task=task.id)
        if wait:
            self._run(task)
        else:
            threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return task

    def propose_agent(self, name: str, wait: bool = False) -> Task:
        """Stage a Workstation agent manifest.  It never imports executable agent state."""
        source = self.settings.agents_inbox / name
        if not source.is_dir():
            raise WorkshopError(f"there is no agent called '{name}' in the Agent Inbox")
        if source.is_symlink():
            raise WorkshopError("agent candidates cannot be symbolic links")
        if any(t.skill == name and t.kind == "agent" and t.stage in ("staging", "auditing", "awaiting")
               for t in self.tasks.values()):
            raise WorkshopError(f"agent '{name}' is already being reviewed")
        task = Task(id=uuid.uuid4().hex[:8], skill=name, kind="agent", source=source)
        self.tasks[task.id] = task
        self.audit.record("agent_proposed", agent=name, task=task.id)
        if wait:
            self._run(task)
        else:
            threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return task

    def _run(self, task: Task) -> None:
        with self._one_at_a_time:
            try:
                self._checkpoint(task, 2, "Preparing an isolated shadow project…")
                self._stage(task)
                self._checkpoint(task, 15, "Starting the Security Governor's checks…")
                task.stage = "auditing"
                task.report = self._audit(task)
                task.stage = "awaiting"
                task.progress, task.phase = 100, "Audit complete"
                task.message = ("Blocked: see the findings." if task.report.blocked
                                else "Ready for your review.")  # fmt: skip
                event = "agent_audited" if task.kind == "agent" else "skill_audited"
                self.audit.record(event, **({"agent": task.skill} if task.kind == "agent" else {"skill": task.skill}),
                                  task=task.id, risk=task.report.risk, blocked=task.report.blocked)
            except AuditCancelled:
                task.stage, task.phase = "cancelled", "Cancelled"
                task.message = "Cancelled. Nothing was installed."
                self.audit.record("agent_audit_cancelled" if task.kind == "agent" else "skill_audit_cancelled",
                                  **({"agent": task.skill} if task.kind == "agent" else {"skill": task.skill}), task=task.id)
                self._cleanup(task)
            except Exception as e:  # noqa: BLE001: any failure ends the task safely
                detail = str(e).strip() or type(e).__name__
                failed_phase = task.phase
                task.stage, task.phase, task.error = "failed", "Failed", detail[:1_200]
                task.message = f"The audit couldn't finish during {failed_phase.lower()}: {task.error}"
                self.audit.record("agent_audit_failed" if task.kind == "agent" else "skill_audit_failed",
                                  **({"agent": task.skill} if task.kind == "agent" else {"skill": task.skill}), task=task.id,
                                  error=task.error, progress=task.progress)
                self._cleanup(task)

    def _stage(self, task: Task) -> None:
        self._checkpoint(task, 5, "Creating an isolated shadow project…")
        name = task.skill
        task.worktree = self.settings.staging_dir / task.id
        if task.worktree.exists():
            shutil.rmtree(task.worktree)
        folder = "agents" if task.kind == "agent" else "skills"
        inbox = self.settings.agents_inbox if task.kind == "agent" else self.settings.skills_inbox
        dest = task.worktree / folder / name
        shutil.copytree(task.source or inbox / name, dest, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))  # fmt: skip
        self._checkpoint(task, 12, "Recording the proposed skill for review…")

    def _audit(self, task: Task) -> AuditReport:
        if task.kind == "agent":
            return self._audit_agent(task)
        wt, name = task.worktree, task.skill
        assert wt is not None
        report = AuditReport(skill=name)
        add = report.findings.append

        self._checkpoint(task, 20, "Checking the proposed change's scope…")
        changed = [p.relative_to(wt).as_posix() for p in wt.rglob("*") if p.is_file() or p.is_symlink()]
        protected = [p for p in changed if any(p == item or p.startswith(item) for item in PROTECTED)]
        if protected:
            add(Finding("protected", "reject", "touches Aura's protected core", ", ".join(protected)))
            return report
        outside = [p for p in changed if not p.startswith(f"skills/{name}/")]
        if outside:
            add(Finding("write-scope", "reject", "contains files outside the skill's own folder",
                        ", ".join(outside[:10])))
            return report
        add(Finding("write-scope", "pass", f"only skills/{name}/ are staged ({len(changed)} files)"))
        add(Finding("protected", "pass", "the read-only source checkout is untouched"))

        self._checkpoint(task, 32, "Validating the skill manifest…")
        folder = wt / "skills" / name
        try:
            manifest = load_manifest(folder)
        except (SkillError, SyntaxError) as e:
            add(Finding("manifest", "reject", f"invalid skill: {e}"))
            return report
        report.version, report.purpose = manifest["version"], manifest["purpose"]
        add(Finding("manifest", "pass", f"valid manifest; vault access: {manifest['permissions']['vault']}; network: none"))

        self._checkpoint(task, 42, "Statically scanning the skill code…")
        report.findings += scan_skill(folder)

        self._checkpoint(task, 52, "Resolving pinned dependencies…")
        try:
            pins = resolve(manifest["dependencies"])
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            add(Finding("provenance", "red", "dependencies could not be resolved from ready-built packages", str(e)[:300]))
            return report
        task.pins = pins
        if not pins:
            add(Finding("provenance", "pass", "no third-party packages"))
        for i, (pkg, version) in enumerate(pins, start=1):
            self._checkpoint(task, 52 + int(18 * i / max(len(pins), 1)),
                             f"Checking dependency provenance: {pkg} {version}…")
            try:
                report.findings += check_package(pkg, version).findings
            except Exception as e:  # noqa: BLE001: unknown can only make the verdict worse
                add(Finding("provenance", "red", f"{pkg} {version} could not be verified", str(e)[:200]))
        if report.risk == "red":
            return report  # never build or run code the rules have already rejected

        task.digest = content_hash(folder)
        # The image contains the audited package set, not the skill's source.  Source mounts
        # read-only at runtime, so skills with an identical package resolution share one image.
        image = dependency_image_for(pins)
        try:
            self._checkpoint(task, 74, "Preparing the isolated test image…")
            if not self.sandbox.image_exists(image):
                self.sandbox.build_image(image, dockerfile(pins))
        except SandboxUnavailable as e:
            add(Finding("tests", "red", "the skill's sandbox image couldn't be built", str(e)[:300]))
            return report
        report.image = image
        self._checkpoint(task, 84, "Running the skill's tests in the isolated sandbox…")
        report.tests = self.manager.run_tests(self._sandbox_skill_copy(task, folder), image,
                                              vault=manifest["permissions"]["vault"] == "read")
        if self.run_suite:
            self._checkpoint(task, 92, "Running Aura's offline regression suite…")
            report.suite = self._suite(wt, task.cancel_requested)
        self._checkpoint(task, 98, "Finalizing the audit report…")
        return report

    def _audit_agent(self, task: Task) -> AuditReport:
        """Audit a declarative agent record with the same scope, suite, and approval gates.

        Imported agent runtimes, code, connectors, credentials, and routines are intentionally
        not representable in this format; each would need its own separately-audited artifact.
        """
        wt, name = task.worktree, task.skill
        assert wt is not None
        report = AuditReport(skill=name)
        add = report.findings.append
        self._checkpoint(task, 20, "Checking the proposed agent's scope…")
        changed = [p.relative_to(wt).as_posix() for p in wt.rglob("*") if p.is_file() or p.is_symlink()]
        outside = [p for p in changed if not p.startswith(f"agents/{name}/")]
        if outside:
            add(Finding("write-scope", "reject", "contains files outside the agent's own folder", ", ".join(outside[:10])))
            return report
        add(Finding("write-scope", "pass", f"only agents/{name}/ are staged ({len(changed)} files)"))
        add(Finding("protected", "pass", "the read-only source checkout is untouched"))
        self._checkpoint(task, 40, "Validating the recognition manifest…")
        try:
            manifest = load_agent_manifest(wt / "agents" / name)
        except (AgentError, OSError, SyntaxError) as e:
            add(Finding("manifest", "reject", f"invalid agent manifest: {e}"))
            return report
        report.version, report.purpose = manifest["version"], manifest["purpose"]
        add(Finding("manifest", "pass", "non-executable manifest; vault, network, and execution are denied"))
        delta = manifest["recognition"]["intent_delta"]
        add(Finding("intent-delta", "info", delta))
        self._checkpoint(task, 70, "Checking the agent has no executable surface…")
        report.tests = [{"name": "manifest-only agent surface", "status": "passed",
                         "detail": "No code, dependencies, connectors, routines, or credentials were admitted."}]
        if self.run_suite:
            self._checkpoint(task, 92, "Running Aura's offline regression suite…")
            report.suite = self._suite(wt, task.cancel_requested)
        self._checkpoint(task, 98, "Finalizing the audit report…")
        return report

    def _sandbox_skill_copy(self, task: Task, folder: Path) -> Path:
        """Expose audited skill code to Docker without mounting a hidden Git worktree.

        Docker Desktop's macOS file-sharing VM can reject paths under ``.aura-staging`` or
        Library caches as nonexistent.  Copying these three small, already-scanned files into the
        Vault's temporary audit area gives the sandbox a normal visible read-only mount.  The copy
        is not a deployment and is deleted with its audit task.
        """
        root = self.settings.vault_dir / "Audit_Temp" / task.id
        target = root / task.skill
        if root.exists():
            shutil.rmtree(root)
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
        task.sandbox_skill = target
        return target

    # The server-lifecycle check starts ``uv run aura-server`` and therefore constructs another
    # project environment.  It is an explicit integration check, never part of the bounded
    # offline audit gate (including while an older committed shadow project is under review).
    SUITE = ("pytest", "-q", "-p", "no:cacheprovider", "-m", "not live",
             "tests/test_governor.py", "tests/test_sandbox.py", "tests/test_vault.py",
             "--deselect", "tests/test_live.py",
             "--deselect", "tests/test_server.py::test_server_process_announces_port_serves_and_exits_with_its_parent")

    def _suite(self, worktree: Path, cancel: threading.Event | None = None) -> dict[str, Any]:
        """Run Aura's current offline regression suite.

        A candidate skill is not allowed to modify core code or tests (the scope gate already
        verifies that), so this must exercise Aura's active source and its matching tests rather
        than a potentially older Git shadow checkout.  Offline means no `live` tests: the gate
        mustn't depend on the network, Docker or the model.
        """
        suite_root = self.settings.project_dir if hasattr(self, "settings") else worktree
        # Do not invoke ``uv --project`` here: it creates a full .venv inside every audit.  That
        # environment can be gigabytes and its cleanup was the cause of audits appearing stuck
        # at 92%.  The running server's interpreter has the locked dependencies; PYTHONPATH makes
        # tests import the active source without writing bytecode or caches into the checkout.
        command = [sys.executable, "-m", *self.SUITE]
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        suite_src = str(suite_root / "src")
        environment["PYTHONPATH"] = suite_src + (os.pathsep + environment["PYTHONPATH"]
                                                if environment.get("PYTHONPATH") else "")
        if cancel is None:
            out = subprocess.run(command, cwd=suite_root, capture_output=True, text=True,
                                 timeout=SUITE_MAX_SECONDS, env=environment)
        else:
            out = self._run_suite_cancellable(command, suite_root, cancel, environment)
        return self.parse_suite(out.returncode, out.stdout, out.stderr)

    @staticmethod
    def _run_suite_cancellable(command: list[str], worktree: Path, cancel: threading.Event,
                               environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """Run the regression gate in a process group with cancel and bounded run limits.

        Pytest's live progress is read byte-by-byte, so a test suite that stops producing output
        cannot silently consume a CPU forever. Its process group is terminated, including any
        child test worker, and the caller turns the precise reason into an audit diagnostic.
        """
        stdout: list[str] = []
        stderr: list[str] = []
        activity = [time.monotonic()]
        lock = threading.Lock()

        def drain(stream: Any, destination: list[str]) -> None:
            for char in iter(lambda: stream.read(1), ""):
                destination.append(char)
                with lock:
                    activity[0] = time.monotonic()

        environment = environment or {**os.environ, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(command, cwd=worktree, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True, env=environment)  # fmt: skip
        assert process.stdout is not None and process.stderr is not None
        readers = [threading.Thread(target=drain, args=(stream, destination), daemon=True)
                   for stream, destination in ((process.stdout, stdout), (process.stderr, stderr))]
        for reader in readers:
            reader.start()
        started = time.monotonic()
        while True:
            if process.poll() is not None:
                break
            if cancel.is_set():
                Workshop._stop_process_group(process)
                raise AuditCancelled
            with lock:
                stalled = time.monotonic() - activity[0] >= SUITE_NO_PROGRESS_SECONDS
            if stalled:
                Workshop._stop_process_group(process)
                stderr.append(f"\nAura stopped the regression suite after {SUITE_NO_PROGRESS_SECONDS} seconds without test output.")
                break
            if time.monotonic() - started >= SUITE_MAX_SECONDS:
                Workshop._stop_process_group(process)
                stderr.append(f"\nAura stopped the regression suite after {SUITE_MAX_SECONDS} seconds (maximum audit duration).")
                break
            time.sleep(0.2)
        for reader in readers:
            reader.join(timeout=1)
        code = process.returncode if process.returncode is not None else 124
        return subprocess.CompletedProcess(command, code, "".join(stdout), "".join(stderr))

    @staticmethod
    def _stop_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
        except ProcessLookupError:
            pass

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
        # Keep the completed task in memory for the originating Developer build to observe its
        # decision.  The UI's visible-task filter hides completed cards, while the audit log is
        # the durable history across restarts.
        return task

    def cancel(self, task_id: str) -> Task:
        """Request safe cancellation of a staging/auditing task. Nothing is installed."""
        task = self.tasks.get(task_id)
        if task is None:
            raise WorkshopError("no such task")
        if task.stage not in ("staging", "auditing"):
            raise WorkshopError(f"this task is {task.stage}, not running")
        task.cancel_requested.set()
        task.phase = "Cancelling"
        task.message = "Cancellation requested. Aura will stop at the next safe checkpoint."
        self.audit.record("skill_audit_cancel_requested", skill=task.skill, task=task.id)
        return task

    def discard(self, task_id: str, message: str) -> None:
        """A Developer round that didn't pass: wipe its Shadow Project; it is never offered."""
        task = self.tasks.get(task_id)
        if task is None or task.stage not in ("awaiting", "failed"):
            return
        self._cleanup(task)
        task.stage, task.message = "discarded", message
        self.audit.record("skill_discarded", skill=task.skill, task=task.id)

    def _deploy(self, task: Task) -> None:
        if task.kind == "agent":
            self._deploy_agent(task)
            return
        name = task.skill
        assert task.worktree is not None and task.report is not None
        old_image = (self.manager.approvals().get(name) or {}).get("image")
        live = self.settings.skills_dir / name
        # A user can audit a skill again after it was installed (or restore a saved Audit Card).
        # If its exact audited content is already live, there is no Git change to commit. Treat
        # approval as idempotent instead of leaving the card stuck on a failed empty commit.
        already_live = live.is_dir() and content_hash(live) == task.digest
        if already_live:
            self.manager.approve(name, task.digest, task.report.version, task.report.image, packages=task.pins)
            self._clear_inbox_source(task)
            self._cleanup(task)
            self.on_deployed()
            checks = self.manager.run_tests(live, task.report.image, vault=True)
            ok = all(c["status"] == "passed" for c in checks)
            task.stage = "deployed"
            task.message = ("Already installed and verified in the live registry."
                            if ok else "Already installed, but its live tests failed; it has been disabled.")
            if not ok:
                self.manager.set_enabled(name, False)
                self.on_deployed()
            self.audit.record("skill_deployed", skill=name, task=task.id, verified=ok, existing=True)
            return
        if live.exists():
            shutil.rmtree(live)
        shutil.copytree(task.worktree / "skills" / name, live)
        self.manager.approve(name, task.digest, task.report.version, task.report.image, packages=task.pins)
        self._clear_inbox_source(task)
        # A replacement can make Aura's previous bundle unreachable.  Delete only that exact,
        # Aura-namespaced image, and only after proving no other approved skill refers to it.
        if old_image and old_image != task.report.image and not self.manager.image_is_referenced_elsewhere(old_image, name):
            self.sandbox.remove_image(old_image)
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

    def _clear_inbox_source(self, task: Task) -> None:
        """Remove a deployed skill from the pending Inbox, and nothing else.

        The Inbox is a review queue.  Only its direct, non-symlinked folder for this exact skill
        is removed after deployment; Developer workspaces and arbitrary proposal sources remain.
        """
        source = task.source
        inbox = self.settings.agents_inbox if task.kind == "agent" else self.settings.skills_inbox
        if (source is None or source.is_symlink() or not source.is_dir() or source.name != task.skill
                or source.parent.resolve() != inbox.resolve()):
            return
        shutil.rmtree(source)
        self.audit.record("agent_inbox_cleared" if task.kind == "agent" else "skill_inbox_cleared",
                          **({"agent": task.skill} if task.kind == "agent" else {"skill": task.skill}), task=task.id)

    def _deploy_agent(self, task: Task) -> None:
        """Promote an audited declarative record into the Workstation registry.

        This deliberately does not load it as a chat tool or grant it capabilities. Recognition is
        an accountable record, not permission to execute an imported runtime.
        """
        name = task.skill
        assert task.worktree is not None and task.report is not None
        new = task.worktree / "agents" / name
        live = self.settings.agents_dir / name
        if live.exists():
            shutil.rmtree(live)
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(new, live)
        self._clear_inbox_source(task)
        self._cleanup(task)
        task.stage = "deployed"
        task.message = "Recognized in the Workstation. It remains a non-executable manifest."
        self.audit.record("agent_deployed", agent=name, task=task.id, verified=True)

    def uninstall(self, name: str) -> None:
        """Remove a Vault-local skill, forget its approval, and reload Aura's tools."""
        live = self.settings.skills_dir / name
        if not live.is_dir() or name.startswith((".", "_")) or live.parent != self.settings.skills_dir:
            raise WorkshopError(f"'{name}' is not an installed skill")
        image = (self.manager.approvals().get(name) or {}).get("image")
        shutil.rmtree(live)
        self.manager.forget(name)
        if image and not self.manager.image_is_referenced_elsewhere(image, name):
            self.sandbox.remove_image(image)
        self.audit.record("skill_uninstalled", skill=name)
        self.on_deployed()

    def _cleanup(self, task: Task) -> None:
        if task.worktree and task.worktree.exists():
            if task.worktree.exists() and task.worktree.parent.resolve() == self.settings.staging_dir.resolve():
                shutil.rmtree(task.worktree, ignore_errors=True)
        staging = self.settings.staging_dir
        if staging.is_dir() and not any(staging.iterdir()):
            staging.rmdir()
        if task.sandbox_skill:
            root = self.settings.vault_dir / "Audit_Temp" / task.id
            if task.sandbox_skill.is_relative_to(root) and root.parent.resolve() == (self.settings.vault_dir / "Audit_Temp").resolve():
                shutil.rmtree(root, ignore_errors=True)

    def _cleanup_orphaned_staging(self) -> None:
        """A server restart has no resumable tasks; remove only old Vault-local audit folders."""
        staging = self.settings.staging_dir
        try:
            # This root is reserved for Aura only, so every direct child is safe to remove.
            if staging.is_dir():
                for child in staging.iterdir():
                    if child.is_dir() and child.parent.resolve() == staging.resolve():
                        shutil.rmtree(child, ignore_errors=True)
                if not any(staging.iterdir()):
                    staging.rmdir()
            mounts = self.settings.vault_dir / "Audit_Temp"
            if mounts.is_dir():
                for child in mounts.iterdir():
                    if child.is_dir() and child.parent.resolve() == mounts.resolve():
                        shutil.rmtree(child, ignore_errors=True)
                if not any(mounts.iterdir()):
                    mounts.rmdir()
        except (OSError, subprocess.SubprocessError):
            # Startup must remain available even when Git is unavailable; a later startup retries.
            return

    @staticmethod
    def _checkpoint(task: Task, progress: int, phase: str) -> None:
        if task.cancel_requested.is_set():
            raise AuditCancelled
        task.progress, task.phase, task.message = progress, phase, phase

    # ── the side-by-side diff ──────────────────────────────────────────────────────────────

    def diff(self, task_id: str) -> list[dict[str, Any]]:
        task = self.tasks.get(task_id)
        if task is None or task.worktree is None or not task.worktree.exists():
            return []
        root = "agents" if task.kind == "agent" else "skills"
        new_root = task.worktree / root / task.skill
        old_root = (self.settings.agents_dir if task.kind == "agent" else self.settings.skills_dir) / task.skill
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
