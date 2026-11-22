# Aura: The Evolution Era (the Autonomy Protocol). Technical Design

**From:** Claude (Lead Developer) · **To:** Gemma (Architect) · **Status:** proposal for review. No Sprint 5 code has been written.
**Baseline:** tag `sprint-4`. Since then: your Soul directive (verbatim, first in every prompt), your Integrity Rule (in the constitution), approval cards for code runs, and Kokoro neural voices.

## 0. Sequencing (the user's request) and spike results

The user asked for Sprint 5 first and for Claude Code and Codex to follow. Your Autonomy Protocol separates cleanly along that line:
- **Sprint 5, the rails.** The Skill Registry, staging, the test gate, the Security Governor, rule-based provenance, the Audit Card, and the Workshop UI. It is engine-agnostic.
- **Sprint 6, the developers.** Claude Code and Codex plug into those rails as `Developer_Tool` engines. Nothing in Sprint 5 depends on them, and they get no path around the rails.

**Verified on this Mac:**
- **Claude Code 2.1.330** is installed. Its command line supports everything a governed engine needs:
  - **Headless mode:** `-p`.
  - **A live progress feed:** `--output-format stream-json --include-partial-messages`, for the Active Task stream.
  - **A read-only planning mode:** `--permission-mode plan`.
  - **Refuse anything not allowed:** `--permission-mode dontAsk` with `--allowedTools` / `--disallowedTools`.
  - **Isolation from the user's own setup:** `--bare` / `--setting-sources` (no personal settings, hooks or plugins).
  - **A hard spending cap:** `--max-budget-usd`.
  - **No saved sessions:** `--no-session-persistence`.
- **Codex** is not installed. The user installs it and logs in before Sprint 6.
- **Isolated copies of the repo:** `git worktree` works on this repo, so every task can get its own throwaway branch and folder.
- **Rule-based provenance, prototyped** against the live PyPI and OSV (Google's open-source vulnerability database) services:

| Package | Verdict | Why (rules only, no LLM) |
|---|---|---|
| `pypdf` 7.1.1 | 🟢 GREEN | 12 years old, 127 releases, ships ready-built packages, 1 required dependency, no known vulnerabilities |
| `pyyaml` 5.3 | 🔴 RED | 4 known vulnerabilities (OSV: GHSA-6757-jp84-gxfx, GHSA-8q59-q68h-6hv4, PYSEC-2020-96, …) |
| `reqeusts` | 🔴 RED | Not on PyPI; one edit away from `requests`, so a likely typosquat |

## 1. The central safety decision: expansion means new skills, never core edits

Your example plan says "updating `brain.py`". I propose the opposite as a hard rule: **a new capability is always a new folder in `skills/`. The pipeline can never modify Aura's core.**
- **The registry finds skills on its own.** Adding a tool needs no change to `brain.py`, the persona, the server or the tools.
- **The Governor enforces this deterministically.** Every staged diff is checked against a path allowlist, `skills/<this-skill>/**` only. Any change outside it is rejected automatically, whatever the developer engine claims.
- **It enforces your Integrity Rule mechanically, not just by asking.** The constitution, the Soul directive, the core rules and the Governor itself are all outside the allowlist.
- **Existing tests are read-only for developers.** An engine cannot "fix" a failing test by weakening it. New skills bring their own tests, in their own folder.

## 2. The Skill Registry (Phase 1)

```
skills/
  pdf_text/
    skill.toml        manifest: name, version, purpose, inputs, permissions, dependencies (pinned)
    skill.py          def run(args: dict) -> str   one entry point, no cross-skill imports
    test_skill.py     required: tests that run in the sandbox
```

```toml
name = "pdf_text"
version = "1.0.0"
purpose = "Extract the text of a PDF in the vault."
inputs = { path = "vault-relative path to a .pdf" }
permissions = { network = false, vault = "read" }       # Sprint 5: network is never granted
dependencies = ["pypdf==7.1.1"]
```

- **Discovery.**
  - **When it happens:** at server start and after every deploy, the registry scans `skills/` and validates each manifest against a strict schema.
  - **Registration:** valid, *approved* skills become agent tools named `skill_<name>`. A skill whose files don't match their approved content hash is not loaded.
  - **New folders:** a folder that appears without approval (e.g. the user drops one in) is listed as **pending**, and it is registered only through the Audit Card (§4).
- **Execution (Phase 3).**
  - **Containers:** every skill runs in the existing Docker sandbox, never on the host.
  - **Images:** each skill gets its own image layer, `aura-skill-<name>:<content-hash>`, built from `aura-sandbox` plus the skill's pinned dependencies.
  - **Limits:** network off, vault mounted read-only, CPU and memory limits, a 30-second timeout, and a fresh container per call. Libraries are **never** installed into Aura's own Python.
- **Hot reload.** After a deploy the registry re-scans and the agent is rebuilt with the new tool list. Conversation memory survives, because the checkpointer is kept, and no server restart is needed.
- **Rollback.** Every skill has an on/off switch in the Workshop, and deploys are ordinary git commits, so they can be reverted.

## 3. The staging workflow (Plan → Code → Test → Commit)

| Stage | What happens | Human gate |
|---|---|---|
| 1. **Inquiry** | Aura turns the request into a spec: purpose, inputs and outputs, dependencies, permissions, tests, rollback. `check_library` (read-only, no gate) runs the provenance rules on candidate libraries | — |
| 2. **Plan Approval** | Card: the spec, the files to be created, dependencies with their provenance verdicts, permissions, the engine and its budget cap | **Approve plan** (Deny is the default) |
| 3. **Staging** | `git worktree add .aura-staging/<task> -b aura/skill-<name>` gives an isolated copy of the repo. Sprint 5: a skill is staged from a folder the user supplies. Sprint 6: a developer engine writes it | — |
| 4. **Audit** (Governor, §4) | Path allowlist → manifest schema → static scan → dependency provenance → build the skill image → the skill's tests **and the full existing suite**, in staging. Any failure means the task is rejected and the worktree deleted | — |
| 5. **Audit Card** | The diff, test results, risk badge with reasons, and the verified/unverified split (§5) | **Approve & Deploy** (Reject is the default) |
| 6. **Deploy** | Fast-forward merge into `main` with a commit (local; never pushed automatically) → registry hot-reload → the skill's smoke test against the live registry → result reported (and spoken) | — |

## 4. The Security Governor (deterministic; never the LLM)

| Check | Rule | Effect |
|---|---|---|
| Write scope | The diff touches only `skills/<name>/**` | Anything else: **automatic reject** |
| Protected files | Hashes of constitution, persona core, Soul directive, Governor, server security, vault whitelist and sandbox unchanged | Any change: **automatic reject** |
| Manifest | Schema-valid; dependencies pinned; `network = false` (Sprint 5) | Invalid: reject |
| Static scan (AST) | Flags `subprocess`, `os.system`, `ctypes`, `socket`/HTTP clients, `eval`/`exec`, paths outside `/vault` and `/tmp` | 🟡 or 🔴 with the exact line |
| Provenance (per dependency, including indirect ones resolved in the sandbox) | Exists on PyPI; age and number of releases; known vulnerabilities (OSV); typosquat distance from popular packages; whether it ships ready-built packages or runs its own `setup.py` at install (if so, `setup.py` is scanned for `os.system`, network or file access); unknown/unverified dependencies | 🟢 / 🟡 / 🔴 with reasons |
| Tests | The skill's own tests and the existing suite all pass in staging | Any failure: reject and wipe |
| Human | Plan Approval and Audit Card, both defaulting to Deny | Nothing deploys without an affirmative **Allow** |

**Risk badge.** 🔴 if any rule fires on: known vulnerabilities, typosquat, network or system-binary use, or an unscanned install script. 🟡 for a young package (< 90 days or < 3 releases), unverified dependencies, an install script that was scanned clean, or file access beyond the vault. Otherwise 🟢.

**Red is blocking in Sprint 5.** A 🔴 task cannot be deployed. Whether a user can override red is a decision for you (§8).

## 5. Your constitutional protocols, made concrete

- **Inquiry (Socratic: epistemic humility).**
  - **The questions:** for every candidate library, Aura answers a fixed set: *What does it do? Who publishes it, and for how long? What will it touch? What does it pull in? What is unknown?*
  - **The evidence ledger:** each answer is recorded with its source (PyPI metadata, OSV, a static-scan line, a docs URL) or marked **unknown**.
  - **Unknowns count against it:** an unknown can never improve the verdict. It moves the risk to at least 🟡.
- **Order (Platonic: hierarchy, purpose, order).**
  - **One shape for every skill:** one folder, one manifest, one entry point and its own tests. No cross-skill imports, and naming rules apply.
  - **One fixed plan outline:** Purpose → Inputs and Outputs → Dependencies → Permissions → Tests → Rollback.
  - **Enforced, not advised:** the Governor checks this structure, so the registry stays orderly by construction.
- **Truth (unverified facts and ambiguous data).**
  - **Two sections on the Audit Card:** **Verified by rules** (deterministic results) and **Aura's assessment (unverified)**, the model's reasoning, labelled as such.
  - **The LLM cannot move the risk score.** Your "circular logic" point is built in: only rules set the badge.
  - **Ambiguity becomes a question, not a guess:** conflicting or missing data ("no repository URL", "licence unclear") is listed as an open question.

## 6. The Developer_Tool (Sprint 6, designed now)

Engines implement one small interface: `plan(spec, workdir) -> PlanText` and `implement(plan, workdir) -> stream of events`. They run **only** inside the staging worktree, and the Governor checks their output exactly as it checks any other diff.

- **Claude Code.**
  - **The command:** `claude -p --bare --no-session-persistence --output-format stream-json --include-partial-messages --max-budget-usd <cap>`, run with the staging worktree as its working directory.
    - **Planning:** `--permission-mode plan`, which is read-only.
    - **Implementation:** `--permission-mode dontAsk --allowedTools "Read,Glob,Grep,Edit(skills/<name>/**),Write(skills/<name>/**),Bash(uv run pytest skills/<name>:*)"`. Anything else is refused.
  - **The Workshop's progress feed:** its stream-json events become the Active Task stream ("Writing skills/pdf_text/skill.py…", "Running the skill's tests…").
- **Codex.** `codex exec` in its workspace-write sandbox, with `--json` events. To be verified once it's installed; the governance is the same.
- **Cost.** Both engines use the user's own accounts. The Plan Approval card shows the engine and its hard budget cap, and a task stops when it hits the cap.

## 7. The UI: the Autonomy Console

- **Panel: compact cards in the chat.**
  - **Active Task:** stage pills (Planning ▸ Coding ▸ Auditing ▸ Awaiting you ▸ Deployed) with one live status line.
  - **Plan Approval:** the spec and the provenance badges.
  - **Audit:** the risk badge, test counts, and a **Review changes…** button.
  - **Spoken:** every card is read aloud (the verbal audit trail).
- **The Workshop window** (opened from the card or the menu bar). It is resizable, because a 420-pt panel is too small for code review.
  - **Proposed Changes:** a side-by-side diff with per-file tabs; additions green, changes amber, removals red; syntax-coloured.
  - **Risk badge:** the reasons below it, split into *Verified by rules* and *Aura's assessment (unverified)*.
  - **Test results:** counts, with failures expandable.
  - **Two buttons:** **Approve & Deploy**, and **Reject**. Reject is the default (Return); deploy needs a deliberate click.
  - **System Status:** model loaded or idle, memory (server, model, Docker), sandbox health, and the skill list with on/off switches and version history.
- **New events and endpoints:** `task` SSE events (stage, progress, cards); `GET /v1/tasks`, `GET /v1/tasks/{id}/diff`, `POST /v1/tasks/{id}/plan-decision`, `POST /v1/tasks/{id}/deploy-decision`; and `GET/PATCH /v1/skills`. All use the existing token, Host and JSON rules.

## 8. Decisions requested

1. **Expansion only through new skills.** The pipeline never edits `brain.py` or other core files. *Recommended: yes.*
2. **Calling an installed skill.** Skills were audited, tested and approved at install, and they run sandboxed and offline. *Recommended: no per-call card, but a spoken status line.* Your code-run mandate continues to cover arbitrary `run_python`. Or should every skill call also show a card?
3. **🔴 risk.** *Recommended: blocking in Sprint 5, with no override.* Revisit with real usage.
4. **Network-enabled skills.** *Recommended: not allowed in Sprint 5.*
5. **Deploy target.** A local commit on `main`, never pushed automatically. *Recommended: yes.* The user pushes when they choose.
6. **Engine budget cap** (Sprint 6). *Recommended: a $2 default per task, adjustable in the Workshop.*

## 9. User actions
- **Before Sprint 6:** install Codex (`npm install -g @openai/codex`) and sign in. Claude Code is already installed and signed in.
- **Docker Desktop must be running.** Skills live in the sandbox, so Docker becomes essential rather than optional.
