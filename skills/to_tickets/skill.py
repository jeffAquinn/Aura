"""A transparent ticket scaffold. It preserves ambiguity instead of inventing a dependency graph."""

import re

MAX_TICKETS = 10


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:8_000]


def items(plan: str) -> list[str]:
    raw = [clean(p).lstrip("-•0123456789. ") for p in re.split(r"\n+|;", plan)]
    return [p for p in raw if p][:MAX_TICKETS]


def run(args: dict) -> str:
    plan, scope = str(args.get("plan", "")), clean(args.get("scope")) or "not yet bounded"
    work = items(plan) or ["Clarify the first vertical slice of the plan."]
    lines = ["# Ticket Draft", "", f"**Scope:** {scope}", "", "## Tickets"]
    for i, item in enumerate(work, 1):
        blocker = "none — verify" if i == 1 else f"T{i - 1} only if this item depends on its interface or artifact; otherwise remove that edge."
        lines += [f"### T{i} — {item}", f"- Blocking edge: {blocker}", "- Acceptance: one observable outcome and one verification method.", "- Non-goal: do not absorb unrelated cleanup."]
    lines += ["", "## Before implementation", "Confirm the blocking edges with the user or specification. These are drafts, not an authoritative work graph."]
    return "\n".join(lines) + "\n"
