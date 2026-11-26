# Aura: working notes for Claude Code

Aura is a local Mac agent: a Python backend (`src/aura`, run with `uv`) and a SwiftUI menu-bar app (`app/`). See `README.md` for the architecture and `docs/sprint-*-design.md` for design decisions.

## Working routine: use the skills like a senior developer

Before starting a task, decide which skills apply and invoke them with the Skill tool. Don't rely on memory of what they say. Scale the process to the risk: a typo fix needs no skill, while a new feature goes through several. In one line, say which skill you're using and why.

| Situation | Skill |
|---|---|
| Large or vague objective, multi-session work, a new sprint | `wayfinder`, then `grill-with-docs` |
| New feature, or a request unclear about outcome, scope or acceptance | `grill-with-docs` (interview, then a spec for approval) |
| A new concept, a rename, or two terms that overlap | `domain-modeling` |
| Approved spec touching more than about three files or several steps | `to-tickets` |
| Writing the code for agreed work | `implement-spec` (vertical slices, verify each one) |
| Bug, failing or flaky test, regression, slowness | `diagnosing-bugs`: reproduce before fixing |
| A Claude/Anthropic API, SDK or model question (for example, the Claude Code engine in `src/aura/developer`) | `claude-api` |
| Code complete, before reporting done | `simplify`, then `code-review` on the diff |
| Changes to the Security Governor, approvals, sandbox, Keychain, connections or tool permissions | `security-review` as well |
| Confirming a UI or app change works for real | `run`. For the native app, see `aura-session-review` |
| README, docs, Aura review packets, UI strings, long commit messages | `unslop` before they ship |
| A session with an Aura plan and review | `aura-session-review` |
| End of a long or unfinished session; a save point; a review packet for Aura | `handoff` |
| Repeated failures, corrections from the user, or a hard-won fix | offer `retro` |
| Charts or dashboards | `dataviz` |

Skills don't override these rules: the user decides on commits, pushes, and sprint order, and nothing is published or sent without asking. Only use `code-review` at the `max` or `ultra` level when the user asks for it.

## Project rules

- **Aura's own skills are off-limits.** Don't modify `skills/*` with a `skill.toml` (source) or `~/Documents/Aura_Vault/Aura/skills` (the live registry). `skills/aura-session-review/SKILL.md` is the only Claude Code file in that folder.
- **Never run skill code on the host.** Skill tests run inside their sandbox images (the Workshop does that). `pytest` collects only `tests/`.
- Run Python with `uv run`. `/usr/bin/python3` is blocked by the unaccepted Xcode license.
- Tests: `uv run pytest -q <path> --basetemp /private/tmp/aura-pytest-<name>`, then remove that exact directory. Tests marked `live` use real services, cost money or time, and need the user's go-ahead.
- Lint: `uv run ruff check`. App: `scripts/build-app.sh`.
- Fake tokens in tests must be split (`"xoxb-" + "…"`), or GitHub push protection blocks the push.
- Check that a file name is free before creating a file.
- Never use `screencapture` (it triggers a Screen Recording prompt). Use CGWindowList for window checks.
- Never send `/new` from a second `uv run aura` process: it deletes the app's saved conversation. For scratch runs, use `AURA_DATA_DIR`, `AURA_VAULT_DIR`, and so on.
- Architecture and security changes are reviewed by Aura (the project architect; Gemma held this role until 2026-11-26) before they're tagged. Keep the Integrity Rule, approval cards defaulting to Deny, and the R1/R2/R5 framing intact.
- Circuit breaker: after three attempts that end in the same error, stop and report the evidence.
