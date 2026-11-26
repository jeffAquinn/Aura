---
name: implement-spec
description: Implement an approved Aura spec or ticket set in small vertical slices, verifying after each slice and ending with an evidence report. Use proactively whenever starting non-trivial implementation work in the Aura repo after the scope is agreed.
---

# Implement spec

Claude Code version of Aura's `implement_spec` skill. In Aura, this skill only drafts a hand-off for `ask_developer`. Here, Claude Code is the developer, so it implements, but only within the approved scope.

## Before editing

1. Restate the approved scope: which files and areas may change, and the non-goals. If no approved spec exists, stop and use `grill-with-docs` first.
2. Read the affected code and its existing tests.
3. Check that any file name you plan to create is free (`tests/test_live.py` was once overwritten this way).

## Loop, one vertical slice at a time

1. Implement one slice at a declared seam with Edit/Write.
2. Run the smallest relevant check: a focused `uv run pytest -q <test>` with `--basetemp` in a named `/private/tmp/aura-pytest-*` directory (remove it afterwards); `swift build`/`scripts/build-app.sh` for app changes; `uv run ruff check` for Python.
3. Compare the result with the spec. Report any mismatch instead of expanding the scope.

Circuit breaker: after three attempts that end in the same failure, stop and report.

## Guardrails

- Keep the Integrity Rule, the Security Governor, approval-card defaults (Deny), and R1/R2/R5 framing intact. Changes to them need Gemma's review.
- Fake tokens in tests must be split (`"xoxb-" + "…"`) so GitHub push protection doesn't block the push.
- Don't run `live`-marked tests without asking.
- Commit only when the user asks. The user pushes; commits use the repo's noreply address, which is already configured.

## Evidence report

- Files changed and why
- Checks run, with their results
- Remaining risks, reversibility, and blast radius
- Anything that now needs Gemma's review
