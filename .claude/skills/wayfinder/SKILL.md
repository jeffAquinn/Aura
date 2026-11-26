---
name: wayfinder
description: Turn a large or long-running Aura objective (a sprint, a multi-session feature) into a small decision map that keeps uncertainty and blocking relationships visible across sessions. Use proactively when a goal is too big or too vague for one spec, before grill-with-docs or to-tickets.
---

# Wayfinder

Claude Code version of Aura's `wayfinder` skill. It produces a decision map, not an implementation plan that pretends the uncertainty is resolved.

## Decision tickets

Fill these in from the user's objective and the repo's docs (`README.md`, the latest `docs/sprint-*-design.md`, memory). Where the docs already settle a decision, mark it resolved and cite the source.

| ID | Decision | Question | Blocked by |
|---|---|---|---|
| D1 | Outcome | What observable result marks this objective complete? | — |
| D2 | Boundary | Which parts of the project, vault areas, systems, and users are in scope? | D1 |
| D3 | Constraints | What must not change: data, cost, privacy, security floors, compatibility, time? | D1, D2 |
| D4 | Approach | Which viable approach best fits the confirmed constraints? | D2, D3 |
| D5 | Evidence | What proof must exist before the work is accepted (tests, live checks, Gemma's review)? | D4 |

Add decisions specific to the objective (D6, D7…) with their real blocking edges.

## Operating rule

- Resolve one decision at a time with the user. Record the answer and its reason. Status is `open`, `resolved (<reason>)`, or `deferred`.
- Unresolved decisions are not permission to guess.
- Architecture or security decisions go to Gemma. Sprint order is the user's call.
- When the map spans sessions, offer to save it as a project memory, or as a design doc in `docs/` if the user wants it versioned.
- Once D1–D5 are resolved, continue with `grill-with-docs` or `to-tickets`.
