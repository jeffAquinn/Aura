---
name: retro
description: Bounded retrospective on a Claude Code session in the Aura repo: ranked improvement candidates (tests, checks, pointers, memories), applied only after approval. Use when the user asks for a retro or lessons learned, and offer it proactively after a session with repeated failures, corrections from the user, or a hard-won fix.
---

# Retro

Claude Code version of Aura's `retro` skill. It suggests candidates. It doesn't change instructions, memories, tests, or checks until the user approves.

## 1. Collect evidence

Go through the session for what was slow, confusing, failed, repeated, or needed manual correction by the user. Quote the concrete moment: the command, the error, or the user's words. Don't infer frustration that wasn't expressed.

## 2. Classify each problem

| Kind | Typical signal | Preferred remedy |
|---|---|---|
| Navigation | Searching for where something lives | A one-line pointer in a doc or memory to the authoritative module; don't duplicate its content |
| Automated check | A bug, regression, or failed test that slipped through | A concrete test, type check, or lint rule that catches it deterministically |
| Control flow | The same action retried, a loop, getting stuck | A recorded stop condition and fallback path |
| Context economy | The same context rediscovered, large reads | A compact reference, handoff, or memory |
| Observation | Nothing above fits | Capture one concrete failure mode before adding any rule |

## 3. Rank and present (at most five)

For each candidate:

```
### High|Medium|Low — <kind>
- Evidence: <what happened>
- Candidate: <the specific change>
- Prevents: <the exact future failure>
- Where: <test file / memory file / skill / README>
```

Rule: prefer a deterministic check over a prose rule, and a short pointer over duplicated guidance.

## 4. Apply only what's approved

Implement only the candidates the user approves. Feedback memories go in the memory directory with **Why:** and **How to apply:** lines. Update an existing memory rather than creating a duplicate.
