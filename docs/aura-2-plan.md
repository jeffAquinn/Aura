# Aura 2.0: Registry, Mechanics, Observer (decision map)

**Status:** planning. The architect (Aura) approved the direction on 2026-11-26. Each pillar gets its own spec before any code.

## Why

Aura proposed three pillars, each modelled on a public repository. A review against the code and the repositories changed all three:

- **public-apis** ([repo](https://github.com/public-apis/public-apis)) is a hand-maintained table of free third-party APIs. Its Auth, HTTPS and CORS columns are entered by hand and are hints, not guarantees. For Aura, each entry would be a place to send data, which the Egress Gatekeeper controls. What Aura borrows is the format: a fixed schema per entry with a contribution checklist, applied to Aura's own tools.
- **rea** ([repo](https://github.com/morluto/rea)) is an MCP that connects coding agents to Ghidra, IDA and Hopper for reverse engineering. It doesn't monitor systems. Installing it would add an npm MCP with broad host access, and lookalike forks exist. It isn't adopted (D8).
- **build-your-own-x** ([repo](https://github.com/codecrafters-io/build-your-own-x)) is a list of tutorials. Aura keeps its principle: understand the mechanics before building. Aura enforces it in code, not in prose.

Two findings in the code shaped the plan:

- No `Context_Provider` exists. Tool selection is a regex table, `_TOOL_FAMILIES` in `brain.py`, and a request that matches no pattern gets no tools at all. That causes a measured failure: `tests/test_voice.py::test_aura_s_own_neural_voices_are_listed_even_without_the_app` fails on `main` with "try one of []".
- A tool's facts are spread over five tables: `GROUP`, `_DOING` and `NEEDS_APPROVAL` in `tools/__init__.py`, `GATED` in `tools/egress.py`, and `_TOOL_FAMILIES` in `brain.py`.

## Status

- **Pillar 1, Registry: built, not committed (2026-11-26).** Tickets T1 to T5 and T7 are done. T6, the live gemma4 evaluation, reached 70% against the 85% bar; Aura accepted 70% as the baseline and kept the bar (see below). Aura's architect review comes before tagging. One change from the spec: `Capabilities.md` is at the vault root, not `Aura/Capabilities.md`, because `Aura/` in the vault is the editable copy of Aura's source (the user's choice).
- **Pillars 2 and 3:** not started.

## The pillars, in order

1. **Registry.** One `Capability` table in code: tool, group, status line, selection cues, approval floor, egress class (none, local, internet) and owner (core, skill, connection). It drives tool selection, approval wiring and the egress gate. The vault gets a read-only rendering at `Capabilities.md`, at its root. Skills declare their entry in `skill.toml`, and the Governor checks it.
2. **Mechanics.** A required "Mechanics" section in skill specifications (`SECTIONS` in `developer/workspace.py`): the system calls, files, network use and containers the skill relies on. The Workshop rejects a specification without one, and checks it against what the audit observes.
3. **Observer.** Aura reports on its own activity: the containers it starts (resources, duration, network mode), turns and approvals (`state.sqlite`), vault changes made during a task, and egress grants. It's shown in the app and in `aura-session-review` reports.

## Decisions

| ID | Decision | Status |
|---|---|---|
| D1 | Outcome | Resolved (Aura): the Registry drives tool selection; the Observer panel reports Aura's activity; the Mechanics gate rejects incomplete specifications. |
| D2 | Registry boundary | Resolved (Pillar 1 spec, 2026-11-26): every core, connection and skill tool gets a row. Skill rows are derived from `skill.toml`, so no skill files change. Each row carries its floor and default setting for the later Permissions screen. No Swift changes. |
| D3 | Constraints | Resolved: the Integrity Rule, approval cards defaulting to Deny, R1/R2/R5, the Egress Gatekeeper, no floor or default changes, and the prompt saving for local models kept. |
| D4 | Approach | Resolved: the three pillars above. |
| D5 | Evidence | Resolved for Pillar 1: about 60 recorded prompts offline, derived approval and egress sets equal to today's, and a live gemma4 run on about 20 unmatched prompts that must load the right family in at least 85%. Then Aura's architect review. Pillars 2 and 3 define their own. |
| D6 | Registry source of truth | Resolved (Aura): code. A vault file could be changed by one approved `write_file` and alter Aura's security posture. |
| D7 | Observer scope | Resolved (Aura): Aura's own activity only. Watching the wider Mac needs a separate, audited design. |
| D8 | Adopt rea or other outside MCP servers | Deferred: depends on Sprint 7's MCP-in-Workshop work and R3 pinning. |
| D9 | Order | Resolved (Aura's ruling, relayed by the user): Registry, Mechanics, Observer, all before Egress Phase 2 (page fetch). |

## T6: live evaluation (gemma4:26b, 2026-11-26)

20 prompts that need a tool but contain no cue; every approval card denied. The bar is the right family's tool called in at least 85% of them.

| Run | Result | Change before the run |
|---|---|---|
| 1 | 11/20 (55%) | none |
| 2 | 14/20 (70%) | `find_capabilities` lists every family in its description and accepts close names ("Vault" means Files, "Web search" means Web) |

The prompts and the scoring were not changed between runs. The six misses in run 2: three questions the model answered from its system prompt without a tool (persona settings, installed skills, agents); one clarifying question before a Developer handoff; two voice requests read as persona changes. **The bar is not met.** Aura's ruling (2026-11-26): accept 70% as the baseline, keep the 85% bar, and reach it by refinement in a later iteration (the `find_capabilities` description, and the system prompt's line between what Aura already knows and what needs a tool), not by lowering the bar.

## Ungated egress the Registry recorded

Filling in the table showed two tools that send data off the Mac with no approval card. Both are recorded with `egress = "internet"` and a note that `Capabilities.md` shows. Their floors are unchanged, as Aura ruled on 2026-11-26.

- `check_library` sends a package name to PyPI and OSV.
- `propose_skill` starts an audit that looks up the skill's dependencies on PyPI and OSV and builds its image from PyPI.

## Terms

- **Capability:** one tool, one row in the Registry table.
- **Family:** a user-facing group of capabilities (Files, Knowledge, Web, Code, Voice, Persona, Workshop, Developer, Google, Slack, Discord, Connections, Skills). `find_capabilities` lists and loads families, and the Permissions screen will show them. The status-line label (`Web_Search_Tool`) is separate.
- **Cue:** a word stem that selects a capability for a turn (`voice` matches "voices").

## Review

Aura is now the project architect, and it is also the system these pillars change. Changes to approvals, the egress gate or the Governor therefore need the user's explicit approval at commit, as well as Aura's review.
