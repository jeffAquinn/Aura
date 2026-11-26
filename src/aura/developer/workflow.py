"""The Developer-Agent Workflow (Sprint 6, approved by Gemma).

Specification (written by Aura, approved by the user)
  → Developer (Claude Code or Codex, in the Developer container, in a scratch workspace)
  → Auditor (the Security Governor: Shadow Project, static scan, provenance, the skill's tests in its
    sandbox, Aura's full test suite)
  → at most `repair_rounds` repair rounds (the Auditor's findings go back to the Developer; each failed
    round's Shadow Project is wiped)
  → Code Review (only a build that passed every gate reaches the user's Audit Card)
  → Deploy (the user's click; Sprint 5).

The Developer is a zero-trust external agent: it never touches Aura's repository, and only the
contents of skill/ are taken, then judged by the Governor exactly like any other skill.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog
from aura.config import Settings
from aura.developer import vault_tasks as vt
from aura.developer import workspace as ws
from aura.developer.engines import ENGINES, Engine, EngineEvent
from aura.governor.workshop import Task, Workshop
from aura.masking import PATTERNS_ONLY, Masker
from aura.secrets import Secrets
from aura.state import StateStore, cap
from aura.skills import NAME
from aura.tools.vault import Vault, human_size

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "skills" / "pdf_text"
RUNNING = ("queued", "developing", "auditing", "repairing")
KEEP_SCRATCH = 24 * 3600.0  # seconds a failed or interrupted job's scratch folder is kept, for diagnosis
CREDENTIAL_COPIES = ("codex", "codex.sum")  # an engine's per-run sign-in copy: never kept
CONNECTION_CHECK_TIMEOUT = 90.0
CONNECTION_CHECK_PROMPT = (
    "This is Aura's connection check. Do not read, create, edit, or delete any files, and do not run shell "
    "commands or use tools. Reply with exactly: Aura developer connection ready."
)


class DeveloperError(ValueError):
    pass


def describe_build(args: dict[str, Any], developer: Developer | None) -> str:
    """The Specification Approval card: who builds what, the hard limits, exactly what leaves this
    Mac, and the specification itself, verbatim (it is all the Developer receives)."""
    engines = developer.engines if developer else ENGINES
    engine = engines.get(str(args.get("engine") or "claude"))
    name, improve = str(args.get("name", "")), bool(args.get("improve"))
    label, vendor = (engine.label, engine.vendor) if engine else (str(args.get("engine")), "the vendor")
    rounds = developer.repair_rounds if developer else 2
    minutes = int((developer.timeout if developer else 600) // 60)
    cost = (f"${developer.budget_usd if developer else 2.0:.2f} total, " if engine and engine.caps_cost
            else f"{engine.plan if engine else 'its plan'}, ")  # fmt: skip
    what = f"improve the installed skill '{name}'" if improve else f"build the new skill '{name}'"
    lines = [
        f"have {label} {what} ({cost}{minutes} minutes per round, up to {rounds} repair rounds):",
        f"    Sent to {vendor}: this specification, the skill format guide and an example skill"
        + (" and the skill's current files" if improve else "") + ". Nothing from your vault or this conversation.",
        "    The result must pass the Security Governor; you review it in the Workshop before anything is installed.",
    ]
    lines += [f"    │ {line}" for line in str(args.get("specification", "")).strip().splitlines()]
    return "\n".join(lines)


def engine_name(value: Any) -> str:
    """"Claude Code" / "claude-code" / "OpenAI Codex" -> "claude" / "codex" (the model's spellings vary)."""
    v = str(value or "claude").strip().lower()
    return "codex" if "codex" in v else "claude" if "claude" in v else v


def describe_task(args: dict[str, Any], developer: Developer | None, vault: Vault) -> str:
    """A vault task's approval card: who gets what (the task and a copy of one folder, measured),
    the limits, and the task itself, verbatim."""
    engines = developer.engines if developer else ENGINES
    engine = engines.get(engine_name(args.get("engine")))
    label, vendor, plan = (engine.label, engine.vendor, engine.plan) if engine else (str(args.get("engine")), "its vendor", "")
    minutes = int((developer.timeout if developer else 600) // 60)
    folder = str(args.get("folder") or ".")
    try:
        name, files, size = vt.measure(vault, folder)
        where = "your whole vault" if name == "." else f"'{name}/' in your vault"
        sent = (f"    Sent to {vendor}: this task and a copy of {where} ({_count(files, 'file')}, {human_size(size)}), "
                f"which {label} reads. Nothing else from your Mac or this conversation.")  # fmt: skip
    except vt.VaultTaskError as e:
        where, sent = f"'{folder}' in your vault", f"    This will be refused: {e}."
    lines = [f"have {label} work on a copy of {where} ({plan + ', ' if plan else ''}up to {minutes} minutes):", sent,
             "    Then you allow or deny each file it changed. Nothing is written to your vault before that."]  # fmt: skip
    lines += [f"    │ {line}" for line in str(args.get("task", "")).strip().splitlines()]
    return "\n".join(lines)


def _epoch(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return 0.0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Build:
    id: str
    skill: str
    engine: str
    specification: str
    improve: bool
    budget_usd: float
    max_rounds: int
    stage: str = "queued"  # queued → developing → auditing → (repairing →)* ready | failed | stopped | interrupted
    round: int = 0
    cost_usd: float = 0.0
    message: str = "Waiting to start…"
    task_id: str | None = None  # the passing round's Workshop task (the Audit Card)
    history: list[dict[str, Any]] = field(default_factory=list)  # one entry per audited round
    feed: deque = field(default_factory=lambda: deque(maxlen=300))  # live status lines
    created: str = field(default_factory=_now)
    finished: str | None = None
    decision: str | None = None  # "deployed" | "rejected": the user's decision on the Audit Card
    recoverable: bool = False  # interrupted by a restart: the Workshop offers Retry
    scratch: str | None = None  # a failed or interrupted build's scratch folder, kept for diagnosis
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    sink: Callable[[str], None] | None = field(default=None, repr=False)  # the durable feed

    def say(self, text: str) -> None:
        text = cap(text, 500) or ""
        self.feed.append({"t": _now(), "text": text})
        if self.sink is not None:
            self.sink(text)

    def origin(self, label: str) -> dict[str, Any]:
        return {"build": self.id, "engine": label, "round": self.round, "cost_usd": round(self.cost_usd, 4),
                "specification": self.specification, "improve": self.improve}  # fmt: skip

    def public(self, workshop: Workshop | None = None) -> dict[str, Any]:
        stage = self.decision or self.stage
        if stage == "ready" and workshop and self.task_id in workshop.tasks:
            decided = workshop.tasks[self.task_id].stage  # the user's decision, once made
            stage = {"deployed": "deployed", "rejected": "rejected"}.get(decided, stage)
        return {"id": self.id, "skill": self.skill, "engine": self.engine, "improve": self.improve,
                "specification": self.specification, "stage": stage, "round": self.round,
                "max_rounds": self.max_rounds, "cost_usd": round(self.cost_usd, 4), "budget_usd": self.budget_usd,
                "message": self.message, "task_id": self.task_id, "history": self.history,
                "feed": list(self.feed), "created": self.created, "finished": self.finished,
                "recoverable": self.recoverable, "scratch_kept": self.scratch is not None}  # fmt: skip

    def record(self) -> dict[str, Any]:
        """What the state store keeps (the feed is kept separately, bounded)."""
        return {"id": self.id, "skill": self.skill, "engine": self.engine, "specification": self.specification,
                "improve": self.improve, "budget_usd": self.budget_usd, "max_rounds": self.max_rounds,
                "stage": self.stage, "round": self.round, "cost_usd": self.cost_usd, "message": self.message,
                "task_id": self.task_id, "history": self.history, "created": self.created, "finished": self.finished,
                "decision": self.decision, "recoverable": self.recoverable, "scratch": self.scratch}  # fmt: skip

    @classmethod
    def restore(cls, data: dict[str, Any]) -> Build:
        names = {f for f in cls.__dataclass_fields__ if f not in ("feed", "stop", "sink")}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class VaultTask:
    """A job in the vault (ask_developer): run once, then kept until the user has decided on every
    change it brought back."""

    id: str
    engine: str
    task: str
    copy: vt.Copy | None = None
    changes: list[vt.Change] = field(default_factory=list)
    summary: str = ""  # the Developer's own closing words
    error: str | None = None
    stopped: bool = False
    timed_out: bool = False
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def record(self, key: str) -> dict[str, Any]:
        """Metadata only: hashes, paths and counts, never file contents or diffs."""
        copy = self.copy
        return {"key": key, "id": self.id, "engine": self.engine, "task": cap(self.task, 300),
                "copy": copy and {"folder": copy.folder, "files": copy.files, "folders": sorted(copy.folders),
                                  "bytes": copy.bytes},
                "changes": [{k: v for k, v in vars(c).items() if k != "diff"} for c in self.changes],
                "summary": self.summary, "error": cap(self.error, 500), "stopped": self.stopped,
                "timed_out": self.timed_out}  # fmt: skip

    @classmethod
    def restore(cls, data: dict[str, Any]) -> VaultTask:
        c = data.get("copy")
        copy = vt.Copy(c["folder"], dict(c["files"]), set(c["folders"]), int(c["bytes"])) if c else None
        return cls(id=data["id"], engine=data["engine"], task=data.get("task", ""), copy=copy,
                   changes=[vt.Change(**ch) for ch in data.get("changes", [])], summary=data.get("summary", ""),
                   error=data.get("error"), stopped=bool(data.get("stopped")), timed_out=bool(data.get("timed_out")))  # fmt: skip


class Developer:
    def __init__(self, settings: Settings, workshop: Workshop, container: Any, secrets: Secrets, audit: AuditLog,
                 engines: dict[str, Engine] | None = None, budget_usd: float = 2.0, timeout: float = 600,
                 repair_rounds: int = 2, example: Path = EXAMPLE, state: StateStore | None = None,
                 masker: Masker | None = None):  # fmt: skip
        self.settings = settings
        self.workshop = workshop
        self.container = container
        self.secrets = secrets
        self.audit = audit
        self.engines = engines or ENGINES
        self.budget_usd = budget_usd
        self.timeout = timeout
        self.repair_rounds = repair_rounds
        self.example = example
        self.builds: dict[str, Build] = {}
        self.tasks: dict[str, VaultTask] = {}  # vault tasks awaiting the user's decisions, by tool call
        self.connection_checks: dict[str, dict[str, str | None]] = {}
        self.on_ready: Callable[[Build], None] = lambda b: None  # e.g. a spoken status line
        self._busy = threading.Lock()
        self.state = state or StateStore(None)
        self.masker = masker or PATTERNS_ONLY
        self._recover()
        self.sweep()

    # ── restart recovery ────────────────────────────────────────────────────────────────────

    def _recover(self) -> None:
        """Builds and vault tasks from before a restart. Nothing that was running is resumed or
        rerun: it is marked interrupted, its log kept. A vault task whose changes were waiting
        for the user's decisions is restored, so the decisions can still be made (each write is
        still guarded by the hash its file had when it was copied)."""
        for build_id, stage, data in self.state.records("build"):
            build = Build.restore(data)
            build.feed.extend(self.state.events("build", build_id))
            if stage in RUNNING:
                build.stage, build.recoverable, build.finished = "interrupted", True, _now()
                build.message = (f"Aura restarted while this build was {stage}. Nothing was installed; its "
                                 "log is kept. Retry to build it again.")  # fmt: skip
                build.scratch = str(self._dir(build)) if self._dir(build).exists() else None
            elif stage == "ready" and build.decision is None and build.task_id not in self.workshop.tasks:
                build.stage, build.recoverable = "interrupted", True
                build.message = ("Aura restarted before you reviewed this build, and its Audit Card couldn't "
                                 "be kept. Nothing was installed. Retry to build it again.")  # fmt: skip
            self._attach(build)
            if build.stage != stage:
                self._save(build)
        self.state.prune_records("build", keep_states=RUNNING)
        for key, state, data in self.state.records("vault_task"):
            work = self.settings.dev_dir / data["id"] / "work"
            if state == "awaiting" and work.is_dir():
                self.tasks[key] = VaultTask.restore(data)
                continue
            if state in ("running", "awaiting", "finishing"):
                why = {"running": "while the developer was working on it",
                       "awaiting": "and its working copy was gone",
                       "finishing": "while your allowed changes were being written (some may have been)"}[state]  # fmt: skip
                data["message"] = f"Aura restarted {why}. Nothing more was written to your vault."
                data["interrupted"] = _now()
                self.state.put_record("vault_task", key, "interrupted", data)
                self.audit.record("vault_task_interrupted", task=data["id"], was=state)

    def sweep(self) -> None:
        """Clean scratch folders (idempotent, so a cleanup cut short finishes on the next start).
        Kept: an awaiting vault task's working copy, and a failed or interrupted job's folder for
        KEEP_SCRATCH. An engine's sign-in copy is never kept."""
        root = self.settings.dev_dir
        if not root.is_dir():
            return
        keep: dict[str, float] = {}  # folder name -> until (monotonic-free: epoch seconds)
        for t in self.tasks.values():
            keep[t.id] = float("inf")
        for b in self.builds.values():
            if b.stage in RUNNING:
                keep[b.id] = float("inf")
            elif b.scratch and b.finished:
                keep[b.id] = _epoch(b.finished) + KEEP_SCRATCH
        for _, state, data in self.state.records("vault_task", ("interrupted",)):
            keep[data["id"]] = _epoch(data.get("interrupted") or _now()) + KEEP_SCRATCH
        for folder in root.iterdir():
            if not folder.is_dir() or not folder.name.startswith(("b-", "v-")):
                continue
            for name in CREDENTIAL_COPIES:  # left behind only if a run was cut off
                target = folder / name
                shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
            if time.time() >= keep.get(folder.name, 0.0):
                shutil.rmtree(folder, ignore_errors=True)
                build = self.builds.get(folder.name)
                if build is not None and build.scratch:
                    build.scratch = None
                    self._save(build)

    def recovery(self) -> list[dict[str, Any]]:
        """Interrupted work, for the Workshop's recovery section."""
        out = [{"kind": "build", "id": b.id, "title": f"Build of '{b.skill}' ({self._label(b.engine)})",
                "message": b.message, "when": b.finished or b.created, "scratch_kept": b.scratch is not None,
                "actions": ["retry", "dismiss"]}
               for b in sorted(self.builds.values(), key=lambda b: b.created, reverse=True) if b.recoverable]  # fmt: skip
        out += [{"kind": "vault_task", "id": key, "title": f"Vault job for {self._label(data['engine'])}: {cap(data.get('task', ''), 80)}",
                 "message": data.get("message", ""), "when": data.get("interrupted", ""),
                 "scratch_kept": (self.settings.dev_dir / data["id"]).is_dir(), "actions": ["dismiss"]}
                for key, _, data in self.state.records("vault_task", ("interrupted",))]  # fmt: skip
        return out

    def dismiss(self, item_id: str) -> None:
        """Clear one recovery item (idempotent): its log stays in the audit trail."""
        if (build := self.builds.get(item_id)) is not None:
            if build.recoverable or build.scratch:
                build.recoverable = False
                if build.scratch:
                    shutil.rmtree(build.scratch, ignore_errors=True)
                    build.scratch = None
                self._save(build)
            return
        found = self.state.record("vault_task", item_id)
        if found is None:
            raise DeveloperError("nothing to dismiss with that id")
        state, data = found
        if state != "interrupted":
            raise DeveloperError("that job isn't interrupted")
        shutil.rmtree(self.settings.dev_dir / data["id"], ignore_errors=True)
        self.state.delete_record("vault_task", item_id)

    def retry(self, build_id: str) -> Build:
        old = self.builds.get(build_id)
        if old is None or not old.recoverable:
            raise DeveloperError("only an interrupted build can be retried")
        build = self.start(old.skill, old.specification, old.engine, old.improve)
        self.dismiss(build_id)
        return build

    def task_decided(self, task_id: str, stage: str) -> None:
        """The user decided on an Audit Card: remember it on the build (it outlives a restart)."""
        for build in self.builds.values():
            if build.task_id == task_id and stage in ("deployed", "rejected"):
                build.decision = stage
                self._save(build)

    def _label(self, engine: str) -> str:
        return self.engines[engine].label if engine in self.engines else engine

    def _attach(self, build: Build) -> None:
        build.sink = lambda text, build_id=build.id: self.state.add_event("build", build_id, text)
        self.builds[build.id] = build

    def _save(self, build: Build) -> None:
        self.state.put_record("build", build.id, build.stage, build.record())

    def _stage(self, build: Build, stage: str, message: str | None = None, say: bool = False) -> None:
        """Every transition is saved before the work it starts."""
        build.stage = stage
        if message is not None:
            build.message = message
            if say:
                build.say(message)
        self._save(build)

    # ── status ──────────────────────────────────────────────────────────────────────────────

    def engines_status(self) -> list[dict[str, Any]]:
        docker = self.container.problem()
        return [self._engine_status(e, docker or e.problem(self.secrets)) for e in self.engines.values()]

    def _engine_status(self, engine: Engine, problem: str | None = None) -> dict[str, Any]:
        check = self.connection_checks.get(engine.name, {})
        return {"name": engine.name, "label": engine.label, "vendor": engine.vendor, "caps_cost": engine.caps_cost,
                "problem": problem, "check_state": check.get("state"), "check_message": check.get("message"),
                "checked_at": check.get("checked_at")}  # fmt: skip

    def check_connection(self, engine_name: str) -> dict[str, Any]:
        """Prove one signed-in Developer can answer in an empty, read-only workspace.

        This deliberately does not use a vault copy or Aura source tree, and is separate from a
        build or ``ask_developer`` task: no user files can be changed or sent during the check.
        """
        if engine_name not in self.engines:
            raise DeveloperError(f"unknown developer '{engine_name}' (choose: {', '.join(self.engines)})")
        engine = self.engines[engine_name]
        if problem := (self.container.problem() or engine.problem(self.secrets)):
            self._set_connection_check(engine.name, "unavailable", problem)
            self.audit.record("developer_connection_check", engine=engine.name, result="unavailable")
            return self._engine_status(engine, problem)
        if self.running() or not self._busy.acquire(blocking=False):
            raise DeveloperError("Claude Code or Codex is busy with another job; wait for it to finish before checking an account")
        root = self.settings.dev_dir / ("c-" + uuid.uuid4().hex[:8])
        try:
            work = root / "work"
            work.mkdir(parents=True)
            outcome = self.container.run(engine, work, root, CONNECTION_CHECK_PROMPT, 0.0,
                                         CONNECTION_CHECK_TIMEOUT, lambda _event: None, threading.Event(),
                                         workspace_read_only=True)
            if outcome.ok and outcome.exit_code == 0 and not outcome.timed_out and not outcome.stopped:
                self._set_connection_check(engine.name, "verified", "Verified with your connected account in an isolated read-only workspace.")
            else:
                detail = outcome.error or outcome.result or "The developer did not report a successful response."
                if outcome.timed_out:
                    detail = f"Timed out after {int(CONNECTION_CHECK_TIMEOUT)} seconds."
                elif outcome.stopped:
                    detail = "Stopped before the account check finished."
                self._set_connection_check(engine.name, "failed", cap(self.masker.mask(detail), 500) or "Connection check failed.")
            state = self.connection_checks[engine.name]["state"]
            self.audit.record("developer_connection_check", engine=engine.name, result=state)
            return self._engine_status(engine)
        except Exception as err:  # noqa: BLE001: a connection check must become an inspectable status
            self._set_connection_check(engine.name, "failed", cap(self.masker.mask(str(err)), 500) or "Connection check failed.")
            self.audit.record("developer_connection_check", engine=engine.name, result="failed")
            return self._engine_status(engine)
        finally:
            shutil.rmtree(root, ignore_errors=True)
            self._busy.release()

    def _set_connection_check(self, engine: str, state: str, message: str) -> None:
        self.connection_checks[engine] = {"state": state, "message": message, "checked_at": _now()}

    def running(self) -> Build | None:
        return next((b for b in self.builds.values() if b.stage in ("queued", "developing", "auditing", "repairing")), None)

    # ── start / stop ────────────────────────────────────────────────────────────────────────

    def check(self, skill: str, specification: str, engine: str, improve: bool) -> Engine:
        """Every reason a build can't start, raised before anything leaves this Mac."""
        if not NAME.match(skill):
            raise DeveloperError("the skill name must be lowercase letters, digits and underscores (2-40 characters)")
        if engine not in self.engines:
            raise DeveloperError(f"unknown developer '{engine}' (choose: {', '.join(self.engines)})")
        if problems := ws.check_specification(specification):
            raise DeveloperError("the specification isn't complete: " + "; ".join(problems))
        installed = (self.settings.skills_dir / skill).is_dir()
        if improve and not installed:
            raise DeveloperError(f"there is no installed skill called '{skill}' to improve")
        if not improve and installed:
            raise DeveloperError(f"'{skill}' is already installed; ask to improve it instead")
        e = self.engines[engine]
        if problem := (self.container.problem() or e.problem(self.secrets)):
            raise DeveloperError(f"{e.label} isn't ready: {problem}")
        if self.running():
            raise DeveloperError("another build is running; wait for it or stop it in the Workshop")
        if self._busy.locked():
            raise DeveloperError("Claude Code or Codex is busy with a task in the vault; try again when it's done")
        return e

    def start(self, skill: str, specification: str, engine: str = "claude", improve: bool = False,
              wait: bool = False) -> Build:  # fmt: skip
        e = self.check(skill, specification, engine, improve)
        build = Build(id="b-" + uuid.uuid4().hex[:8], skill=skill, engine=engine, specification=specification.strip(),
                      improve=improve, budget_usd=self.budget_usd if e.caps_cost else 0.0,
                      max_rounds=1 + self.repair_rounds)  # fmt: skip
        self._attach(build)
        self._save(build)  # persisted before any work starts
        self.audit.record("build_started", build=build.id, skill=skill, engine=engine, improve=improve,
                          specification_chars=len(build.specification))  # fmt: skip
        if wait:
            self._run(build, e)
        else:
            threading.Thread(target=self._run, args=(build, e), daemon=True, name=f"build-{build.id}").start()
        return build

    def stop(self, build_id: str) -> Build:
        build = self.builds.get(build_id)
        if build is None:
            raise DeveloperError("no such build")
        if build.stage in RUNNING and not build.stop.is_set():  # idempotent: a second stop changes nothing
            build.stop.set()
            build.say("Stopping…")
        return build

    # ── vault tasks (ask_developer) ─────────────────────────────────────────────────────────

    def run_task(self, key: str, task: str, engine: str, folder: str, vault: Vault,
                 on_status: Callable[[str], None] = lambda s: None) -> VaultTask:  # fmt: skip
        """Copy the folder, let the engine work on the copy (blocking, up to the time limit), and
        collect its changes. `key` (the tool call) finds the result again when the user decides."""
        if key in self.tasks:
            return self.tasks[key]
        if self.state.record("vault_task", key) is not None:  # known, but not resumable: never run twice
            raise DeveloperError("this job was interrupted when Aura restarted and isn't run again; ask for it "
                                 "again to start a fresh one")  # fmt: skip
        engine = engine_name(engine)
        try:
            vt.check_task(task)
        except vt.VaultTaskError as e:
            raise DeveloperError(str(e)) from e
        if engine not in self.engines:
            raise DeveloperError(f"unknown developer '{engine}' (choose: {', '.join(self.engines)})")
        e = self.engines[engine]
        if problem := (self.container.problem() or e.problem(self.secrets)):
            raise DeveloperError(f"{e.label} isn't ready: {problem}")
        if self.running() or not self._busy.acquire(blocking=False):
            raise DeveloperError("Claude Code or Codex is busy with another job (see the Workshop); try again when it's done")
        try:
            t = VaultTask(id="v-" + uuid.uuid4().hex[:8], engine=engine, task=task.strip())
            root = self.settings.dev_dir / t.id
            try:
                t.copy = vt.prepare(vault, folder, root / "work")
            except (vt.VaultTaskError, OSError) as err:
                shutil.rmtree(root, ignore_errors=True)
                raise DeveloperError(str(err)) from err
            self.tasks[key] = t
            self.state.put_record("vault_task", key, "running", t.record(key))  # before the engine starts
            self.audit.record("vault_task_started", task=t.id, engine=engine, folder=t.copy.folder,
                              files=len(t.copy.files), bytes=t.copy.bytes, task_chars=len(t.task))  # fmt: skip
            on_status(f"{e.label} is working on a copy of {_count(len(t.copy.files), 'file')}…")
            said: list[str] = []

            def on_event(event: EngineEvent) -> None:
                if event.kind == "status" and event.text:
                    on_status(f"{e.label}: {event.text}")
                elif event.kind == "message" and event.text:
                    said.append(event.text)

            outcome = self.container.run(e, root / "work", root, vt.prompt(t.task), 0.0, self.timeout, on_event, t.stop)
            t.stopped, t.timed_out, t.error = outcome.stopped or t.stop.is_set(), outcome.timed_out, outcome.error
            t.error = self.masker.mask(t.error) if t.error else None
            t.summary = cap(self.masker.mask(outcome.result if outcome.result not in ("", "done")
                                             else (said[-1] if said else "")), 2000) or ""  # fmt: skip
            if not (t.stopped or t.error):
                t.changes = vt.collect(vault, t.copy, root / "work")
            self.state.put_record("vault_task", key, "awaiting", t.record(key))
            self.audit.record("vault_task_ran", task=t.id, engine=engine, changes=len(t.changes), stopped=t.stopped,
                              timed_out=t.timed_out, error=bool(t.error))  # fmt: skip
            return t
        finally:
            self._busy.release()

    def finish_task(self, key: str, vault: Vault, allowed: list[bool] | None = None) -> list[str]:
        """Write the changes the user allowed (none when `allowed` is None), then drop the copy."""
        # awaiting → finishing, atomically: a second finish (a retried approval) writes nothing.
        if not self.state.transition("vault_task", key, ("awaiting", "running"), "finishing"):
            self.tasks.pop(key, None)
            return []
        t = self.tasks.pop(key, None)
        if t is None:
            self.state.delete_record("vault_task", key)
            return []
        try:
            if allowed is None or t.copy is None:
                return []
            return vt.apply(vault, t.copy, self.settings.dev_dir / t.id / "work", t.changes, allowed)
        finally:
            shutil.rmtree(self.settings.dev_dir / t.id, ignore_errors=True)
            self.state.delete_record("vault_task", key)
            self.audit.record("vault_task_finished", task=t.id, allowed=sum(allowed or []), changes=len(t.changes))

    def stop_tasks(self) -> None:
        """Stop any vault task that's running (e.g. the user pressed Stop in the chat)."""
        for t in self.tasks.values():
            t.stop.set()

    # ── the workflow ────────────────────────────────────────────────────────────────────────

    def _run(self, build: Build, engine: Engine) -> None:
        with self._busy:
            try:
                self._workflow(build, engine)
            except Exception as e:  # noqa: BLE001: any failure ends the build safely, nothing installed
                build.stage, build.message = "failed", cap(self.masker.mask(f"The build couldn't finish: {e}"), 600)
            finally:
                build.finished = _now()
                folder = self._dir(build)
                if build.stage == "failed" and folder.exists():  # kept for diagnosis, then swept
                    for name in CREDENTIAL_COPIES:
                        target = folder / name
                        shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
                    build.scratch = str(folder)
                else:
                    shutil.rmtree(folder, ignore_errors=True)  # the scratch workspace and run files
                self._save(build)
                self._record(build)
                if build.stage == "ready":
                    self.on_ready(build)

    def _workflow(self, build: Build, engine: Engine) -> None:
        root = self._dir(build)
        work = root / "work"
        current = self.settings.skills_dir / build.skill if build.improve else None
        ws.prepare(work, build.specification, self.example, current)
        prompt = ws.first_prompt(build.skill, build.improve)

        while build.round < build.max_rounds:
            build.round += 1
            self._stage(build, "developing", f"{engine.label} is writing the skill (round {build.round} of "
                                             f"{build.max_rounds})…", say=True)  # fmt: skip
            budget = max(build.budget_usd - build.cost_usd, 0.0)
            if engine.caps_cost and budget < 0.05:
                self._stage(build, "failed", f"The ${build.budget_usd:.2f} budget is used up.")
                return
            outcome = self.container.run(engine, work, root, prompt, budget, self.timeout, self._listener(build), build.stop)
            if engine.caps_cost:  # a plan's notional cost isn't a bill: only a capped engine counts it
                build.cost_usd += outcome.cost_usd
            if outcome.stopped or build.stop.is_set():
                self._stage(build, "stopped", "Stopped. Nothing was installed.")
                return
            if outcome.error:
                self._stage(build, "failed", cap(f"{engine.label} couldn't run: {self.masker.mask(outcome.error)}", 600))
                return
            if outcome.timed_out:
                build.say(f"{engine.label} ran out of time ({int(self.timeout // 60)} minutes); auditing what it wrote.")

            findings = self._audit(build, engine, work)
            if not findings:
                return  # ready: the passing task awaits the user's decision
            if build.round >= build.max_rounds:
                self._stage(build, "failed", f"{engine.label} couldn't produce a skill that passes every check in "
                                             f"{build.max_rounds} rounds. Nothing was installed.")  # fmt: skip
                return
            self._stage(build, "repairing")
            build.say(f"Round {build.round} didn't pass ({_count(len(findings), 'finding')}); sent back to {engine.label}.")
            prompt = ws.repair_prompt(build.skill, build.round, findings)

    def _audit(self, build: Build, engine: Engine, work: Path) -> list[str]:
        """Run the Governor on this round. Returns the findings to fix ([] if it passed)."""
        if missing := ws.has_skill(work):
            findings = [f"skill/{f} is missing" for f in missing]
            build.history.append({"round": build.round, "task": None, "passed": False, "findings": findings})
            return findings
        self._stage(build, "auditing", f"The Security Governor is auditing round {build.round} (tests included)…", say=True)
        task = self.workshop.propose(build.skill, wait=True, source=ws.collected(work), origin=build.origin(engine.label))
        findings = self._findings(task)
        build.history.append({"round": build.round, "task": task.id, "passed": not findings, "findings": findings,
                              "risk": task.report.risk if task.report else None})  # fmt: skip
        if findings:
            self.workshop.discard(task.id, f"Round {build.round} didn't pass; sent back to {engine.label}.")
            return findings
        build.task_id = task.id
        self._stage(build, "ready", f"Ready for your review: passed every check in round {build.round}.", say=True)
        return []

    @staticmethod
    def _findings(task: Task) -> list[str]:
        if task.stage != "awaiting" or task.report is None:
            return [task.message or "the audit couldn't finish"]
        report = task.report
        if not report.blocked:
            return []
        out = [f"[{f.severity}] {f.message}" + (f" ({f.detail})" if f.detail else "")
               for f in report.findings if f.severity in ("red", "reject")]  # fmt: skip
        out += [f"test {t['name']} failed: {t.get('detail', '')[-600:]}" for t in report.tests if t["status"] != "passed"]
        if not report.tests:
            out.append("no tests ran")
        if (report.suite or {}).get("failed"):
            out.append(f"Aura's test suite failed: {report.suite.get('summary', '')}")
        return out or ["the audit blocked this skill"]

    def _listener(self, build: Build) -> Callable[[EngineEvent], None]:
        def on_event(event: EngineEvent) -> None:
            if event.kind in ("status", "message") and event.text:
                text = self.masker.mask(event.text)
                build.say(text if event.kind == "status" else f"“{cap(text, 400)}”")

        return on_event

    def _dir(self, build: Build) -> Path:
        return self.settings.dev_dir / build.id

    def _record(self, build: Build) -> None:
        """Every build's record, for the Librarian (Sprint 8) and the audit trail."""
        self.audit.record("build_finished", build=build.id, skill=build.skill, engine=build.engine, stage=build.stage,
                          rounds=build.round, cost_usd=round(build.cost_usd, 4))  # fmt: skip
        folder = self.settings.data_dir / "builds"
        folder.mkdir(parents=True, exist_ok=True)
        record = {k: v for k, v in build.public().items() if k != "feed"}
        (folder / f"{build.id}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
