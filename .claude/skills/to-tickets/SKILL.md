---
name: to-tickets
description: Break an approved Aura plan or spec into small, reviewable implementation tickets with acceptance checks and explicit (not guessed) blocking edges. Use proactively once a spec is approved and the work spans more than about three files or several steps, before implementing.
---

# To tickets

Claude Code version of Aura's `to_tickets` skill. The tickets are drafts for the user to confirm, not an authoritative work graph.

## Steps

1. Read the plan and the code areas it touches, so that each ticket names real files and seams.
2. Split the plan into vertical slices: each one leaves the app working and is verifiable on its own. Ten tickets at most. If the plan needs more, propose phases.
3. For each ticket, add a blocking edge only when the ticket truly needs another ticket's interface or artifact. Don't chain tickets just because of their order in the plan. Mark any edge you're unsure about as "confirm".
4. Present the list and ask the user to confirm the edges and the order.

## Ticket format

```
### T<n> — <imperative title>
- Scope: <files / modules>
- Blocked by: none | T<k> (<why>) | confirm
- Acceptance: <one observable outcome> — verified by <test / command / UI check>
- Non-goal: <the tempting adjacent cleanup it must not absorb>
```

If a ticket changes security boundaries, approvals, or architecture, note "needs Aura review". Once the tickets are confirmed, use TaskCreate to track them in this session if that tool is available, and then `implement-spec`.
