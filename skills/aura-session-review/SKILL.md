---
name: aura-session-review
description: Start Aura and run a bounded plan-review-implementation loop for one Aura-root-project improvement. Use at the start of a coding session when Aura should plan and review while Codex implements.
---

# Aura session review

Use Aura as the product architect and adversarial reviewer. Codex remains the lead developer: inspect the repository, decide the safe implementation, edit root-project code, run verification, and report concrete evidence.

## Start

1. Launch the built app at `app/build/Aura.app` with computer use. If no current build exists, build it with `scripts/build-app.sh` first.
2. Ask Aura for one bounded, high-value improvement. Require a dossier with intent, constraints, acceptance evidence, risks, and long-term implications. Aura must not modify the root project.
3. Read Aura's entire response. Reject or narrow suggestions that add authority, bypass the Security Governor, require ungranted external access, or are too broad for one review loop.

## Implement

1. Inspect the affected code and any applicable tests before editing.
2. State the refined scope in commentary, then implement the smallest coherent change with `apply_patch`.
3. Verify in proportion to risk. Use focused tests first; run the app build for native UI or packaging changes. Put pytest temporary state in a named `/private/tmp/aura-pytest-*` directory and remove that exact directory after completion.
4. Apply the circuit breaker: after three consecutive attempts that produce the same no-progress state or error, stop automation, report the command and evidence, and ask the user for direction.

## Aura review and handoff

1. Send Aura the implementation facts: changed behavior, authority boundaries, diff scope, tests/build output, and any known limitations. Ask for an adversarial post-implementation review; Aura must not edit files.
2. Read the full review. Fix only a concrete defect within the agreed scope. Otherwise record Aura's residual risks as a possible next bounded task; do not expand the current change merely because a future improvement exists.
3. If the user has authorized autonomous Git publishing in the current conversation, stage only files belonging to this change, run `git diff --cached --check`, commit, and push. Preserve unrelated dirty files and do not stage them.

## Completion standard

End the loop only after Aura has reviewed the tested result, the application has been rebuilt when relevant, and the user receives the commit, verification evidence, and any residual risk. A manual Refresh control or other clearly disclosed limitation is valid evidence; do not claim live synchronization that has not been implemented.
