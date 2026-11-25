"""A bounded retrospective. It suggests candidates; it never changes instructions or checks."""

import re


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:6_000]


def run(args: dict) -> str:
    summary, scope = clean(args.get("session_summary")), clean(args.get("scope")) or "the current workflow"
    lower = summary.lower()
    candidates = []
    if any(word in lower for word in ("couldn't find", "where", "navigate", "search", "location")):
        candidates.append(("High", "Navigation", "Add one short pointer to the authoritative document or module; do not duplicate its contents."))
    if any(word in lower for word in ("test", "bug", "regression", "failed", "error")):
        candidates.append(("High", "Automated check", "Identify a concrete test, type check, or lint rule that would catch this failure deterministically."))
    if any(word in lower for word in ("repeat", "again", "loop", "retry", "stuck")):
        candidates.append(("High", "Control flow", "Record the stop condition and a fallback path so the same failed action is not retried indefinitely."))
    if any(word in lower for word in ("slow", "token", "context", "large")):
        candidates.append(("Medium", "Context economy", "Create a compact reference or handoff instead of repeatedly rediscovering the same context."))
    if not candidates:
        candidates.append(("Medium", "Observation", "Capture one concrete failure mode before adding new rules or automation."))
    lines = ["# Retrospective", "", f"**Scope:** {scope}", "", "## Evidence supplied", f"- {summary or 'No session summary was supplied.'}", "", "## Improvement candidates"]
    for severity, kind, action in candidates[:5]:
        lines += [f"### {severity} — {kind}", f"- Candidate: {action}", "- Verify: name the exact future failure it should prevent.", "- Decision: review and approve before changing Aura, the Vault, or standards."]
    lines += ["", "## Rule", "Prefer a deterministic check over a prose rule; prefer a concise pointer over duplicated guidance."]
    return "\n".join(lines) + "\n"
