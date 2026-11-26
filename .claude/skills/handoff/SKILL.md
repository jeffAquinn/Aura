---
name: handoff
description: Write a concise, redacted handoff of the current Claude Code session for the next session, Codex, or Aura (including Aura's architect review), and optionally save it as a project memory save point. Use when the user asks for a handoff, save point or review packet, and offer it proactively at the end of a long session or when work stops unfinished.
---

# Handoff

Claude Code version of Aura's `handoff` skill. Build the handoff only from facts established in this session, and verify each one (git, test output, files) rather than recalling it.

## Gather

- `git status`, `git log --oneline origin/main..HEAD`, and the current branch. Note what's committed, pushed, tagged, and still dirty.
- What was decided, attempted, or left unresolved, and the evidence for each (commands run and their results).
- What still waits on someone else: Aura's architect review, the user's push, a key or token the user must supply.

## Redact

Never include credentials. Replace anything shaped like a key or token with `[redacted]`: `sk-…`, `sk-ant-…`, `tvly-…`, `xoxb-`/`xoxp-`/`xapp-…`, `ghp_`/`gho_`/`github_pat_…`, Discord `a.b.c` bot tokens, `Bearer …`, OAuth tokens, and any value from the Keychain. Leave out the user's personal email address too. When in doubt, describe the secret ("the Slack bot token in the Keychain") instead of quoting it.

## Output

```
# Session Handoff — YYYY-MM-DD
## Current state      (bullets; commit hashes; pushed or not)
## Next focus         (the next smallest safe step)
## Durable artifacts  (paths, commits, docs)
## Open questions     (what's unverified; what needs approval)
## Suggested next move
```

## Deliver

- If the handoff is Aura's architect review, write it as a review packet mapped to Aura's checkpoints and offer to copy it with `pbcopy`. Gemma only sees what the user pastes.
- If the user wants a save point, write or update a `project` memory (`save-point-YYYY-MM-DD.md`) and its line in `MEMORY.md`. Update an existing save point rather than duplicating one.
- Otherwise, print it in the terminal.
