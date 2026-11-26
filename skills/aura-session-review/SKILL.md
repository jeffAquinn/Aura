---
name: aura-session-review
description: Start Aura and run a bounded plan-review-implementation loop for one Aura-root-project improvement. Use at the start of a coding session when Aura should plan and review while Claude Code implements.
---

# Aura session review

Use Aura as the product architect and adversarial reviewer. Claude Code or Codex remains the lead developer: inspect the repository, decide the safe implementation, edit root-project code, run verification, and report concrete evidence.

## Phase routing

This is a selective pipeline, not an instruction to run every available skill. Start each bounded request by naming the smallest applicable phases and the evidence that unlocks the next one.

| Need | Use before implementation | Stop condition |
| --- | --- | --- |
| Unclear, cross-cutting objective | `wayfinder` | Outcome, scope, constraints, and proof are explicit. |
| Existing project conventions or written decisions | `grill_with_docs`; `domain_modeling` when terminology matters | Relevant documents and terms are identified. |
| Defect or performance regression | `diagnosing_bugs` | A minimal reproduction and discriminating evidence exist. |
| Notes or PDF material is the source of truth | `synthesize` or `pdf_text` | Evidence is condensed and scoped. |
| Approved work has several independent slices | `to_tickets`, then `implement_spec` | One smallest vertical slice is ready for the Developer. |
| Completion or a paused session | `handoff`; use `retro` only when workflow evidence warrants it | Next owner can continue without rediscovery. |
| User-requested editorial cleanup | `unslop` | The user has reviewed the suggested prose edits. |

Do not claim a skill ran unless its actual output is in the conversation. In the HOL Terminal, the connected Developer is intentionally unable to invoke host skills; request the specific approved Aura skill and its inputs from the user/Aura, then incorporate the returned evidence. Do not infer, paraphrase, or reconstruct a missing skill result from surrounding files. This avoids inventing tool results and keeps the Developer's authority limited to the disposable Vault workspace.

## Start

1. Load the `anthropic-skills:computer-use` skill, then launch the built app at `app/build/Aura.app` with computer use (or `scripts/start-aura-session-review.sh`). If no current build exists, build it with `scripts/build-app.sh` first. If the computer-use tools are not available in this session (they come from the Claude desktop app), stop and tell the user; do not reach Aura another way (in particular, never send `/new` from a second `uv run aura` process: it deletes the app's saved conversation). Never use `screencapture`.
2. Ask Aura for one bounded, high-value improvement. Require a dossier with intent, constraints, acceptance evidence, risks, and long-term implications. Route the request through only the applicable phase skills above; Aura must not modify the root project. Aura's file tools see `~/Documents/Aura_Vault/Aura`, which can be a stale copy of this repo, so paste the relevant current facts (file excerpts, recent commits) into the request instead of assuming Aura can read them.
3. Read Aura's entire response. Reject or narrow suggestions that add authority, bypass the Security Governor, require ungranted external access, or are too broad for one review loop.

## Implement

1. Inspect the affected code and any applicable tests before editing.
2. State the refined scope to the user, then implement the smallest coherent change with the Edit and Write tools.
3. Verify in proportion to risk. Use focused tests first; run the app build for native UI or packaging changes. Put pytest temporary state in a named `/private/tmp/aura-pytest-*` directory and remove that exact directory after completion.
4. Apply the circuit breaker: after three consecutive attempts that produce the same no-progress state or error, stop automation, report the command and evidence, and ask the user for direction.

## Aura review and handoff

1. Send Aura the implementation facts: changed behavior, authority boundaries, diff scope, tests/build output, and any known limitations. Ask for an adversarial post-implementation review; Aura must not edit files.
2. Read the full review. Fix only a concrete defect within the agreed scope. Otherwise record Aura's residual risks as a possible next bounded task; do not expand the current change merely because a future improvement exists.
3. If the user has authorized autonomous Git publishing in the current conversation, stage only files belonging to this change, run `git diff --cached --check`, commit, and push. Preserve unrelated dirty files and do not stage them.

## Completion standard

End the loop only after Aura has reviewed the tested result, the application has been rebuilt when relevant, and the user receives the commit, verification evidence, and any residual risk. A manual Refresh control or other clearly disclosed limitation is valid evidence; do not claim live synchronization that has not been implemented.
