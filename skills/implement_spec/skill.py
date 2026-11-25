"""Prepare, but never execute, a Developer handoff from an approved specification."""

import re


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:10_000]


def run(args: dict) -> str:
    spec = clean(args.get("spec"))
    scope = clean(args.get("scope"))
    lines = ["# Developer Handoff Draft", "", f"Allowed scope: {scope or 'Not specified — do not hand off yet.'}", "", "## Approved work"]
    lines.append(f"- {spec or 'No specification was supplied; return to planning.'}")
    lines += ["", "## Required execution loop", "1. Read the relevant scope and existing tests before editing.", "2. Implement one vertical slice at a time at a declared seam.", "3. Run the smallest relevant verification after each slice.", "4. Compare the result with the specification; report any mismatch rather than expanding scope.",
              "", "## Evidence required", "- Files changed and why", "- Commands or checks run, with their result", "- Remaining risks, reversibility, and blast radius", "", "## Approval boundary", "This skill cannot implement or hand work to a Developer. Aura must present this handoff for approval before calling ask_developer, and the user must approve resulting file changes."]
    return "\n".join(lines) + "\n"
