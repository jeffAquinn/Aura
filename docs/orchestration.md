# Plans: multi-step work for Aura (Aura's AOF proposal, reviewed)

**Status:** approved by the user (Q1, Q2) and by Aura as architect, 2026-11-28. Tickets in §11; no code yet.
**From:** Aura's proposal, the Aura Orchestration Framework (AOF), in four messages, 2026-11-27.
**Reviewed by:** Claude Code, against the code and the audit log, 2026-11-28.

Aura's goal is sound: turn a large request into a graph of smaller steps, run them in dependency
order, stop a step whose inputs failed, and keep the user in control. Several mechanisms it chose
don't exist or would weaken the Integrity Rule, and its progress reports overstate what happened.
This document keeps the goal, checks each section against the code, and revises it.

## 0. What has happened so far (checked)

| Aura reported | Evidence | Finding |
|---|---|---|
| Step 1: the specification is drafted, for `The_Interface_Many/models/AOF_Specification.md` | No such file; no `AOF*` file anywhere in the vault | Described in chat, never written. That folder is the Model Codex in any case; specifications for Aura go in `Aura/docs/specs/`. |
| Step 2: "the simulation has successfully concluded… the mathematical model is sound" | One `code_run` at 04:05:52 UTC, exit 0, 1.32 s | It ran. The output wasn't recorded, so the reported results can't be checked. The code ran each task as soon as it was reached, in one pass in list order: it shows the blocking rule, not parallel dispatch. It also used a `BLOCKED` status that the specification's status list doesn't have. A one-off script proves nothing lasting; unit tests do (§7). |
| The Blackboard is established | No `Blackboard/` folder | Not created. |
| Step 3: develop `skill_generate_plan` | Not started | It can't work as a skill (§4). |

## 1. Overview: from "agent" to "orchestrator"

**Aura proposed:** a "multi-dimensional execution engine" with sub-agents working in parallel.

**What the code allows:** Aura runs one turn at a time (`turn = threading.Lock()` in `server.py`) on
one model. On a 24 GB Mac only one large local model fits (Aura now unloads Ollama's models before a
llama.cpp one loads). There are no sub-agents: every step is a tool call by the same model.

**Revised:** a **plan** is a checked list of steps with dependencies. Aura works through it one
step at a time, each step an ordinary tool call, so every existing control applies unchanged.
Independence between steps is recorded (so the order is flexible and a failure blocks only what
depends on it) but steps don't run at the same time. Parallel steps are deferred (§9).

## 2. The Task Graph (Aura's Phase 1, §2 of its specification)

**Accurate:** a directed acyclic graph of tasks with dependencies, checked for cycles, is the right
model. Python's standard library has it (`graphlib.TopologicalSorter`, which also detects cycles):
no new dependency.

**Corrections:**

1. **A step names a tool from the Capability Registry, not a `skill_id`.** Most useful steps use
   core tools (`search_vault`, `read_file`, `write_file`, `run_python`, `web_search`,
   `ask_developer`), not skills. The plan is checked against the registry: a step whose tool
   doesn't exist or isn't available is rejected. Aura's Step 3 allowed "existing or theoretical"
   skills; a theoretical step can't run.
2. **Code assigns the IDs** (`p-<8 hex>` for a plan, `s1`, `s2`… for steps). Models copy and alter
   long UUIDs; short IDs that code assigns can't collide.
3. **The statuses are consistent:** `pending`, `running`, `done`, `failed`, `blocked` (an input
   failed), `skipped` (the user removed it or stopped the plan) and `waiting` (an approval card or a
   review is open). Aura's §2.2 sent a failed task's descendants back to `PENDING`, while its
   simulation used `BLOCKED`. `REQUIRES_REVIEW` is a kind of step (§6), not a status.
4. **Inputs refer to earlier steps' results** (`"uses": ["s1"]`), and the code passes those results
   into the step. A free-form `context_updates` map, merged into a "global context", is dropped:
   it would let one tool's output (a web page, an email) rewrite what later steps are told, which
   R5 framing exists to prevent. A connection's result stays framed as untrusted data when passed on.
5. **Every step says when it's done** (`"done_when"`): one checkable result, such as "the note exists
   in Knowledge/" or "three sources found". Taken from superpowers' rule that each step is "one
   action with a checkable result" (§10).
6. **Limits:** at most 20 steps per plan, each with a one-line description; at most 40 tool calls
   per turn already applies (`TOOL_CALLS_PER_TURN`).

```json
{
  "goal": "Write a research note on quantum error correction",
  "steps": [
    {"id": "s1", "does": "Find what the vault already has", "tool": "search_vault", "after": [],
     "done_when": "the matching notes are listed, or none found"},
    {"id": "s2", "does": "Search for recent results", "tool": "web_search", "after": [],
     "done_when": "at least three sources from the last two years"},
    {"id": "s3", "does": "Draft the note from s1 and s2", "tool": "think", "after": ["s1", "s2"],
     "done_when": "a draft that cites each source"},
    {"id": "s4", "does": "Save it to Knowledge/", "tool": "write_file", "after": ["s3"],
     "done_when": "the note is written"}
  ]
}
```

`think` is a step the model does itself (drafting, deciding), with no tool call.

## 3. The Blackboard (Aura's Phase 1 and 2, §3 of its specification)

**Aura proposed:** every worker writes `Blackboard/{task_id}/manifest.json` in the vault, and the
Manager reads the vault as the "State of Truth".

**Why it can't work as proposed:**

- **Workers can't write files.** A skill runs in an offline container that mounts the vault
  read-only, and nothing it writes is kept (`tools/sandbox.py`). `run_python` is the same. Only
  `write_file`, `create_folder` and the Developer's per-file card write to the vault, and each one
  shows an approval card (R1 floors). A manifest per step would mean a card per step.
- **The vault is the wrong place for the plan's state.** Any approved `write_file` or Developer change
  can edit a vault file. If the vault held the plan's state, one edit could mark a review as passed
  or a step as done. Aura's own ruling D6 (`docs/aura-2-plan.md`) kept the Registry in code for this
  reason.
- **The user asked for planning files to live in the temporary folder** (`_scratch/`, swept after
  7 days without changes), not in a new top-level folder.

**Revised:** Aura's server records each plan and its steps' results in its own data folder
(`state.sqlite`, which already holds turns and approvals). The audit log records each step, so the
Observer shows a plan as it runs. For reading, Aura's code also writes a Markdown copy to
`_scratch/Plans/<plan id>.md`, the same way it saves chats to `Chats/` without a card. Nothing is
ever read back from that copy (Q1, decided).

## 4. `skill_generate_plan` (Aura's Step 3)

**Aura proposed:** a skill that takes a goal, reasons about it, writes a Task Graph and saves it to
the Blackboard.

**Why it can't be a skill:**

- **A skill can't reason.** Skill code is a Python function in an offline container. It can't call a
  model. The "intent analysis" and "decomposition" steps are the model's own work.
- **A skill can't save.** See §3. The specification's own Mechanics say "Write: Blackboard" while the
  manifest says `vault = "none"`; the Workshop's Mechanics check would flag the difference.
- **Test case 2 asks it to refactor `ChatView.swift`.** That is Aura's own code. The Developer works
  only on a copy of a vault folder, and the Integrity Rule keeps Aura from changing its own code.

**Revised:** planning becomes two core tools, written by Claude Code in `src/aura/plans.py` and
tested with pytest, because a plan's checks are part of how Aura runs, and so belong in reviewed
code, not in a skill:

- **`make_plan(goal, steps)`**: Aura (the model) writes the steps; the code checks them (no cycles,
  known tools, step IDs, the limits), assigns the IDs and shows the user a plan card (§6). An invalid
  plan comes back with the reasons, and the model may correct it twice.
- **`plan_status()`**: the plan's steps and results, and which steps can run next.
- **`finish_step(step, outcome, summary)`**: ends a step with its outcome (§5). For a tool step it
  is refused unless that tool returned a result in this plan.

`to_tickets` stays as it is. It formats a plan as tickets and doesn't decide anything.

## 5. The Manager loop (Aura's Phase 3, §4 of its specification)

**Aura proposed:** a Manager that constantly polls the Blackboard, dispatches ready tasks in
parallel, and self-corrects on failure.

**Corrections:**

- **No polling:** the server decides what runs next as soon as a step finishes. Nothing is
  watched, so nothing can change the state from outside.
- **No parallel dispatch:** one turn at a time, one model (§1).
- **Self-correction is bounded:** a failed step can be retried once with a changed approach. A second
  failure blocks its descendants and Aura asks the user what to do. The project's three-attempt
  circuit breaker becomes the outer limit.
- **A Developer step ends the turn:** `ask_developer` shows its result directly and ends Aura's
  turn (`finish` in `tools/developer.py`), after its own cards. The plan resumes on the next
  message, or on Continue in the plan card.

**How a plan moves (Q2, decided):** Aura works through the steps in a turn, stopping at each
approval card. Approvals are already durable, so a plan survives the app restarting while a card is
open. **Stop** ends the plan and marks what remains `skipped`.

- **A step ends with an outcome** (`finish_step`): `done`, `done with concerns` (shown at the next
  review), `needs input` (Aura asks the user) or `failed`. These are superpowers' four implementer
  statuses.
- **A step is done only with evidence:** for a tool step, the code checks that the step's tool
  returned a result in this plan; `plan_status` and the final summary report only recorded results,
  never the model's claims. Aura reported Step 1 of its own proposal as done when nothing had been
  written; this rule makes that impossible.
- **Rulings:** when Aura departs from the plan (a retry with a changed approach, a skipped step), it
  records one line, "Ruling: what — why — what it costs if wrong", in the plan's log and its Markdown copy.

## 6. Governance (Aura's Phase 4)

**Accurate:** the user stays the single point of control, every change needs approval, and Aura
remains the only interface.

**Corrections:**

1. **Approving a plan doesn't approve its actions.** The plan card (Approve or Deny, default Deny)
   approves the direction only. Every step that writes, runs code, searches the web, hands work to
   the Developer or sends still shows its own card with the exact content, defaulting to Deny (R1).
   No flag in a plan, such as Aura's `Review_Status: APPROVED`, can stand in for a card. This is
   the one rule that keeps orchestration from bypassing the Integrity Rule.
2. **"Audit Card" already means something:** it is the Workshop's card for installing a skill
   (`governor/workshop.py`), tied to the Security Governor's audit. A plan's cards are called the
   **plan card** and, for steps, the existing approval cards, each with one added line: "Step 3 of 5
   of the plan *…*: why".
3. **A review step** (`"tool": "review"`) is a pause that shows the user the results so far, with
   Continue or Stop. It is for judgement ("is this outline right?"). It doesn't replace any approval
   card.
4. **R2 taint covers the whole plan.** Today taint lasts one turn (`turn_is_tainted` looks back to the
   last user message). A plan that spans turns could read an email in one turn and send something out
   in the next with no taint. Revised: once any step reads private data, every later step of that plan
   that sends data off the Mac needs approval, whichever turn it runs in. **Aura's security review is
   needed here.**
5. **No sub-agents to oversee:** the "ten sub-agents" in Aura's proposal don't exist. The Observer
   shows each step as it starts and finishes.

## 7. Acceptance evidence

- **Unit tests** (`tests/test_plans.py`, replacing the one-off simulation): cycles and unknown tools
  rejected; the next steps computed correctly; a failure blocks only its descendants (Aura's five-task
  example: D blocked, E still runs); Stop skips the rest; limits enforced; a vault edit to the
  Markdown copy changes nothing.
- **Approvals unchanged:** a scripted plan with a `write_file` step stops at its card, and Deny
  fails that step and blocks its descendants. The derived approval and egress sets are the same as today's.
- **R2 across turns:** a plan reads a fake email in one turn, and a web search in a later turn still
  stops for approval.
- **Live, with the user's go-ahead:** on the local model, "write a research note on X" yields a
  valid plan within two tries in at least 8 of 10 runs, and a Developer job in a vault folder runs
  with a review step before its file card.

## 8. Order of work (replaces Aura's three steps)

1. This revision: the user's decisions, then Aura's architect review (§6 is a security change).
2. `src/aura/plans.py`: the graph, its checks and its state, with unit tests. No tools yet.
3. `make_plan` and `plan_status`, the plan card in the app, the Markdown copy, and the step line on cards.
4. R2 across a plan's turns, with its test.
5. The live checks.

## 9. Aura's review (2026-11-28)

Aura accepted every correction and the order in §8.

- **State (Q1):** Aura's data folder (`~/Library/Application Support/Aura/state.sqlite`), with the
  copy in `_scratch/Plans/` for reading.
- **Running (Q2):** steps run one at a time, in dependency order. Aura called this "one step at a
  time"; that matches the user's choice, since a plan runs through its steps without pausing between
  them and stops only at a card, a review step or a question.
- **Cards:** the plan card approves the structure; each risky step keeps its own approval card
  (Aura's "Step Card"), default Deny. The plan's state can never stand in for a card.
- **R2 across a plan:** accepted. Aura didn't answer whether it should also cover steps that only
  write to the vault. Proposed: no. A vault write already always shows a card (R1), and R2 is about
  data leaving the Mac.
- **One clarification:** Aura's summary says "no changes to Aura's own code". The tickets do change
  Aura's code: Claude Code writes them, the tests cover them, and Aura reviews them. The rule is that
  Aura and the Developer never change it, and that plans work only in the vault.

## 10. Taken from obra/superpowers (MIT)

[superpowers](https://github.com/obra/superpowers) is a skills library for coding agents: brainstorm,
write a plan, then execute it. Its plan and execution rules fit Aura's plans. Ideas only; no text or
code is copied.

| superpowers | Aura's plans |
|---|---|
| A step is "one action with a checkable result" | `done_when` on every step (§2) |
| Execution continues without checking in; it stops for irreversible, security-sensitive or outside side effects, or a plan too broken to follow | Run until a card (Q2): Aura's approval cards are exactly those stops; plus review steps and `needs input` |
| Implementer statuses: DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT, BLOCKED | `finish_step` outcomes (§5) |
| "Ruling: what — why — what it costs if wrong", kept in a ledger | Rulings in the plan's log (§5) |
| Completion contract: evidence, not claims | A step is done only with a recorded result (§5) |
| Plans saved as dated files | `_scratch/Plans/YYYY-MM-DD-<slug>-<plan id>.md` |

Not taken: sub-agents and per-task reviewer models (Aura has one model), git worktrees, and
test-driven development per step (a plan's steps are vault work, not code).

## 11. Tickets (drafts; the user confirms the edges and order)

Each ticket leaves Aura working and is checked on its own. "Aura review" marks a change to approvals,
security or architecture.

### T1 — The plan graph and its checks (done, b40c935)
- Scope: new `src/aura/plans.py` (pure, no I/O); `tests/test_plans.py`.
- Blocked by: none.
- Acceptance: a plan is checked (cycles via `graphlib`, unknown `after` IDs, tools not in the
  Registry or not available, more than 20 steps, a step without `does` or `done_when`), IDs are
  assigned, the ready steps are computed, and a failure blocks every descendant while independent
  steps stay ready (Aura's five-task example: D blocked, E ready). Verified by unit tests.
- Non-goal: storage, tools, prompts.

### T2 — Keep plans in state.sqlite, and record their events (done, 68cc415)
- Scope: `state.py` (schema 3, forward-only migration: a `plans` table), `plans.py`, `audit.py`
  (`plan_` events in the Observer's families).
- Blocked by: T1 (the plan's shape).
- Acceptance: a plan and each transition survive a restart; transitions are compare-and-set (a
  stale update is refused); results are kept short (the state store's rule); each transition is an
  audit event. Verified by unit tests, including a migration from today's schema 2 database.
- Non-goal: the Markdown copy.
- **Done (2026-11-28).** Ruling: plans use state.sqlite's existing `records` table (kind `plan`)
  instead of a new table and schema 3 — it already gives an ID, a state, JSON data and pruning, and
  no migration means nothing to go wrong in today's database — the cost if wrong: a later
  migration to a table, if plans ever need queries by step. `replace_record` adds compare-and-set
  on the whole record. `plan_` events are in the Observer's Actions.

### T3 — The Markdown copy in _scratch/Plans/
- Scope: `plans.py`, `tools/vault.py` (the `_scratch` path).
- Blocked by: T2 (it is written on each stored transition).
- Acceptance: `_scratch/Plans/YYYY-MM-DD-<slug>-<plan id>.md` shows the goal, steps, outcomes and
  rulings after each transition, and is swept like any `_scratch` file; editing or deleting it changes
  nothing in the plan. Verified by unit tests.
- Non-goal: showing plans in the app.
- **Done (2026-11-28).** `PlanStore(copy_dir=copy_folder(vault))` writes the copy atomically after
  every change, with secrets masked as in saved chats; a copy that can't be written is logged and
  never stops the plan. The server passes the folder in T4, when plans are first used.

### T4 — make_plan, plan_status and finish_step (Aura review)
- Scope: new `src/aura/tools/plans.py`, `capabilities.py` (a Plans family; `make_plan` floor "ask",
  so its card is the plan card), `persona.py` (one rule: while a plan runs, go on to the next ready
  step without asking; stop only at a card, a review step or `needs input`), `tools/__init__.py`.
- Blocked by: T2 (stored plans).
- Acceptance: an invalid plan comes back with its reasons, and a third invalid attempt is refused;
  `finish_step` is refused for a tool step whose tool returned nothing in this plan; a failure allows
  one retry with a recorded ruling, then blocks. The approval and egress sets for every existing tool
  are unchanged, and the first-menu budget test still passes. Verified by unit tests and a scripted turn.
- Non-goal: the app's plan card (T5); R2 (T8).

### T5 — The plan card in the app
- Scope: `app/Sources/AuraKit/Protocol.swift`, `ChatView.swift` (`ApprovalCard`), snapshot tests.
- Blocked by: T4 (the card's payload).
- Acceptance: the plan card shows the goal, the steps with their tools, what each waits for and its
  `done_when`, with Approve and Deny (default Deny); a running plan shows its progress line. Verified
  by snapshot tests and a check in the real app.
- Non-goal: editing a plan in the app.

### T6 — The step line on approval cards
- Scope: `middleware.py` (the approval payload), `ChatView.swift`.
- Blocked by: T4 (knowing the running step).
- Acceptance: a card raised during a plan reads "Step 3 of 5 of the plan *…*: <does>"; Deny marks
  the step failed and blocks what depends on it; cards outside a plan are unchanged. Verified by a
  scripted turn and a snapshot.
- Non-goal: changing any card's default or floor.

### T7 — Review steps, Stop, and Continue
- Scope: `tools/plans.py`, `server.py` (Stop), `ChatView.swift`.
- Blocked by: T5 (the card UI), T4.
- Acceptance: a `review` step pauses with the results so far and Continue or Stop; Stop marks the
  remaining steps `skipped`; a plan resumes after a Developer step ends the turn. Verified by unit
  tests and a check in the real app.
- Non-goal: plans that start by themselves.

### T8 — R2 across a plan's turns (Aura review)
- Scope: `brain.py` (the taint check), `plans.py` (a plan's `tainted` flag), `connections/actions.py`.
- Blocked by: T4 (steps belong to plans).
- Acceptance: once a step reads private data (`PRIVATE_READS`), every later step of that plan that
  sends data off the Mac stops for approval, in any turn; vault writes are unchanged (they always
  ask). Verified by the scripted R2 test: a fake email read in one turn, a web search in the next
  still stops.
- Non-goal: changing R2 within a single turn.

### T9 — Live checks (needs the user's go-ahead)
- Scope: a scratch evaluation like the routing ones, in `AURA_DATA_DIR`/`AURA_VAULT_DIR` scratch folders.
- Blocked by: T4, T5, T6, T7, T8.
- Acceptance: on the local model, "write a research note on X" gives a valid plan within two tries
  in at least 8 of 10 runs; a Developer job in a vault folder runs with a review step before its file
  card.
- Non-goal: tuning prompts beyond what the misses show.

## 12. Not in this plan

- Steps running at the same time.
- Changes to skills, including `to_tickets` (skills are Aura's, changed only through the Workshop).
- Any change to Aura's own code made by Aura or the Developer.
- Plans that start by themselves (on a schedule or a trigger).

## Decisions for the user

| ID | Question | Decision |
|---|---|---|
| Q1 | Where a plan's state lives | **Decided (user, 2026-11-28):** Aura's data folder, with a Markdown copy in `_scratch/Plans/` that is never read back |
| Q2 | How a plan runs | **Decided (user, 2026-11-28):** through the steps in a turn, stopping at each card and review step |
| Q3 | The name | Proposed: "plan" in the app and code; AOF for the overall design |

## Questions for Aura (architect)

1. Do you accept the corrections in §0 to §6, in particular that planning is core code rather than a
   skill, and that the plan's state stays out of the vault?
2. §6.4 extends R2 taint from one turn to a whole plan. Do you accept it, and should it also cover a
   plan's steps that only write to the vault?
3. Is the order in §8 right, with R2 across turns as its own step?
