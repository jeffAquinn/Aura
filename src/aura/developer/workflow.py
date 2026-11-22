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
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aura.audit import AuditLog
from aura.config import Settings
from aura.developer import workspace as ws
from aura.developer.engines import ENGINES, Engine, EngineEvent
from aura.governor.workshop import Task, Workshop
from aura.secrets import Secrets
from aura.skills import NAME

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "skills" / "pdf_text"


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
            else "your ChatGPT plan, ")  # fmt: skip
    what = f"improve the installed skill '{name}'" if improve else f"build the new skill '{name}'"
    lines = [
        f"have {label} {what} ({cost}{minutes} minutes per round, up to {rounds} repair rounds):",
        f"    Sent to {vendor}: this specification, the skill format guide and an example skill"
        + (" and the skill's current files" if improve else "") + ". Nothing from your vault or this conversation.",
        "    The result must pass the Security Governor; you review it in the Workshop before anything is installed.",
    ]
    lines += [f"    │ {line}" for line in str(args.get("specification", "")).strip().splitlines()]
    return "\n".join(lines)


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
    stage: str = "queued"  # queued → developing → auditing → (repairing →)* ready | failed | stopped
    round: int = 0
    cost_usd: float = 0.0
    message: str = "Waiting to start…"
    task_id: str | None = None  # the passing round's Workshop task (the Audit Card)
    history: list[dict[str, Any]] = field(default_factory=list)  # one entry per audited round
    feed: deque = field(default_factory=lambda: deque(maxlen=300))  # live status lines
    created: str = field(default_factory=_now)
    finished: str | None = None
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def say(self, text: str) -> None:
        self.feed.append({"t": _now(), "text": text})

    def origin(self, label: str) -> dict[str, Any]:
        return {"build": self.id, "engine": label, "round": self.round, "cost_usd": round(self.cost_usd, 4),
                "specification": self.specification, "improve": self.improve}  # fmt: skip

    def public(self, workshop: Workshop | None = None) -> dict[str, Any]:
        stage = self.stage
        if stage == "ready" and workshop and self.task_id in workshop.tasks:
            decided = workshop.tasks[self.task_id].stage  # the user's decision, once made
            stage = {"deployed": "deployed", "rejected": "rejected"}.get(decided, stage)
        return {"id": self.id, "skill": self.skill, "engine": self.engine, "improve": self.improve,
                "specification": self.specification, "stage": stage, "round": self.round,
                "max_rounds": self.max_rounds, "cost_usd": round(self.cost_usd, 4), "budget_usd": self.budget_usd,
                "message": self.message, "task_id": self.task_id, "history": self.history,
                "feed": list(self.feed), "created": self.created, "finished": self.finished}  # fmt: skip


class Developer:
    def __init__(self, settings: Settings, workshop: Workshop, container: Any, secrets: Secrets, audit: AuditLog,
                 engines: dict[str, Engine] | None = None, budget_usd: float = 2.0, timeout: float = 600,
                 repair_rounds: int = 2, example: Path = EXAMPLE):  # fmt: skip
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
        self.on_ready: Callable[[Build], None] = lambda b: None  # e.g. a spoken status line
        self._busy = threading.Lock()

    # ── status ──────────────────────────────────────────────────────────────────────────────

    def engines_status(self) -> list[dict[str, Any]]:
        docker = self.container.problem()
        return [{"name": e.name, "label": e.label, "vendor": e.vendor, "caps_cost": e.caps_cost,
                 "problem": docker or e.problem(self.secrets)} for e in self.engines.values()]  # fmt: skip

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
        return e

    def start(self, skill: str, specification: str, engine: str = "claude", improve: bool = False,
              wait: bool = False) -> Build:  # fmt: skip
        e = self.check(skill, specification, engine, improve)
        build = Build(id="b-" + uuid.uuid4().hex[:8], skill=skill, engine=engine, specification=specification.strip(),
                      improve=improve, budget_usd=self.budget_usd if e.caps_cost else 0.0,
                      max_rounds=1 + self.repair_rounds)  # fmt: skip
        self.builds[build.id] = build
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
        build.stop.set()
        build.say("Stopping…")
        return build

    # ── the workflow ────────────────────────────────────────────────────────────────────────

    def _run(self, build: Build, engine: Engine) -> None:
        with self._busy:
            try:
                self._workflow(build, engine)
            except Exception as e:  # noqa: BLE001: any failure ends the build safely, nothing installed
                build.stage, build.message = "failed", f"The build couldn't finish: {e}"
            finally:
                build.finished = _now()
                self._record(build)
                shutil.rmtree(self._dir(build), ignore_errors=True)  # the scratch workspace and run files
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
            build.stage = "developing"
            build.message = f"{engine.label} is writing the skill (round {build.round} of {build.max_rounds})…"
            build.say(build.message)
            budget = max(build.budget_usd - build.cost_usd, 0.0)
            if engine.caps_cost and budget < 0.05:
                build.stage, build.message = "failed", f"The ${build.budget_usd:.2f} budget is used up."
                return
            outcome = self.container.run(engine, work, root, prompt, budget, self.timeout, self._listener(build), build.stop)
            build.cost_usd += outcome.cost_usd
            if outcome.stopped or build.stop.is_set():
                build.stage, build.message = "stopped", "Stopped. Nothing was installed."
                return
            if outcome.error:
                build.stage, build.message = "failed", f"{engine.label} couldn't run: {outcome.error}"
                return
            if outcome.timed_out:
                build.say(f"{engine.label} ran out of time ({int(self.timeout // 60)} minutes); auditing what it wrote.")

            findings = self._audit(build, engine, work)
            if not findings:
                return  # ready: the passing task awaits the user's decision
            if build.round >= build.max_rounds:
                build.stage = "failed"
                build.message = (f"{engine.label} couldn't produce a skill that passes every check in "
                                 f"{build.max_rounds} rounds. Nothing was installed.")  # fmt: skip
                return
            build.stage = "repairing"
            build.say(f"Round {build.round} didn't pass ({_count(len(findings), 'finding')}); sent back to {engine.label}.")
            prompt = ws.repair_prompt(build.skill, build.round, findings)

    def _audit(self, build: Build, engine: Engine, work: Path) -> list[str]:
        """Run the Governor on this round. Returns the findings to fix ([] if it passed)."""
        if missing := ws.has_skill(work):
            findings = [f"skill/{f} is missing" for f in missing]
            build.history.append({"round": build.round, "task": None, "passed": False, "findings": findings})
            return findings
        build.stage = "auditing"
        build.message = f"The Security Governor is auditing round {build.round} (tests included)…"
        build.say(build.message)
        task = self.workshop.propose(build.skill, wait=True, source=ws.collected(work), origin=build.origin(engine.label))
        findings = self._findings(task)
        build.history.append({"round": build.round, "task": task.id, "passed": not findings, "findings": findings,
                              "risk": task.report.risk if task.report else None})  # fmt: skip
        if findings:
            self.workshop.discard(task.id, f"Round {build.round} didn't pass; sent back to {engine.label}.")
            return findings
        build.stage, build.task_id = "ready", task.id
        build.message = f"Ready for your review: passed every check in round {build.round}."
        build.say(build.message)
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
                build.say(event.text if event.kind == "status" else f"“{event.text[:400]}”")

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
