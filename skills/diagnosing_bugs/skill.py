"""A diagnosis protocol. This does not run commands, alter code, or claim a root cause."""

import re


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:5_000]


def run(args: dict) -> str:
    symptom = clean(args.get("symptom")) or "No symptom was supplied."
    scope = clean(args.get("scope")) or "not yet bounded"
    phases = [
        ("1. Reproduce", "Define the smallest repeatable input, environment, and expected versus actual result."),
        ("2. Minimise", "Remove unrelated inputs until the failure still occurs at the smallest useful seam."),
        ("3. Hypothesise", "List competing causes and state what evidence would falsify each one."),
        ("4. Instrument", "Add or inspect the narrowest safe observation needed to distinguish the hypotheses."),
        ("5. Fix", "Choose the smallest change justified by the evidence; do not broaden scope."),
        ("6. Regression proof", "Demonstrate the original reproduction is now green and nearby behavior still works."),
        ("7. Retro", "If discovery or proof was difficult, propose one guardrail for review."),
    ]
    lines = ["# Diagnosis Plan", "", f"**Symptom:** {symptom}", f"**Scope:** {scope}", "", "## Gated phases"]
    for title, instruction in phases:
        lines += [f"### {title}", f"- {instruction}", "- Gate: record evidence before continuing."]
    lines += ["", "## Constraint", "A plausible explanation is not a diagnosis. Do not implement a fix until the reproduction and discriminating evidence exist."]
    return "\n".join(lines) + "\n"
