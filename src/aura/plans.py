"""Plans: multi-step work as a checked graph of steps (docs/orchestration.md, ticket T1).

A plan is a goal and up to 20 steps. Each step names one tool from the Capability Registry (or
`think`, work the model does itself, or `review`, a pause for the user), what it does, when it is
done, and the steps it waits for. Aura (the model) writes the steps; this module checks them,
assigns the IDs, and decides what can run next. A failure blocks every step that depends on it,
and only those.

Pure: no I/O, no clock. Storing plans is `state.py`'s job (T2); the tools that use them are T4.
Nothing here grants permission for anything: every step's tool keeps its own approval card.
"""

from __future__ import annotations

import graphlib
import secrets
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_STEPS = 20
MAX_TEXT = 300  # a step's `does` and `done_when`: one line each
MAX_GOAL = 500
MAX_ATTEMPTS = 2  # a failed step may be retried once, with a ruling
THINK, REVIEW = "think", "review"  # steps with no tool call
BUILT_IN = (THINK, REVIEW)
PLAN_TOOLS = frozenset({"make_plan", "plan_status", "finish_step"})  # never a step of a plan

PENDING, RUNNING, WAITING, DONE, FAILED, BLOCKED, SKIPPED = (
    "pending", "running", "waiting", "done", "failed", "blocked", "skipped")
OPEN = frozenset({PENDING, RUNNING, WAITING})
# How a step can end (superpowers' implementer statuses, docs/orchestration.md §10).
OUTCOMES = ("done", "done_with_concerns", "needs_input", "failed")


class PlanError(ValueError):
    """A plan or a transition was refused. `reasons` lists every problem found, so the model can
    correct them all in one try."""

    def __init__(self, reasons: Iterable[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass
class Step:
    id: str
    does: str
    tool: str
    done_when: str
    after: tuple[str, ...] = ()
    status: str = PENDING
    outcome: str | None = None
    summary: str = ""
    attempts: int = 0

    @property
    def uses_tool(self) -> bool:
        return self.tool not in BUILT_IN


@dataclass
class Ruling:
    """A departure from the plan: "Ruling: what — why — what it costs if wrong"."""

    step: str
    what: str
    why: str
    cost: str

    def line(self) -> str:
        return f"Ruling ({self.step}): {self.what} — {self.why} — {self.cost}"


@dataclass
class Plan:
    id: str
    goal: str
    steps: dict[str, Step]
    rulings: list[Ruling] = field(default_factory=list)

    # -- reading ------------------------------------------------------------------------------

    def step(self, step_id: str) -> Step:
        if step_id not in self.steps:
            raise PlanError([f"no step {step_id!r} in this plan (its steps: {', '.join(self.steps)})"])
        return self.steps[step_id]

    def ready(self) -> list[Step]:
        """The steps that can start now: pending, with everything they wait for done."""
        return [s for s in self.steps.values()
                if s.status == PENDING and all(self.steps[a].status == DONE for a in s.after)]

    def ancestors(self, step_id: str) -> set[str]:
        seen: set[str] = set()
        todo = list(self.steps[step_id].after)
        while todo:
            current = todo.pop()
            if current not in seen:
                seen.add(current)
                todo.extend(self.steps[current].after)
        return seen

    @property
    def complete(self) -> bool:
        """Every step is done."""
        return all(s.status == DONE for s in self.steps.values())

    @property
    def ended(self) -> bool:
        """Nothing is left that can run, wait or be retried."""
        return not any(s.status in OPEN or (s.status == FAILED and s.attempts < MAX_ATTEMPTS)
                       for s in self.steps.values())

    # -- transitions ----------------------------------------------------------------------------

    def start(self, step_id: str) -> Step:
        step = self.step(step_id)
        if step not in self.ready():
            waiting = [a for a in step.after if self.steps[a].status != DONE]
            why = f"it waits for {', '.join(waiting)}" if waiting and step.status == PENDING else f"it is {step.status}"
            raise PlanError([f"step {step_id} can't start: {why}"])
        step.status = RUNNING
        step.attempts += 1
        return step

    def wait(self, step_id: str) -> Step:
        """The running step is waiting on the user (an approval card, a review, a question)."""
        step = self._expect(step_id, RUNNING)
        step.status = WAITING
        return step

    def resume(self, step_id: str) -> Step:
        step = self._expect(step_id, WAITING)
        step.status = RUNNING
        return step

    def finish(self, step_id: str, outcome: str, summary: str, evidence: bool = False) -> Step:
        """End a step. A tool step counts as done only with `evidence`: its tool returned a result
        in this plan (the caller checks that). A model's claim alone never makes a step done."""
        step = self._expect(step_id, RUNNING, WAITING)
        if outcome not in OUTCOMES:
            raise PlanError([f"outcome must be one of {', '.join(OUTCOMES)}, not {outcome!r}"])
        if outcome in ("done", "done_with_concerns") and step.uses_tool and not evidence:
            raise PlanError([f"step {step_id} isn't done: {step.tool} hasn't returned a result in this plan"])
        step.outcome, step.summary = outcome, _clip(summary, MAX_TEXT * 2)
        step.status = {"needs_input": WAITING, "failed": FAILED}.get(outcome, DONE)
        self._settle()
        return step

    def retry(self, step_id: str, ruling: Ruling) -> Step:
        """Try a failed step again, once, with a changed approach recorded as a ruling."""
        step = self._expect(step_id, FAILED)
        if step.attempts >= MAX_ATTEMPTS:
            raise PlanError([f"step {step_id} has failed {step.attempts} times: ask the user what to do"])
        self.rulings.append(ruling)
        step.status, step.outcome, step.summary = PENDING, None, ""
        self._settle()
        return step

    def skip(self, step_id: str, ruling: Ruling) -> Step:
        """Leave out a step that hasn't finished (the user removed it); what depends on it is blocked."""
        step = self._expect(step_id, PENDING, BLOCKED, WAITING, FAILED)
        self.rulings.append(ruling)
        step.status = SKIPPED
        self._settle()
        return step

    def stop(self) -> list[str]:
        """The user pressed Stop: every unfinished step is skipped. Returns the steps skipped."""
        stopped = [s.id for s in self.steps.values() if s.status in OPEN | {BLOCKED}]
        for step_id in stopped:
            self.steps[step_id].status = SKIPPED
        return stopped

    def _expect(self, step_id: str, *statuses: str) -> Step:
        step = self.step(step_id)
        if step.status not in statuses:
            raise PlanError([f"step {step_id} is {step.status}, not {' or '.join(statuses)}"])
        return step

    def _settle(self) -> None:
        """A pending step behind a failed or skipped one is blocked; a blocked step whose way is
        clear again (after a retry) is pending."""
        for step in self.steps.values():
            if step.status not in (PENDING, BLOCKED):
                continue
            stuck = any(self.steps[a].status in (FAILED, SKIPPED) for a in self.ancestors(step.id))
            step.status = BLOCKED if stuck else PENDING

    # -- storage shape (kept by state.py in T2) -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "steps": [asdict(s) for s in self.steps.values()],
                "rulings": [asdict(r) for r in self.rulings]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        steps = {s["id"]: Step(**{**s, "after": tuple(s.get("after", ()))}) for s in data["steps"]}
        return cls(id=data["id"], goal=data["goal"], steps=steps, rulings=[Ruling(**r) for r in data.get("rulings", [])])


def make(goal: Any, steps: Any, available: Iterable[str], plan_id: str | None = None) -> Plan:
    """Check a plan the model wrote and build it, or raise PlanError with every reason.

    `steps`: a list of {"does", "tool", "done_when", "after"?, "id"?}. A step's own `id` is only a
    name for `after` to refer to (its position, "1", "2"…, also works); the code assigns the real
    IDs, s1, s2…, in the order given. `available`: the Registry tools Aura can use now.
    """
    tools = set(available) - PLAN_TOOLS
    reasons: list[str] = []
    goal_text = goal.strip() if isinstance(goal, str) else ""
    if not goal_text:
        reasons.append("the plan needs a goal")
    elif len(goal_text) > MAX_GOAL:
        reasons.append(f"the goal is {len(goal_text)} characters; keep it under {MAX_GOAL}")
    if not isinstance(steps, list) or not steps:
        raise PlanError([*reasons, "the plan needs a list of steps"])
    if len(steps) > MAX_STEPS:
        reasons.append(f"the plan has {len(steps)} steps; at most {MAX_STEPS}: split it into smaller plans")

    names: dict[str, str] = {}  # the model's name for a step -> its real ID; then positions
    for i, raw in enumerate(steps, 1):
        own = str(raw.get("id", "")).strip() if isinstance(raw, dict) else ""
        if own in names:
            reasons.append(f"step {i}: the name {own!r} is already used by step {names[own][1:]}")
        elif own:
            names[own] = f"s{i}"
    for i in range(1, len(steps) + 1):
        names.setdefault(str(i), f"s{i}")  # a position works too, unless a name took it

    built: dict[str, Step] = {}
    for i, raw in enumerate(steps, 1):
        where = f"step {i}"
        if not isinstance(raw, dict):
            reasons.append(f"{where} isn't an object with does, tool and done_when")
            continue
        texts = {}
        for key in ("does", "done_when"):
            value = raw.get(key)
            text = value.strip() if isinstance(value, str) else ""
            if not text:
                reasons.append(f"{where} needs {key!r}" + (": one checkable result" if key == "done_when" else ""))
            elif len(text) > MAX_TEXT:
                reasons.append(f"{where}: {key!r} is {len(text)} characters; keep it to one line (under {MAX_TEXT})")
            texts[key] = text
        tool = str(raw.get("tool", "")).strip()
        if tool in PLAN_TOOLS:
            reasons.append(f"{where}: {tool} can't be a step of a plan")
        elif tool not in tools and tool not in BUILT_IN:
            reasons.append(f"{where}: no tool {tool!r} is available (use one Aura has now, or think or review)"
                           if tool else f"{where} needs a 'tool'")
        after_raw = raw.get("after", [])
        if isinstance(after_raw, str | int):
            after_raw = [after_raw]
        after: list[str] = []
        for ref in after_raw if isinstance(after_raw, list) else []:
            target = names.get(str(ref).strip())
            if target is None:
                reasons.append(f"{where} waits for {ref!r}, which isn't a step of this plan")
            elif target == f"s{i}":
                reasons.append(f"{where} can't wait for itself")
            elif target not in after:
                after.append(target)
        built[f"s{i}"] = Step(id=f"s{i}", does=texts.get("does", ""), tool=tool,
                              done_when=texts.get("done_when", ""), after=tuple(after))

    try:
        graphlib.TopologicalSorter({s.id: s.after for s in built.values()}).prepare()
    except graphlib.CycleError as error:
        cycle = error.args[1]
        reasons.append("steps wait for each other in a circle: " + " -> ".join(f"step {c[1:]}" for c in cycle))
    if reasons:
        raise PlanError(reasons)
    return Plan(id=plan_id or f"p-{secrets.token_hex(4)}", goal=goal_text, steps=built)


def _clip(text: str, limit: int) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
