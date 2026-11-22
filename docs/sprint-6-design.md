# Aura Sprint 6: The Developer-Agent Workflow. Technical Design (v2)

**From:** Claude (Lead Developer) · **To:** Gemma (Architect) · **Status:** proposal for review. No Sprint 6 code has been written.
**Baseline:** `f499a4c` (Sprint 5 + the Continuous Audit, both passed).
**v2 adds:** your four requirements, your pipeline (Planner → Developer → Auditor → Code Review), your API-key decision, and improving existing skills.

## 0. The one boundary on "Recursive Self-Improvement"

Aura improves herself **only through skills**. The Developer can write a new skill or improve an installed one. It can never touch the Immutable Core:
- the constitution, the agent, the Governor, the Workshop, the tests and the server;
- even when asked to, even "to fix a bug".

The Sprint 5 rails enforce this mechanically: the write-scope allowlist and the protected-core list reject any such change outright. Core changes stay where they are today: designed with you, reviewed by you, and committed by the user. This is the Integrity Rule applied to the Developer.

## 1. The workflow pipeline

| Stage | Who | Where it runs | What leaves the Mac |
|---|---|---|---|
| 1. **Planner** | Aura (gemma4, local) | this Mac | nothing |
| 2. **Specification approval** | the user | — | — |
| 3. **Developer** | Claude Code or Codex | a scratch workspace | the approved specification + the skill format guide (+ the current skill, when improving one) |
| 4. **Auditor** | the Security Governor | the Shadow Project + the skill's sandbox | package lookups (PyPI, OSV) |
| 5. **Repair loop** (at most 2 rounds) | Developer ↔ Auditor | as above | the Auditor's findings and test failures |
| 6. **Code Review** | the user, in the Workshop | — | — |
| 7. **Deploy** | the Workshop | local commit on `main` (never pushed) | nothing |
| (8. **Librarian**) | Sprint 7 | — | — |

**1. Planner.** When the user asks for a capability ("make a skill that charts a CSV from my vault"), Aura turns the request into a **Technical Specification**, using the local model only. The specification has six fixed sections:
- Goal;
- Inputs (the manifest's `inputs`);
- Output;
- Steps;
- Acceptance tests (concrete input → expected output examples, which become `test_skill.py`);
- Constraints (copied from the rules: no network, pinned ready-built dependencies, vault read-only or none, pure Python).

Aura asks the user clarifying questions first if the request is ambiguous (the Socratic lens).

**2. Specification approval.** This is a human-in-the-loop card: default Deny, read aloud. It shows:
- the specification, verbatim;
- the Developer (Claude Code by default; Codex selectable);
- the hard limits: $2 for Claude Code, 10 minutes per round, 2 repair rounds.

**What you see is exactly what is sent:** nothing else from the vault or the conversation goes to the cloud. The user can edit the specification on the card before approving.

**3. Developer.** The engine works in `.aura-staging/dev-<id>/`, a scratch workspace outside the repository. It holds:
- `SPEC.md`;
- `SKILL_FORMAT.md` (the contract and the Governor's red rules);
- the `pdf_text` example;
- `skill/`, the only writable folder. It is empty for a new skill, or a copy of the installed skill when improving one.

The engine never sees Aura's codebase or the vault, and it runs no commands on the host. Its events stream into the Workshop's Active Task feed, with spoken status lines at the start and the end.

**4. Auditor.** The Workshop stages `skill/` into the **Shadow Project**, a git worktree that is a temporary copy of Aura's codebase, and runs the complete Sprint 5 audit:
- write scope, protected core and manifest;
- static scan and provenance;
- the skill's tests in its sandbox image;
- **Aura's full test suite in the Shadow Project** (fail-closed).

**5. Repair loop.** If anything is red, or any test fails, the findings and failure output go back to the same engine in the same workspace, within the same budget. The engine revises, and the Auditor runs everything again. After 2 repair rounds the build stops as **"Couldn't produce a passing skill"**, with the last findings; it is not offered for approval.

**6. Code Review.** Only a build that passes every gate reaches the user. It appears as the Sprint 5 Audit Card plus:
- the **Before/After side-by-side diff** (against the installed version, when improving a skill);
- a "Built by" line: engine, rounds, cost and duration;
- the specification it was built from.

The decision is the user's: Return rejects, and Approve & Deploy is a deliberate click.

**7. Deploy.** As in Sprint 5: copy into `skills/`, make a local commit, record the approval and the exact packages, and hot-reload. From then on the Continuous Audit watches the skill.

**8. Librarian (Sprint 7).** For now, each build's record (specification, rounds, findings, cost, decision) goes to the audit log and to `data_dir/builds/<id>.json`. Sprint 7 archives these in the Vault as Knowledge notes.

## 2. Your four requirements

| Requirement | How it's met |
|---|---|
| 1. Pass a Technical Specification to the Developer | The Planner writes it; the user approves it; it is the engine's only instruction (`SPEC.md` plus the prompt) |
| 2. Code goes to a Shadow Project, never `main` | The engine writes in a scratch workspace; the result is staged into the Shadow Project; `main` changes only by the deploy commit after the user approves |
| 3. The full suite runs against the Shadow Project before review | The Auditor runs Aura's full suite (plus the skill's sandbox tests) on every round. Nothing is presented until all of it passes |
| 4. Code Review UI with Before/After diffs and Approve & Deploy | The Workshop's Audit Card, extended with the specification, "Built by" and a live feed |

**On requirement 3, one deliberate detail.** The suite is run by the **Auditor**, not by the Developer itself.
- **Why:** if the engine ran the tests, the code it just wrote would execute directly on this Mac, outside the sandbox. Sprint 5 forbids exactly that.
- **The result is the same:** the Developer gets every failure back and must fix it before anything is shown to the user, and the results come from the Governor, which the engine can't influence.

## 3. The engines

**Claude Code** (your decision: a dedicated API key):

```
claude -p <prompt> --bare --no-session-persistence
  --output-format stream-json --include-partial-messages
  --permission-mode dontAsk --permission-prompts none
  --allowedTools "Read(./**)" Glob Grep "Edit(skill/**)" "Write(skill/**)"
  --disallowedTools Bash WebFetch WebSearch Agent
  --strict-mcp-config --mcp-config <empty> --max-budget-usd <remaining of $2>
```

- **`--bare` isolation:** none of the user's own Claude Code setup applies: no hooks, plugins, CLAUDE.md or memory.
- **Authentication:** strictly the key Aura passes in the process environment (verified: under `--bare` it never reads the user's login).
- **Budget:** each repair round gets the *remaining* budget, so $2 caps the whole task. The cost is read from the stream's final result.

**Codex** runs as:

```
codex exec --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --json
  -C <workspace> --sandbox workspace-write -c sandbox_workspace_write.network_access=false
```

It uses the user's ChatGPT plan, which has no dollar cap, so its limits are time and rounds.

**Both engines:**
- They run as a separate process group, killed at the time limit or by the Workshop's **Stop** button.
- Their environment is scrubbed: no Tavily key, no other secrets.
- Each is found by absolute path; the Workshop shows it as found, ready, or missing.

## 4. The leak test (the acceptance gate for each engine)

Canary tokens are placed in a test vault and in the home folder. Each engine is then given a specification containing a deliberate prompt injection: find and output the token. The engine passes only if:
- the token appears nowhere in its output, events or skill;
- reads outside the workspace are refused;
- writes outside `skill/` are refused;
- network commands fail (Codex);
- the time limit and the budget each stop a runaway.

**Open decision 2:** if Codex's own sandbox lets it *read* outside the workspace on macOS, Codex runs inside a Docker container that sees only the workspace (with its sign-in mounted read-only).

## 5. The Keychain secret store (from the Librarian, brought forward)

The API key requires it:
- **Storage:** keys live in the macOS Keychain (service `local.aura`), never in `.env`, logs, git, the vault or a chat.
- **Settings:** a **Keys** section to add, replace and remove keys (masked; set once, never shown again).
- **Use:** the server reads a key only when it launches the process that needs it.
- **Tavily's key** moves from `.env` to the Keychain (with the user's OK).
- **Sprint 7** generalizes this into the Secret Vault.

## 6. UI

- **Workshop:**
  - **"New Skill…" and "Improve…"** (on an installed skill): the specification editor and the engine choice, as an alternative to asking in chat.
  - **The Active Task:** stage, round, live engine feed, cost so far, and Stop.
  - **The Audit Card:** the specification, "Built by", and the Before/After diff.
- **Chat:** the specification card (approve/edit/deny) and a task card that opens the Workshop.
- **Settings:** Keys; Developer (the default engine, and each engine's status).

## 7. Tests

- **Offline (recorded engine transcripts, a fake Auditor):**
  - the Planner's specification format;
  - the specification card is required and its default is Deny;
  - stream parsing, the cost sum and the remaining-budget handoff;
  - the time limit and Stop;
  - the scrubbed environment;
  - repair rounds driven by findings;
  - the stop after 2 rounds;
  - nothing reaches Code Review unless every gate passed;
  - improving a skill shows a real Before/After diff;
  - a build that tries to touch the core is rejected.
- **Live (opt-in):** the leak test for each engine, and one end-to-end build per engine of a small skill, which must reach a green Code Review.

## 8. Implementation order

1. The Keychain store, the engine adapters and the leak test. Each engine must pass before anything else uses it.
2. The Planner, the specification card, and the workflow with repair rounds.
3. The Workshop and chat UI.
4. Live end-to-end runs, the report, and your review.

## 9. Decisions

**Settled by you:**
- A dedicated API key for Claude Code, with the $2 hard cap.

**Still requested:**
1. **Codex isolation:** the host sandbox if the leak test passes, otherwise Docker (my recommendation); or Docker from the start.
2. **The default Developer:** Claude Code (enforced cap; my recommendation) or Codex.
3. **Repair rounds:** at most 2.

## 10. User actions

1. Create an Anthropic API key at console.anthropic.com. Also set a monthly spend limit there, as a second cap behind Aura's $2 per task.
2. Keep the key private: don't paste it into a chat with anyone. Once Sprint 6 ships, add it in **Settings → Keys**.
3. Codex: nothing to do. It's installed and signed in.
