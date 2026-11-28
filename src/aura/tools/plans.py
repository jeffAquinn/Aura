"""The plan tools (docs/orchestration.md, ticket T4): make_plan, plan_status, finish_step.

Aura writes a plan's steps; `aura.plans` checks them. A valid plan waits for the user's plan card
(make_plan's approval, default Deny); an invalid one comes straight back with every reason, and no
card. Approving a plan approves its direction only: each step's own tool keeps its own card.

A step is done only with evidence: its tool returned a result in this plan, in the conversation,
that no other step has counted. The model's word alone never finishes a tool step.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from typing import Annotated, Any

from langchain.tools import InjectedState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from aura.plans import (
    DONE,
    FAILED,
    MAX_ATTEMPTS,
    OPEN,
    REVIEW,
    RUNNING,
    WAITING,
    Plan,
    PlanError,
    PlanStore,
    Ruling,
    Step,
    make,
)

log = logging.getLogger(__name__)

PLAN_TOOL_NAMES = frozenset({"make_plan", "plan_status", "finish_step"})
INVALID = "Plan not valid"
STARTED = "Approved by the user and started."
MAX_INVALID = 2  # invalid plans per turn; the third attempt is refused


def checked(goal: Any, steps: Any, available: Iterable[str]) -> Plan:
    """The plan, checked (PlanError with every reason). Review steps arrive with ticket T7."""
    plan = make(goal, steps, available)
    if any(s.tool == REVIEW for s in plan.steps.values()):
        raise PlanError(["review steps aren't available yet: use think, and ask the user in your reply"])
    return plan


def card(goal: Any, steps: Any, available: Iterable[str], running: Plan | None) -> str:
    """The plan card's text (shown only for a plan that passed its checks)."""
    plan = checked(goal, steps, available)
    lines = [f"Start this plan? {plan.goal}", ""]
    for s in plan.steps.values():
        waits = f" (after {', '.join(s.after)})" if s.after else ""
        lines.append(f"{s.id}. {s.does} [{s.tool}]{waits}. Done when: {s.done_when}")
    lines += ["", ("Approving starts the plan. It doesn't approve its actions: every step that writes, runs "
                   "code, searches the web, hands work to the Developer or sends a message still asks you "
                   "first, and once this conversation holds your private data, anything else that leaves "
                   "this Mac asks too.")]
    if running is not None:
        lines.append(f"It replaces the plan still running, \"{running.goal}\" ({running.id}).")
    return "\n".join(lines)


def preview(goal: Any, steps: Any, available: Iterable[str], running: Plan | None) -> dict[str, Any] | None:
    """The plan card's content for the app (T5): the checked plan with its real step IDs, and the plan
    it replaces. None for a plan that doesn't pass its checks (it gets no card)."""
    try:
        plan = checked(goal, steps, available)
    except PlanError:
        return None
    return {"goal": plan.goal,
            "steps": [{"id": s.id, "does": s.does, "tool": s.tool, "after": list(s.after), "done_when": s.done_when}
                      for s in plan.steps.values()],
            "replaces": {"id": running.id, "goal": running.goal} if running is not None else None}


def summary(plan: Plan) -> dict[str, Any]:
    """A running plan for the app's progress line (T5)."""
    return {"id": plan.id, "goal": plan.goal, "done": sum(s.status == DONE for s in plan.steps.values()),
            "total": len(plan.steps),
            "steps": [{"id": s.id, "does": s.does, "status": s.status} for s in plan.steps.values()]}


def is_valid(goal: Any, steps: Any, available: Iterable[str]) -> bool:
    try:
        checked(goal, steps, available)
    except PlanError:
        return False
    return True


def _this_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[start + 1:]


def _rejected(m: ToolMessage) -> bool:
    data = m.additional_kwargs
    return data.get("lc_source") == "hitl_interrupt_decision" and data.get("lc_data", {}).get("decision") == "reject"


def _recorded(m: BaseMessage, plan: Plan) -> bool:
    """A plan tool's own result that recorded a change to `plan`: its start, or a finished step."""
    if not isinstance(m, ToolMessage) or m.status == "error":
        return False
    text = str(m.content)
    return ((m.name == "make_plan" and text.startswith(f"{STARTED}\nPlan {plan.id}:"))
            or (m.name == "finish_step" and text.startswith(f"Plan {plan.id}:")))


def evidence(plan: Plan, tool_name: str, messages: list[BaseMessage]) -> str | None:
    """The newest result of `tool_name` for the step being finished: a ToolMessage after the plan's
    last recorded change (so a call made for an earlier step never counts), not an error, not a denied
    card, and not counted by any step before. If that point was summarised away, only this turn's
    results count."""
    start = next((i for i in range(len(messages) - 1, -1, -1) if _recorded(messages[i], plan)), None)
    window = messages[start + 1:] if start is not None else _this_turn(messages)
    claimed = plan.claimed
    for m in reversed(window):
        if (isinstance(m, ToolMessage) and m.name == tool_name and m.status != "error" and not _rejected(m)
                and m.tool_call_id not in claimed):
            return m.tool_call_id
    return None


def _ruling(step: str, text: str) -> Ruling:
    parts = [p.strip() for p in re.split(r"\s*[—–]\s*", text.strip(), maxsplit=2)]
    parts += [""] * (3 - len(parts))
    return Ruling(step, parts[0], parts[1], parts[2])


def progress(plan: Plan) -> str:
    """What the model reads after each change: the steps, then exactly what to do next."""
    lines = [f"Plan {plan.id}: {plan.goal}"]
    for s in plan.steps.values():
        result = f" — {s.outcome.replace('_', ' ')}: {s.summary}" if s.outcome else ""
        waits = f", after {', '.join(s.after)}" if s.after else ""
        lines.append(f"- {s.id} [{s.status}] {s.does} ({s.tool}{waits}){result}")
    lines += [f"- {r.line()}" for r in plan.rulings]
    if plan.stopped:
        lines.append("The user stopped this plan. Don't continue it.")
    elif plan.complete:
        lines.append("Every step is done. Tell the user what the plan produced, citing each step's result.")
    elif plan.ended:
        lines.append("Nothing more can run: tell the user which steps were done, which failed and why, and ask "
                     "what they want to do.")
    else:
        waiting = [s for s in plan.steps.values() if s.status == "waiting"]
        ready = plan.ready()
        if waiting:
            lines.append(f"Waiting for the user on {', '.join(s.id for s in waiting)}: ask them, then finish it.")
        if ready:
            nxt = ready[0]
            how = "work it out yourself" if nxt.tool == "think" else f"call {nxt.tool}"
            lines.append(f"Next: {nxt.id}, {nxt.does} ({how}; done when: {nxt.done_when}), then "
                         f"finish_step(step=\"{nxt.id}\"). Go on through the steps without asking the user; "
                         "stop only when a card waits for them or a step needs their input.")
    return "\n".join(lines)


def plan_tools(store: PlanStore, available: Callable[[], set[str]]) -> list[BaseTool]:
    """`available`: the tool names Aura has registered now (a step must use one of them)."""

    def running() -> Plan | None:
        active = store.active()
        return active[-1] if active else None

    @tool
    def make_plan(goal: str, steps: list[dict[str, Any]], state: Annotated[dict, InjectedState]) -> str:
        """Plan a large job as steps, when it needs several tool calls that depend on each other (a research
        note from vault notes and web sources; reorganising a folder). Each step: {"does": one line, "tool":
        one of your tools or "think" (your own drafting or deciding), "done_when": one checkable result,
        "after": the steps it needs, by number, e.g. [1, 2]}. At most 20 steps. The user sees the plan and
        approves it; each step's tool still asks the user as usual."""
        messages = state.get("messages", [])
        asked = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if asked is not None and sum(c["name"] == "make_plan" for c in asked.tool_calls) > 1:
            return f"{INVALID}: one plan at a time. Send a single make_plan."
        turn = _this_turn(messages)
        invalid = sum(isinstance(m, ToolMessage) and m.name == "make_plan" and str(m.content).startswith(INVALID)
                      for m in turn)
        try:
            plan = checked(goal, steps, available())
        except PlanError as e:
            if invalid >= MAX_INVALID:  # only invalid plans are counted: a valid one always goes ahead
                return (f"{INVALID}: {invalid} plans were refused this turn, so no more are checked. Tell the "
                        "user what you are trying to do and ask how they want to proceed.")
            left = MAX_INVALID - invalid - 1
            return f"{INVALID}: " + "; ".join(e.reasons) + (f". Correct it and try again ({left} more try this turn)."
                                                            if left else ". Tell the user what you were trying to do.")
        previous = running()
        try:
            created = store.create(plan)  # first: a failure leaves the running plan as it was
        except Exception as e:  # a storage error is reported, never crashes the turn
            log.warning("couldn't start the plan", exc_info=True)
            return f"Not started: Aura couldn't save the plan ({type(e).__name__}). Tell the user."
        if previous is not None:  # the card said this plan replaces it, and the user approved
            try:
                store.change(previous.id, lambda p: p.stop())
            except Exception:
                log.warning("couldn't stop the replaced plan %s", previous.id, exc_info=True)
        return f"{STARTED}\n" + progress(created)

    @tool
    def plan_status() -> str:
        """The running plan: each step, its status and result, and what to do next."""
        plan = running()
        return progress(plan) if plan is not None else "No plan is running."

    @tool
    def finish_step(step: str, outcome: str, summary: str, state: Annotated[dict, InjectedState],
                    ruling: str = "") -> str:
        """End a step of the running plan. outcome: "done", "done_with_concerns" (say what in summary),
        "needs_input" (summary: the question for the user) or "failed" (summary: why). A tool step is done
        only after its tool returned a result. To retry a failed step once, first call its tool again with a
        changed approach, then finish it with ruling: "what changed — why — what it costs if wrong"."""
        plan = running()
        if plan is None:
            return "No plan is running."
        messages = state.get("messages", [])

        def apply(p: Plan) -> None:
            s = p.step(step)
            if s.status == "failed":  # a retry: allowed once, with a ruling
                if not ruling.strip():
                    raise PlanError([f"step {step} failed; to retry it, pass ruling: \"what changed — why — cost\""])
                p.retry(step, _ruling(step, ruling))
            if s.status == "pending":
                p.start(step)
            # A failed attempt claims its result too, so a retry needs a new one.
            proof = evidence(p, s.tool, messages) if s.uses_tool and outcome != "needs_input" else None
            p.finish(step, outcome, summary, evidence=proof or False)

        try:
            return progress(store.change(plan.id, apply))
        except PlanError as e:
            return "Not recorded: " + "; ".join(e.reasons) + "\n" + progress(store.get(plan.id))
        except Exception as e:  # a storage error is reported, never crashes the turn
            log.warning("couldn't save step %s of plan %s", step, plan.id, exc_info=True)
            return f"Not recorded: Aura couldn't save the plan ({type(e).__name__}). Tell the user."

    return [make_plan, plan_status, finish_step]


def step_for(plan: Plan, tool_name: str) -> Step | None:
    """The step a call of `tool_name` serves: the step already running or waiting with that tool, or
    else the first ready step that uses it. None: the call isn't part of the plan."""
    for s in plan.steps.values():
        if s.tool == tool_name and s.status in (RUNNING, WAITING):
            return s
    return next((s for s in plan.ready() if s.tool == tool_name), None)


class PlanSteps:
    """Links Aura's tool calls and cards to the running plan's steps (docs/orchestration.md T6).

    A step's tool running marks it running; a card for it marks it waiting, and the card carries
    "Step 3 of 5 of the plan …"; Allow sets it running again; Deny fails it, which blocks every step
    that depends on it. Bookkeeping only: it grants nothing, and a refused transition is logged, never
    raised, so a plan can never break a turn or a card.
    """

    def __init__(self, store: PlanStore | None):
        self.store = store

    def _running(self, strict: bool = False) -> Plan | None:
        """The running plan. `strict`: an error is raised (security checks fail closed); otherwise it
        is logged and means no plan (bookkeeping only)."""
        try:
            active = self.store.active() if self.store is not None else []
        except Exception:
            if strict:
                raise
            log.warning("couldn't read the running plan", exc_info=True)
            return None
        return active[-1] if active else None

    def line(self, tool_name: str) -> dict[str, Any] | None:
        """What a card raised for `tool_name` says about the plan, or None outside a plan."""
        if tool_name in PLAN_TOOL_NAMES or (plan := self._running()) is None:
            return None
        if (step := step_for(plan, tool_name)) is None:
            return None
        ids = list(plan.steps)
        return {"plan": plan.id, "goal": plan.goal, "step": step.id, "index": ids.index(step.id) + 1,
                "total": len(ids), "does": step.does}

    def _apply(self, tool_name: str, change: Callable[[Plan, Step], Any]) -> None:
        if tool_name in PLAN_TOOL_NAMES or (plan := self._running()) is None:
            return
        if (step := step_for(plan, tool_name)) is None:
            return
        try:
            self.store.change(plan.id, lambda p: change(p, p.steps[step.id]))
        except Exception:  # bookkeeping never breaks a tool call or a card
            log.warning("plan %s: couldn't record %s for step %s", plan.id, tool_name, step.id, exc_info=True)

    def running(self) -> bool:
        """Is a plan running? Fails closed for R2: if the plans can't be read, the answer is yes."""
        try:
            return self._running(strict=True) is not None
        except Exception:
            log.warning("couldn't read the running plan: treating one as running", exc_info=True)
            return True

    def tainted_by(self) -> str | None:
        """The private read the running plan made, in any conversation, or None. Fails closed."""
        try:
            plan = self._running(strict=True)
        except Exception:
            log.warning("couldn't read the running plan: treating it as tainted", exc_info=True)
            return "an unreadable plan"
        return (plan.tainted_by or None) if plan is not None else None

    def note_private_read(self, tool_name: str) -> None:
        """Shown in the plan's copy: which private read the plan made first. Display only; R2's own
        record is the conversation's (brain.Aura._taint_conversation)."""
        def change(p: Plan) -> None:
            p.tainted_by = p.tainted_by or tool_name
        try:
            plan = self._running(strict=True)
            if plan is not None and not plan.tainted_by:
                self.store.change(plan.id, change)
        except Exception:
            log.warning("couldn't note the private read %s on the running plan", tool_name, exc_info=True)

    def started(self, tool_name: str) -> None:
        """The tool is about to run: its step is running."""
        self._apply(tool_name, lambda p, s: p.start(s.id) if s.status == "pending" else None)

    def carded(self, tool_name: str) -> None:
        """A card waits for the user: its step is waiting."""
        def change(p: Plan, s: Step) -> None:
            if s.status == "pending":
                p.start(s.id)
            if s.status == RUNNING:
                p.wait(s.id)
        self._apply(tool_name, change)

    def answered(self, tool_name: str, allowed: bool) -> None:
        """The user answered the card: Allow, the step runs; Deny, the step fails."""
        def change(p: Plan, s: Step) -> None:
            if s.status != WAITING:
                return
            if allowed:
                p.resume(s.id)
            else:
                p.finish(s.id, "failed", f"You denied {tool_name} on its card.")
        self._apply(tool_name, change)


def steps_in_use(store: PlanStore | None) -> set[str]:
    """The running plan's own tools and its steps' tools: shown to the model every turn it runs."""
    if store is None or not (active := store.active()):
        return set()
    plan = active[-1]
    can_run = {s.tool for s in plan.steps.values()
               if s.status in OPEN or (s.status == FAILED and s.attempts < MAX_ATTEMPTS)}
    return {"plan_status", "finish_step"} | (can_run - {"think", "review"})
