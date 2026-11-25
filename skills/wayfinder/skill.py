"""Produce a decision map, not an implementation plan pretending uncertainty is resolved."""

import re


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:4_000]


def run(args: dict) -> str:
    objective = clean(args.get("objective")) or "Define the desired outcome."
    scope = clean(args.get("scope")) or "not yet bounded"
    rows = [
        ("D1", "Outcome", "What observable result marks this objective complete?", "none"),
        ("D2", "Boundary", "Which project, Vault area, systems, and users are in scope?", "D1"),
        ("D3", "Constraints", "What must not change: data, cost, privacy, compatibility, or time?", "D1, D2"),
        ("D4", "Approach", "Which viable approach best satisfies the confirmed constraints?", "D2, D3"),
        ("D5", "Evidence", "What proof must exist before the work can be accepted?", "D4"),
    ]
    lines = ["# Wayfinder Decision Map", "", f"**Objective:** {objective}", f"**Scope:** {scope}", "", "## Decision tickets"]
    for ident, title, question, blocked in rows:
        lines += [f"### {ident} — {title}", f"- Decision needed: {question}", f"- Blocked by: {blocked}", "- Status: open"]
    lines += ["", "## Operating rule", "Resolve one decision at a time. Record the chosen answer and its reason before creating implementation tickets; unresolved decisions are not permission to guess."]
    return "\n".join(lines) + "\n"
