# Pillar 2: Mechanics

Status: **approved by Aura (architect), 2026-11-26.** Implementation in progress.

Aura's notes for implementation: update the four places that list the sections as one change, so they can't drift apart (a test enforces it); state an undeclared-files finding as a plain fact, leaving the decision to the user; and run the live gemma4 check.

Aura 2.0's second pillar (`docs/aura-2-plan.md`, order D9: Registry, Mechanics, Observer). Aura keeps the principle "understand the mechanics before building" and enforces it in code, not prose.

## 1. Outcome

A Technical Specification for a Developer build must have a **Mechanics** section. The section states what the skill relies on, in five fixed lines. Aura refuses a specification without a valid Mechanics section before anything leaves the Mac. When the Security Governor audits the built skill, it compares the code with those lines, and anything the code does that wasn't declared appears on the audit card.

## 2. Scope

In scope: Developer builds (`develop_skill`, and the Specification Approval card).

Not in scope:
- Skills proposed from the Skills Inbox (`propose_skill`). Their audit is unchanged.
- The skill format (`skill.toml`, `skill.py`).
- Installed skills. Nothing is re-audited.
- New checks in the static scan itself. The comparison uses what the scan and `skill.toml` already report.

## 3. The section

```
## Mechanics
- Network: none
- Processes: none
- Native code: none
- Dynamic code: none
- Files: reads the vault file given as `path`; writes a chart to /tmp
```

- **Network, Processes, Native code, Dynamic code** must each say `none`. Anything the code does in these four areas is already a red finding (`governor/scan.py`), and red blocks a skill with no override (Sprint 5). So a specification that claims any of them is refused at once, with that reason, and no build that would be blocked runs.
- **Files** says which of the skill's places it uses: the vault (read-only, `/vault`), the scratch folder (`/tmp`), or `none`. Each comes with a few words on why.
- The section must also stay within the existing length limits. Wording around the five lines is allowed; the five labels are what Aura checks.

## 4. The checks

**Before a build** (`check_specification`, which runs before anything leaves the Mac): the section exists, all five lines are present, the first four say `none`, and Files is not empty. The refusal names the missing or wrong line.

**In the audit** (Developer builds only, read from the specification already stored on the audit task as `origin.specification`). Each mismatch is a **yellow** finding (the user's choice, 2026-11-26), shown on the audit card for the user to judge:
- `skill.toml` asks for `vault = "read"`, but Files doesn't mention the vault.
- The code names a path under `/tmp`, but Files doesn't mention `/tmp`.
- The code names a path outside `/vault`, `/tmp` and `/skill`. That's already yellow today; the finding now adds "not declared in Mechanics".

These are yellow because the scan infers file use from path strings in the code: it's a guide, not proof, and red would block a sound skill with no override.

## 5. Where the section list changes together

`SECTIONS` (`developer/workspace.py`), Aura's prompt (`persona.py`), the `develop_skill` tool description (`tools/workshop.py`), and the app's specification template (`Developer.swift`). Together they gain a Mechanics section and a filled-in example. The Developer engine (Claude Code or Codex) already receives the whole specification, so it sees the declared mechanics with no extra step.

## 6. Non-goals and unchanged

- The Integrity Rule, approval cards defaulting to Deny, R1/R2/R5, and the Egress Gatekeeper are unchanged. No floor or default changes.
- No change to what blocks a skill: today's red rules stay the only blockers.

## 7. Acceptance evidence

- Offline tests:
  - A specification without Mechanics, with a missing line, or with Network not `none` is refused, and the refusal names the line.
  - A valid specification is accepted.
  - The audit of a Developer build adds a yellow finding for undeclared vault or `/tmp` use, and none when everything is declared.
  - Inbox audits are unchanged.
- App: the specification template shows Mechanics, and the build and Swift tests pass.
- Live, with the user's go-ahead: gemma4 drafts a specification from a real request, and it passes the new check.

## 8. Glossary addition

**Mechanics:** a Technical Specification's declaration of what a skill relies on (network, processes, native code, dynamic code, files). It is checked before a build starts and compared with the audit after it.

## 9. As built (2026-11-26)

- **The check:** `workspace.check_specification` and the parser `workspace.mechanics()`.
  - A section starts at a heading, or at its name alone on a line (gemma4 writes `Mechanics:`).
  - "none" means just `none`, with at most a full stop or a short note in brackets. "none, except it fetches…" is refused.
  - The fixed "…: none" lines don't count towards the 150-character minimum, so a blank template stays too short.
- **The app** (`Developer.swift`, `Specification.problems`) applies the same Mechanics checks as the server, so the card won't accept what the build then refuses.
- **One list, four places:** `tests/test_mechanics.py` fails if the server, Aura's prompt, the `develop_skill` description and the app list different sections.
- **The audit:** the static scan (`scan.py`) now records each absolute path and `tempfile` import it sees, with its location (`FileUse`). `governor/mechanics.py` compares those with the Files line.
  - A path is "named" only as a whole: `/etc` isn't named by `/etc/hosts`.
  - The skill's own tests (`test_*.py`) don't count.
  - Findings are yellow, or a pass reading "the static scan found no file use beyond Mechanics' Files line". Paths built while the skill runs aren't seen.
- **A retry** of a build interrupted before Mechanics existed is checked as it was approved, and its audit notes the missing Files line.
- **Reviews:** `simplify`, `code-review` (medium) and `security-review` (nothing at the bar). The fixes are above.
- **The live gemma4 check passed:** a real drafted specification met the check.
